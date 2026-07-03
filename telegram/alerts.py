import logging
from datetime import datetime, timezone

import httpx

import db
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

REALERT_THRESHOLD = 0.02


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_open_opportunity() -> dict | None:
    """Return the currently open (awaiting reply) opportunity, or None."""
    return db.fetchone("""
        SELECT mo.market_id, mo.blended_confidence, mo.edge_pct, mo.best_ask,
               mo.max_size_usd, mo.total_depth_usd, mo.liquidity_flag, mo.status,
               mm.question, mm.subject, mm.context, mm.phrase_topic,
               mm.resolution_deadline, mm.resolution_criteria_summary,
               mm.description, mm.clob_token_ids
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.queue_status = 'open'
    """)


def _get_next_queued() -> dict | None:
    return db.fetchone("""
        SELECT mo.market_id, mo.blended_confidence, mo.edge_pct, mo.best_ask,
               mo.max_size_usd, mo.total_depth_usd, mo.liquidity_flag,
               mm.question, mm.subject, mm.context, mm.phrase_topic,
               mm.resolution_deadline, mm.clob_token_ids
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.queue_status = 'queued'
        ORDER BY mo.queued_at ASC
        LIMIT 1
    """)


def skip_opp(market_id: str) -> None:
    db.execute("""
        UPDATE mention_opportunities
        SET status = 'skipped', queue_status = NULL
        WHERE market_id = %s
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


def format_alert(opp: dict) -> str:
    q       = opp.get('question') or '—'
    subject = opp.get('subject') or '—'
    ctx     = opp.get('context') or '—'
    what    = opp.get('phrase_topic') or '—'
    ask     = float(opp.get('best_ask') or 0)
    conf    = float(opp.get('blended_confidence') or 0)
    until   = _hours_until(opp.get('resolution_deadline'))
    thin    = opp.get('liquidity_flag', False)
    depth   = float(opp.get('total_depth_usd') or 0)

    lines = [
        f'🎯 Trade Found — 🟢 YES · "{q}"',
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
    lines += ['', 'Reply: y [amount] / n / w / d']
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
            queue_status       = 'open',
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
    log.info('Opened queued opportunity: %s', mid[:14])


def advance_queue() -> None:
    """Open the next queued opportunity (if any). Callers must have already
    cleared the current open slot (status set to approved/skipped/expired)."""
    nxt = _get_next_queued()
    if nxt:
        _open_opp(nxt)
    else:
        log.info('Queue empty — nothing to advance to')


# ── APScheduler job ───────────────────────────────────────────────────────────

def run_alert_check() -> None:
    # 1. Check whether the currently open opportunity has gone stale
    open_opp = get_open_opportunity()
    if open_opp:
        if open_opp['status'] == 'expired':
            mid = open_opp['market_id']
            q   = (open_opp.get('question') or mid)[:60]
            log.info('Auto-skipping expired open opportunity: %s', mid[:14])
            send_message(f'⏭ Auto-skipped "{q}" — edge closed or price ceiling hit')
            skip_opp(mid)
            advance_queue()
        else:
            # Still valid — wait for reply before queuing more
            return

    # 2. Queue new qualifying, unqueued opportunities
    candidates = db.fetchall("""
        SELECT mo.market_id, mo.blended_confidence, mo.edge_pct,
               mo.alerted, mo.alerted_edge_pct, mo.alerted_confidence
        FROM mention_opportunities mo
        WHERE mo.status = 'pending'
          AND mo.queue_status IS NULL
        ORDER BY mo.qualified_at ASC
    """)

    for row in candidates:
        mid         = row['market_id']
        was_alerted = row.get('alerted', False)
        prev_edge   = row.get('alerted_edge_pct')
        prev_conf   = row.get('alerted_confidence')

        if was_alerted and prev_edge is not None:
            edge_moved = abs(float(row['edge_pct']) - float(prev_edge)) > REALERT_THRESHOLD
            conf_moved = (
                prev_conf is not None
                and abs(float(row['blended_confidence']) - float(prev_conf)) > REALERT_THRESHOLD
            )
            if not (edge_moved or conf_moved):
                continue

        db.execute("""
            UPDATE mention_opportunities
            SET queued_at = %s, queue_status = 'queued'
            WHERE market_id = %s
        """, (datetime.now(timezone.utc).isoformat(), mid))
        log.info('Queued: %s', mid[:14])

    # 3. If nothing is open, open the next in queue
    if not get_open_opportunity():
        nxt = _get_next_queued()
        if nxt:
            _open_opp(nxt)
