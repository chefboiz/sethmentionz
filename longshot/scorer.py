import logging
from datetime import datetime, timezone

import db
from config import (
    RESOLUTION_WINDOW_HOURS,
    LONGSHOT_MIN_DEPTH_USD,
    LONGSHOT_MAX_PRICE,
    MOMENTUM_WEIGHT,
    VOLUME_WEIGHT,
    TIME_WEIGHT,
)
from edge.clob_client import fetch_book, fetch_price

log = logging.getLogger(__name__)


def _depth_usd(book: dict) -> float:
    return sum(lvl['price'] * lvl['size'] for lvl in book.get('asks', []))


def _snapshot_prices(market_id: str, yes_price, no_price, volume24hr) -> None:
    db.execute("""
        INSERT INTO mention_price_history (market_id, yes_price, no_price, volume24hr)
        VALUES (%s, %s, %s, %s)
    """, (market_id, yes_price, no_price, volume24hr))


def _price_24h_ago(market_id: str, cheap_side: str) -> float | None:
    """Nearest price snapshot to 24h ago (within +-2h window)."""
    row = db.fetchone("""
        SELECT yes_price, no_price FROM mention_price_history
        WHERE market_id = %s
          AND snapped_at BETWEEN NOW() - INTERVAL '26 hours'
                             AND NOW() - INTERVAL '22 hours'
        ORDER BY ABS(EXTRACT(EPOCH FROM (snapped_at - (NOW() - INTERVAL '24 hours'))))
        LIMIT 1
    """, (market_id,))
    if not row:
        return None
    col = 'yes_price' if cheap_side == 'yes' else 'no_price'
    val = row.get(col)
    return float(val) if val is not None else None


def _volume_7d_avg(market_id: str) -> float | None:
    """Average of all volume24hr snapshots taken in the last 7 days (>=2 required)."""
    rows = db.fetchall("""
        SELECT volume24hr FROM mention_price_history
        WHERE market_id = %s
          AND snapped_at > NOW() - INTERVAL '7 days'
          AND volume24hr IS NOT NULL
    """, (market_id,))
    if len(rows) < 2:
        return None
    return sum(float(r['volume24hr']) for r in rows) / len(rows)


def _momentum_score(current_price: float, old_price: float | None, mid: str) -> float:
    if old_price is None:
        log.debug('  %s: no 24h price history -- momentum neutral', mid[:14])
        return 0.5
    if old_price <= 0:
        return 0.5
    change = (current_price - old_price) / old_price
    return max(0.0, min(1.0, 0.5 + change * 0.5))


def _volume_score(volume_24h: float | None, vol_7d_avg: float | None) -> float:
    if not volume_24h or not vol_7d_avg or vol_7d_avg <= 0:
        return 0.5
    return max(0.0, min(1.0, volume_24h / (2.0 * vol_7d_avg)))


def _time_score(hours: float) -> float:
    if hours <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (hours / RESOLUTION_WINDOW_HOURS)))


