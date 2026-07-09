import logging
from datetime import datetime, timezone

import httpx

import db
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LONGSHOT_MIN_CONFIDENCE

log = logging.getLogger(__name__)

# Last alerted top-5 — used by callbacks.py to resolve `b {row}{c|e} {amount}`
_last_digest_rows: list[dict] = []


def get_digest_rows() -> list[dict]:
    return _last_digest_rows


def _send(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        with httpx.Client(timeout=10) as c:
            c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT_ID, 'text': text},
            ).raise_for_status()
    except Exception as e:
        log.error('Digest send error: %s', e)


def _subject_markers(rows: list[dict]) -> dict[str, str]:
    """Assign shared symbols to market_ids that share the same subject."""
    SYMBOLS = ['◆', '▲', '●', '■', '★']
    groups: dict[str, list[str]] = {}
    for r in rows:
        s = (r.get('subject') or '').strip().lower()
        if s:
            groups.setdefault(s, []).append(r['market_id'])
    markers: dict[str, str] = {}
    sym_idx = 0
    for mids in groups.values():
        if len(mids) > 1:
            sym = SYMBOLS[sym_idx % len(SYMBOLS)]
            for mid in mids:
                markers[mid] = sym
            sym_idx += 1
    return markers


def _format_digest(rows: list[dict]) -> str:
    markers = _subject_markers(rows)
    NUMS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']
    lines = ['📊 Longshot Scanner — Top 5', '']
    for i, r in enumerate(rows):
        num        = NUMS[i] if i < len(NUMS) else f'{i + 1}.'
        sym        = markers.get(r['market_id'], '')
        marker_str = f' {sym}' if sym else ''

        question    = (r.get('question') or r['market_id'])[:60]
        cheap_side  = r.get('cheap_side', 'yes').upper()
        cheap_price = float(r.get('cheap_price') or 0)
        exp_price   = float(r.get('expensive_price') or 0)
        pch         = r.get('price_change_24h_pct')
        pch_str     = f'{float(pch):+.1f}%' if pch is not None else 'n/a'
        vol24       = r.get('volume_24h')
        vol_str     = f'${float(vol24):,.0f}' if vol24 is not None else 'n/a'
        hours       = float(r.get('hours_to_resolution') or 0)
        comp        = float(r.get('composite_score') or 0) * 100

        llm_prob    = r.get('llm_probability')
        llm_str     = f'{float(llm_prob)*100:.0f}%' if llm_prob is not None else 'n/a'

        lines += [
            f'{num} "{question}"{marker_str}',
            f'   Cheap: {cheap_side} @ ${cheap_price:.2f} | Exp: ${exp_price:.2f}',
            f'   24h: {pch_str} | Vol: {vol_str} | Resolves: {hours:.0f}h',
            f'   Score: {comp:.0f}/100 | LLM: {llm_str}',
            '',
        ]

    lines.append('Reply: b {row}{c|e} {amount}  (e.g. "b 1c 10" = buy row 1 cheap, $10)')
    return '\n'.join(lines)


def _should_send(current_rows: list[dict]) -> bool:
    if not current_rows:
        return False
    if not _last_digest_rows:
        return True

    current_ids = {r['market_id'] for r in current_rows}
    last_ids    = {r['market_id'] for r in _last_digest_rows}
    if current_ids != last_ids:
        return True

    last_scores = {r['market_id']: float(r.get('composite_score') or 0)
                   for r in _last_digest_rows}
    for r in current_rows:
        mid  = r['market_id']
        cur  = float(r.get('composite_score') or 0)
        prev = last_scores.get(mid, 0.0)
        if prev > 0 and abs(cur - prev) / prev > 0.10:
            return True

    return False


def run_longshot_digest() -> None:
    global _last_digest_rows

    rows = db.fetchall("""
        SELECT lc.market_id, lc.cheap_side, lc.cheap_price, lc.expensive_price,
               lc.price_change_24h_pct, lc.volume_24h, lc.hours_to_resolution,
               lc.momentum_score, lc.volume_score, lc.time_score,
               lc.composite_score, lc.llm_probability, lc.subject,
               mm.question, mm.clob_token_ids
        FROM mention_longshot_candidates lc
        JOIN mention_markets mm ON mm.market_id = lc.market_id
        WHERE mm.resolved = FALSE AND mm.archived = FALSE
          AND lc.composite_score IS NOT NULL
          AND lc.llm_probability >= %(min_conf)s
        ORDER BY lc.composite_score DESC
        LIMIT 5""", {'min_conf': LONGSHOT_MIN_CONFIDENCE}
    """)

    if not rows:
        log.info('Longshot digest: no scored candidates')
        return

    if not _should_send(rows):
        log.info('Longshot digest: top-5 stable, skipping send')
        return

    _send(_format_digest(rows))

    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        db.execute("""
            UPDATE mention_longshot_candidates
            SET last_alerted_at = %s, alerted_composite_score = %s
            WHERE market_id = %s
        """, (now, r.get('composite_score'), r['market_id']))

    _last_digest_rows = list(rows)
    log.info('Longshot digest sent: %d candidates', len(rows))
