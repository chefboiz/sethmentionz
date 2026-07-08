import logging
from datetime import datetime, timezone

import httpx

import db
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from telegram import queue_state

log = logging.getLogger(__name__)

REALERT_THRESHOLD = 0.02


# ── DB helpers ────────────────────────────────────────────────────────────────

_OPP_COLUMNS = """
    mo.market_id, mo.blended_confidence, mo.edge_pct, mo.best_ask,
    mo.max_size_usd, mo.total_depth_usd, mo.liquidity_flag, mo.status,
    mm.question, mm.subject, mm.context, mm.phrase_topic,
    mm.resolution_deadline, mm.resolution_criteria_summary,
    mm.description, mm.clob_token_ids
"""


def get_open_opportunity() -> dict | None:
    """Return the currently open (awaiting reply) opportunity, or None.
    Open = status 'pending' + alerted TRUE (alert was sent, reply not yet received).
    Uses the queue_state market_id when available so the result is always the
    specific market that was last alerted, not an arbitrary pending row."""
    mid = queue_state.get()
    if mid:
        return get_opportunity_by_id(mid)
    # Fallback for startup recovery (queue_state cleared by process restart)
    row = db.fetchone(f"""
        SELECT {_OPP_COLUMNS}
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'pending' AND mo.alerted = TRUE
        ORDER BY mo.qualified_at ASC
        LIMIT 1
    """)
    if row:
        log.warning('queue_state recovered from DB: %s', row['market_id'][:14])
        queue_state.set_open(row['market_id'])
    return row


def get_opportunity_by_id(market_id: str) -> dict | None:
    """Fetch a specific opportunity by market_id regardless of status."""
    return db.fetchone(f"""
        SELECT {_OPP_COLUMNS}
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.market_id = %s
    """, (market_id,))


def _get_next_queued() -> dict | None:
    """Next pending+unalerted opportunity ordered by when it qualified."""
    return db.fetchone(f"""
        SELECT {_OPP_COLUMNS}
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'pending' AND mo.alerted = FALSE
        ORDER BY mo.qualified_at ASC
        LIMIT 1
    """)


def skip_opp(market_id: str) -> None:
    db.execute("""
        UPDATE mention_opportunities SET status = 'skipped' WHERE market_id = %s
    """, (market_id,))


# ── Formatting helpers ────────────────────────────────────────────────────────

def _hours_until(iso) -> str:
    if not iso:
        return '?'
    try:
        dt = iso if isinstance(iso, datetime) else \
            datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = int((dt - datetime.now(timezone.utc)).total_seconds() / 60)
        if mins < 0:
            return 'past'
        h, m = divmod(mins, 60)
        return f'{h}h {m}m' if m else f'{h}h'
    except Exception:
        return str(iso)[:10]


def resolve_dt_str(iso) -> str:
    if not iso:
        return '?'
    try:
        dt = iso if isinstance(iso, datetime) else \
            datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"{dt.strftime('%b')} {dt.day}, {dt.year} at {dt.strftime('%H:%M')} UTC"
    except Exception:
        return str(iso)[:16]


def format_alert(opp: dict, refreshed: bool = False) -> str:
    q       = opp.get('question') or '—'
    subject = opp.get('subject') or '—'
    ctx     = opp.get('context') or '—'
    what    = opp.get('phrase_topic') or '—'
    ask     = float(opp.get('best_ask') or 0)
    conf    = float(opp.get('blended_confidence') or 0)
    until   = _hours_until(opp.get('resolution_deadline'))
    thin    = opp.get('liquidity_flag', False)
    depth   = float(opp.get('total_depth_usd') or 0)
    suffix  = ' (refreshed)' if refreshed else ''

    lines = [
        f'🎯 Trade Found — 🟢 YES · "{q}"{suffix}',
        '',
        f'👤 Who: {subject}',
        f'📍 Where: {ctx}',
        f'📝 What: {what}',
        '',
        f'💰 Price: ${ask:.2f}',
        f'🎯 Confidence: {conf*100:.0f}%',
        f'⏱ Resolves: {until}',
    ]
    if thin:
        lines.append(f'⚠️  Thin book — ${depth:.0f} total depth')
    lines += ['', 'Reply: y [amount] / n / w / d / r']
    return '\n'.join(lines)


# ── Send helpers ──────────────────────────────────────────────────────────────

def send_message(text: str) -> None:
    """Send a plain-text message to the configured chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        with httpx.Client(timeout=8) as c:
            c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT_ID, 'text': text},
            )
    except Exception as e:
        log.error('sendMessage error: %s', e)


def send_alert(opp: dict) -> int | None:
    """Send the formatted alert message. Returns the Telegram message_id or None."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning('Telegram not configured — skipping alert')
        return None
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': TELEGRAM_CHAT_ID, 'text': format_alert(opp)},
            )
            r.raise_for_status()
            msg_id = r.json()['result']['message_id']
            log.info('Alert sent: %s → msg_id=%s', opp['market_id'][:14], msg_id)
            return msg_id
    except Exception as e:
        log.error('Alert send failed for %s: %s', opp['market_id'][:14], e)
        return None


# ── Queue management ──────────────────────────────────────────────────────────

def _open_opp(opp: dict) -> None:
    mid    = opp['market_id']
    msg_id = send_alert(opp)
    db.execute("""
        UPDATE mention_opportunities SET
            alerted            = TRUE,
            alerted_edge_pct   = %(edge_pct)s,
            alerted_confidence = %(blended_confidence)s,
            tg_message_id      = %(tg_message_id)s
        WHERE market_id = %(market_id)s
    """, {
        'edge_pct':           opp.get('edge_pct'),
        'blended_confidence': opp.get('blended_confidence'),
        'tg_message_id':      msg_id,
        'market_id':          mid,
    })
    queue_state.set_open(mid)
    log.info('Opened: %s', mid[:14])


def advance_queue() -> None:
    """Open the next queued opportunity (if any). Callers must have already
    cleared the current open slot (status set to approved/skipped/expired)."""
    nxt = _get_next_queued()
    if nxt:
        _open_opp(nxt)
    else:
        queue_state.set_open(None)
        log.info('Queue empty — nothing to advance to')


# ── APScheduler job ───────────────────────────────────────────────────────────

def run_alert_check() -> None:
    # 1. Notify about any open opportunity that price_refresh already expired.
    #    Detect via: status='expired' AND alerted=TRUE AND tg_message_id IS NOT NULL.
    #    Clear tg_message_id after notifying so we don't repeat the message.
    expired_open = db.fetchone("""
        SELECT mo.market_id, mm.question
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.status = 'expired' AND mo.alerted = TRUE AND mo.tg_message_id IS NOT NULL
        LIMIT 1
    """)
    if expired_open:
        q = (expired_open.get('question') or expired_open['market_id'])[:60]
        log.info('Auto-skip notification: %s', expired_open['market_id'][:14])
        send_message(f'⏭ Auto-skipped "{q}" — edge closed or price ceiling hit')
        db.execute("""
            UPDATE mention_opportunities SET tg_message_id = NULL
            WHERE market_id = %s
        """, (expired_open['market_id'],))
        # Clear queue_state if this was the tracked open slot
        if queue_state.get() == expired_open['market_id']:
            queue_state.set_open(None)

    # 2. If something is still open and pending, wait for the user's reply.
    if get_open_opportunity():
        return

    # 3. Nothing open — open the next in queue (pending+alerted=FALSE, oldest first).
    nxt = _get_next_queued()
    if nxt:
        _open_opp(nxt)