def _hours_until(deadline) -> float | None:
    if not deadline:
        return None
    try:
        dt = deadline if isinstance(deadline, datetime) else \
            datetime.fromisoformat(str(deadline).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return max(0.0, delta)
    except Exception:
        return None


def _llm_score_market(m: dict) -> tuple[float | None, str]:
    """Call LLM for a probability estimate. Returns (probability, reasoning)."""
    from confidence.llm_leg import score as llm_score
    result = llm_score({
        'question':                    m.get('question', ''),
        'subject':                     m.get('subject') or 'Unknown',
        'phrase_topic':                m.get('phrase_topic') or 'Unknown',
        'context':                     m.get('context') or 'unknown',
        'resolution_criteria_summary': (m.get('resolution_criteria_summary') or '')[:400],
        'resolution_deadline':         m.get('resolution_deadline') or 'unknown',
    })
    return result.get('probability'), result.get('reasoning', '')


def run_longshot_score() -> None:
    log.info('=== Longshot scorer started ===')

    markets = db.fetchall("""
        SELECT market_id, question, subject, phrase_topic, context,
               resolution_criteria_summary, resolution_deadline,
               yes_price, no_price, clob_token_ids, volume24hr
        FROM mention_markets
        WHERE resolved = FALSE AND archived = FALSE
    """)

    scored = skipped_depth = skipped_price = skipped_sanity = 0

    for m in markets:
        mid        = m['market_id']
        yes_price  = float(m.get('yes_price') or 0)
        no_price   = float(m.get('no_price') or 0)
        volume24hr = float(m['volume24hr']) if m.get('volume24hr') is not None else None
        clob_ids   = m.get('clob_token_ids') or []

        # Identify cheap side using Gamma prices -- must be <= LONGSHOT_MAX_PRICE
        if 0 < yes_price <= LONGSHOT_MAX_PRICE:
            cheap_side, cheap_idx = 'yes', 0
        elif 0 < no_price <= LONGSHOT_MAX_PRICE:
            cheap_side, cheap_idx = 'no', 1
        else:
            skipped_price += 1
            continue

        expensive_idx = 1 - cheap_idx
        if cheap_idx >= len(clob_ids) or not clob_ids[cheap_idx]:
            continue

        # Fetch live prices via /price (not /book -- /book returns ghost prices)
        cheap_token     = clob_ids[cheap_idx]
        expensive_token = clob_ids[expensive_idx] if expensive_idx < len(clob_ids) else None

        live_cheap     = fetch_price(cheap_token, side='BUY')
        cheap_price    = live_cheap if live_cheap is not None else (yes_price if cheap_side == 'yes' else no_price)

        live_expensive  = fetch_price(expensive_token, side='BUY') if expensive_token else None
        expensive_price = live_expensive if live_expensive is not None else (no_price if cheap_side == 'yes' else yes_price)

        # Sanity check: cheap must actually be cheaper
        if cheap_price >= expensive_price:
            log.warning('  %s: ghost price -- cheap %.3f >= expensive %.3f, skip',
                        mid[:14], cheap_price, expensive_price)
            skipped_sanity += 1
            continue

        # Liquidity gate: fetch order book for depth check
        book = fetch_book(cheap_token)
        if not book:
            continue

        depth = _depth_usd(book)
        if depth < LONGSHOT_MIN_DEPTH_USD:
            skipped_depth += 1
            log.debug('  %s: depth $%.0f < gate -- skip', mid[:14], depth)
            continue

        # Snapshot for future momentum calculations (use Gamma prices for history)
        _snapshot_prices(mid, yes_price or None, no_price or None, volume24hr)

        hours = _hours_until(m.get('resolution_deadline'))
        if hours is None:
            continue

        old_price        = _price_24h_ago(mid, cheap_side)
        price_change_pct = None
        if old_price and old_price > 0:
            price_change_pct = (cheap_price - old_price) / old_price * 100

        mom_score = _momentum_score(cheap_price, old_price, mid)
        vol_7d    = _volume_7d_avg(mid)
        vol_score = _volume_score(volume24hr, vol_7d)
        t_score   = _time_score(hours)
        composite = (MOMENTUM_WEIGHT * mom_score +
                     VOLUME_WEIGHT   * vol_score +
                     TIME_WEIGHT     * t_score)

        # LLM scoring -- call once per market, preserve existing score on re-runs
        existing = db.fetchone(
            'SELECT llm_probability FROM mention_longshot_candidates WHERE market_id = %s',
            (mid,)
        )
        if existing and existing.get('llm_probability') is not None:
            llm_prob      = float(existing['llm_probability'])
            llm_reasoning = None  # preserve existing value in DB
        else:
            llm_prob, llm_reasoning = _llm_score_market(m)
            if llm_prob is not None:
                log.info('  %s LLM prob=%.2f', mid[:14], llm_prob)
            else:
                log.warning('  %s LLM scoring failed', mid[:14])

        db.execute("""
            INSERT INTO mention_longshot_candidates (
                market_id, cheap_side, cheap_price, expensive_price,
                price_change_24h_pct, volume_24h, volume_7d_avg,
                hours_to_resolution, momentum_score, volume_score, time_score,
                composite_score, subject, llm_probability, llm_reasoning, last_scored_at
            ) VALUES (
                %(market_id)s, %(cheap_side)s, %(cheap_price)s, %(expensive_price)s,
                %(price_change_24h_pct)s, %(volume_24h)s, %(volume_7d_avg)s,
                %(hours_to_resolution)s, %(momentum_score)s, %(volume_score)s, %(time_score)s,
                %(composite_score)s, %(subject)s, %(llm_probability)s, %(llm_reasoning)s, NOW()
            )
            ON CONFLICT (market_id) DO UPDATE SET
                cheap_side             = EXCLUDED.cheap_side,
                cheap_price            = EXCLUDED.cheap_price,
                expensive_price        = EXCLUDED.expensive_price,
                price_change_24h_pct   = EXCLUDED.price_change_24h_pct,
                volume_24h             = EXCLUDED.volume_24h,
                volume_7d_avg          = EXCLUDED.volume_7d_avg,
                hours_to_resolution    = EXCLUDED.hours_to_resolution,
                momentum_score         = EXCLUDED.momentum_score,
                volume_score           = EXCLUDED.volume_score,
                time_score             = EXCLUDED.time_score,
                composite_score        = EXCLUDED.composite_score,
                subject                = EXCLUDED.subject,
                llm_probability        = COALESCE(mention_longshot_candidates.llm_probability, EXCLUDED.llm_probability),
                llm_reasoning          = COALESCE(mention_longshot_candidates.llm_reasoning, EXCLUDED.llm_reasoning),
                last_scored_at         = NOW()
        """, {
            'market_id':            mid,
            'cheap_side':           cheap_side,
            'cheap_price':          cheap_price,
            'expensive_price':      expensive_price,
            'price_change_24h_pct': price_change_pct,
            'volume_24h':           volume24hr,
            'volume_7d_avg':        vol_7d,
            'hours_to_resolution':  round(hours, 2),
            'momentum_score':       round(mom_score, 4),
            'volume_score':         round(vol_score, 4),
            'time_score':           round(t_score, 4),
            'composite_score':      round(composite, 4),
            'subject':              m.get('subject'),
            'llm_probability':      llm_prob,
            'llm_reasoning':        llm_reasoning,
        })
        scored += 1
        log.info('  %s | %s @%.2f  comp=%.3f  llm=%s',
                 mid[:14], cheap_side.upper(), cheap_price, composite,
                 f'{llm_prob:.2f}' if llm_prob is not None else 'pending')

    log.info('=== Longshot score done | scored: %d  skipped_depth: %d  no_cheap_side: %d  ghost_price: %d ===',
             scored, skipped_depth, skipped_price, skipped_sanity)
