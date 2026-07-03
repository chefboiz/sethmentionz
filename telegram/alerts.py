import logging
from datetime import datetime, timezone

import httpx

import db
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

REALERT_THRESHOLD = 0.02   # re-alert when edge or confidence moves more than 2pp

# ── Callback registry ─────────────────────────────────────────────────────────
# Telegram callback_data is limited to 64 bytes.
# We map each market_id to a short int key and embed that in the button payload.

_counter  = 0
_fwd: dict[str, str] = {}   # short_id  -> market_id
_rev: dict[str, str] = {}   # market_id -> short_id


def register_market(market_id: str) -> str:
    global _counter
    if market_id in _rev:
        return _rev[market_id]
    _counter += 1
    key = str(_counter)
    _fwd[key]       = market_id
    _rev[market_id] = key
    return key


def resolve_market(short_id: str) -> str | None:
    return _fwd.get(short_id)


# ── Alert formatting ──────────────────────────────────────────────────────────

def _deadline_str(iso: str | None) -> str:
    if not iso:
        return '?'
    try:
        dt   = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        mins = int((dt - datetime.now(timezone.utc)).total_seconds() / 60)
        if mins < 0:
            return 'past deadline'
        h, m = divmod(mins, 60)
        return f'{h}h {m}m' if h else f'{m}m'
    except ValueError:
        return str(iso)[:16]


def _build_text(opp: dict, market: dict) -> str:
    q       = market.get('question', '—')
    subject = market.get('subject') or '—'
    ctx     = market.get('context') or '—'
    dl      = _deadline_str(market.get('resolution_deadline'))
    ask     = float(opp.get('best_ask') or 0)
    conf    = float(opp.get('blended_confidence') or 0)
    edge    = float(opp.get('edge_pct') or 0)
    size    = float(opp.get('max_size_usd') or 0)
    depth   = float(opp.get('total_depth_usd') or 0)
    thin    = opp.get('liquidity_flag', False)

    lines = [
        '<b>🎯 MENTION MARKET</b>',
        '',
        q,
        '',
        f'<b>Who:</b> {subject}  •  <b>Via:</b> {ctx}',
        f'<b>Ends in:</b> {dl}',
        '',
        f'YES @ <b>{ask:.2f}</b>  |  Conf <b>{conf*100:.0f}%</b>  |  Edge <b>+{edge*100:.1f}%</b>',
        f'<b>Suggested size:</b> ${size:.0f}',
    ]
    if thin:
        lines.append(f'⚠️ <i>Thin book — ${depth:.0f} total depth</i>')

    return '\n'.join(lines)


def send_alert(opp: dict, market: dict) -> int | None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning('Telegram not configured — skipping alert')
        return None

    mid      = opp['market_id']
    short_id = register_market(mid)

    payload = {
        'chat_id':    TELEGRAM_CHAT_ID,
        'text':       _build_text(opp, market),
        'parse_mode': 'HTML',
        'reply_markup': {
            'inline_keyboard': [
                [
                    {'text': '✅ Approve',         'callback_data': f'a:{short_id}'},
                    {'text': '❌ Skip',             'callback_data': f's:{short_id}'},
                ],
                [
                    {'text': '📉 Approve smaller', 'callback_data': f'sm:{short_id}'},
                ],
            ]
        },
    }

    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json=payload,
            )
            r.raise_for_status()
            msg_id = r.json()['result']['message_id']
            log.info('Alert sent: %s → msg_id=%s', mid[:14], msg_id)
            return msg_id
    except Exception as e:
        log.error('Alert send failed for %s: %s', mid[:14], e)
        return None


# ── APScheduler job ───────────────────────────────────────────────────────────

def run_alert_check() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. New unalerted opportunities
    new_opps = db.fetchall("""
        SELECT mo.*, mm.question, mm.subject, mm.context, mm.resolution_deadline
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'pending' AND mo.alerted = FALSE
    """)

    for row in new_opps:
        market = {k: row[k] for k in ('question', 'subject', 'context', 'resolution_deadline')}
        opp    = {k: v for k, v in row.items() if k not in market}
        mid    = row['market_id']
        msg_id = send_alert(opp, market)

        db.execute("""
            UPDATE mention_opportunities SET
                alerted            = TRUE,
                alerted_edge_pct   = %(edge_pct)s,
                alerted_confidence = %(blended_confidence)s,
                tg_message_id      = %(tg_message_id)s
            WHERE market_id = %(market_id)s
        """, {
            'edge_pct':           row.get('edge_pct'),
            'blended_confidence': row.get('blended_confidence'),
            'tg_message_id':      msg_id,
            'market_id':          mid,
        })

    # 2. Re-alert if edge or confidence has moved >2pp since last alert
    alerted = db.fetchall("""
        SELECT market_id, edge_pct, blended_confidence,
               alerted_edge_pct, alerted_confidence
        FROM mention_opportunities
        WHERE status = 'pending' AND alerted = TRUE
    """)

    for row in alerted:
        prev_edge = row.get('alerted_edge_pct')
        prev_conf = row.get('alerted_confidence')
        if prev_edge is None:
            continue

        edge_moved = abs(float(row['edge_pct']) - float(prev_edge)) > REALERT_THRESHOLD
        conf_moved = (
            prev_conf is not None
            and abs(float(row['blended_confidence']) - float(prev_conf)) > REALERT_THRESHOLD
        )

        if edge_moved or conf_moved:
            db.execute("""
                UPDATE mention_opportunities SET alerted = FALSE
                WHERE market_id = %s
            """, (row['market_id'],))
            log.info('Re-alert queued: %s (moved >2pp)', row['market_id'][:14])
