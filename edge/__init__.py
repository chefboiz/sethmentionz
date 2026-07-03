import logging
from datetime import datetime, timezone

import db
from config import CONFIDENCE_THRESHOLD
from edge.clob_client import fetch_book
from edge.calculator import compute

log = logging.getLogger(__name__)


def run_edge() -> None:
    log.info('=== Edge run started ===')
    now_iso = datetime.now(timezone.utc).isoformat()

    markets = db.fetchall("""
        SELECT mm.market_id, mm.question, mm.clob_token_ids,
               ms.blended_score
        FROM mention_markets mm
        JOIN LATERAL (
            SELECT blended_score
            FROM mention_signals
            WHERE market_id = mm.market_id
            ORDER BY scored_at DESC
            LIMIT 1
        ) ms ON TRUE
        WHERE mm.resolved = FALSE AND mm.archived = FALSE
          AND ms.blended_score >= %s
    """, (CONFIDENCE_THRESHOLD,))

    checked = qualified = 0

    for market in markets:
        mid      = market['market_id']
        clob_ids = market.get('clob_token_ids') or []
        if not clob_ids:
            continue

        blended = float(market['blended_score'])
        checked += 1

        book = fetch_book(clob_ids[0])
        if not book:
            log.warning('No CLOB book for %s', mid[:14])
            continue

        result = compute(book, blended)

        if result is None:
            db.execute("""
                UPDATE mention_opportunities
                SET status = 'expired', last_price_check_at = %s
                WHERE market_id = %s AND status = 'pending'
            """, (now_iso, mid))
            continue

        db.execute("""
            INSERT INTO mention_opportunities (
                market_id, blended_confidence, edge_pct, implied_probability,
                best_ask, max_size_usd, total_depth_usd, liquidity_flag,
                status, alerted, qualified_at, last_price_check_at
            ) VALUES (
                %(market_id)s, %(blended_confidence)s, %(edge_pct)s,
                %(implied_probability)s, %(best_ask)s, %(max_size_usd)s,
                %(total_depth_usd)s, %(liquidity_flag)s,
                'pending', FALSE, %(now)s, %(now)s
            )
            ON CONFLICT (market_id) DO UPDATE SET
                blended_confidence  = EXCLUDED.blended_confidence,
                edge_pct            = EXCLUDED.edge_pct,
                implied_probability = EXCLUDED.implied_probability,
                best_ask            = EXCLUDED.best_ask,
                max_size_usd        = EXCLUDED.max_size_usd,
                total_depth_usd     = EXCLUDED.total_depth_usd,
                liquidity_flag      = EXCLUDED.liquidity_flag,
                status              = 'pending',
                alerted             = FALSE,
                last_price_check_at = EXCLUDED.last_price_check_at
        """, {
            'market_id':           mid,
            'blended_confidence':  blended,
            'edge_pct':            result['edge_pct'],
            'implied_probability': result['implied_probability'],
            'best_ask':            result['best_ask'],
            'max_size_usd':        result['max_size_usd'],
            'total_depth_usd':     result['total_depth_usd'],
            'liquidity_flag':      result['liquidity_flag'],
            'now':                 now_iso,
        })

        qualified += 1
        thin = '  [THIN]' if result['liquidity_flag'] else ''
        log.info('  QUALIFIED: conf=%.3f  edge=+%.1f%%  size=$%.0f  ask=%.3f%s  — %s',
                 blended, result['edge_pct'] * 100, result['max_size_usd'],
                 result['best_ask'], thin, market['question'][:70])

    log.info('=== Edge complete | checked: %d  qualified: %d ===', checked, qualified)
    _print_summary()


def run_price_refresh() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = db.fetchall("""
        SELECT mo.market_id, mo.blended_confidence, mm.clob_token_ids, mm.question
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'pending'
    """)

    if not rows:
        return

    log.debug('Price refresh: %d pending', len(rows))
    expired = 0

    for opp in rows:
        mid      = opp['market_id']
        blended  = float(opp['blended_confidence'])
        clob_ids = opp.get('clob_token_ids') or []
        if not clob_ids:
            continue

        book = fetch_book(clob_ids[0])
        if not book:
            continue

        result = compute(book, blended)

        if result is None:
            db.execute("""
                UPDATE mention_opportunities
                SET status = 'expired', last_price_check_at = %s
                WHERE market_id = %s
            """, (now_iso, mid))
            expired += 1
            log.info('Price refresh: %s expired — edge closed', mid[:14])
        else:
            db.execute("""
                UPDATE mention_opportunities SET
                    edge_pct            = %(edge_pct)s,
                    implied_probability = %(implied_probability)s,
                    best_ask            = %(best_ask)s,
                    max_size_usd        = %(max_size_usd)s,
                    total_depth_usd     = %(total_depth_usd)s,
                    liquidity_flag      = %(liquidity_flag)s,
                    last_price_check_at = %(now)s
                WHERE market_id = %(market_id)s
            """, {**result, 'now': now_iso, 'market_id': mid})

    if expired:
        log.info('Price refresh: %d expired this cycle', expired)


def _print_summary() -> None:
    rows = db.fetchall("""
        SELECT mo.market_id, mo.blended_confidence, mo.edge_pct,
               mo.max_size_usd, mo.best_ask, mo.liquidity_flag, mm.question
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'pending'
        ORDER BY mo.edge_pct DESC
    """)

    if not rows:
        log.info('── No pending opportunities ──')
        return

    hdr = f"{'MARKET':<16}  {'CONF':>5}  {'EDGE':>6}  {'SIZE':>7}  {'ASK':>5}  {'FLAGS'}"
    log.info(hdr)
    log.info('─' * len(hdr))
    for r in rows:
        q    = (r.get('question') or '')[:55]
        flag = 'THIN' if r.get('liquidity_flag') else ''
        log.info('%-16s  %5.3f  %+5.1f%%  $%6.0f  %.3f  %s  %s',
                 r['market_id'][:16],
                 r['blended_confidence'],
                 r['edge_pct'] * 100,
                 r['max_size_usd'],
                 r['best_ask'],
                 flag, q)
