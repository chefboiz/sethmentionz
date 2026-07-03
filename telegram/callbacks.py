import logging
from datetime import datetime, timezone

import httpx

import db
import trading_state
from config import TELEGRAM_BOT_TOKEN
from telegram.alerts import resolve_market

log = logging.getLogger(__name__)

_state: dict                   = {'last_update_id': 0}
_awaiting_size: dict[str, str] = {}   # str(chat_id) -> market_id


# ── Low-level Telegram helpers ────────────────────────────────────────────────

def _send(chat_id, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        with httpx.Client(timeout=8) as c:
            c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            )
    except Exception as e:
        log.error('sendMessage error: %s', e)


def _ack(callback_query_id: str, text: str = '') -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        with httpx.Client(timeout=5) as c:
            c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery',
                json={'callback_query_id': callback_query_id, 'text': text},
            )
    except Exception:
        pass


# ── Trade helpers ─────────────────────────────────────────────────────────────

def _snapshot_opp(market_id: str) -> dict:
    return db.fetchone("""
        SELECT mo.max_size_usd, mo.best_ask, mo.blended_confidence, mo.edge_pct,
               mm.clob_token_ids, mm.question
        FROM mention_opportunities mo
        JOIN mention_markets mm ON mm.market_id = mo.market_id
        WHERE mo.market_id = %s
    """, (market_id,)) or {}


def _log_trade(market_id: str, size_usd: float, chat_id: str,
               status: str = 'approved_dry_run',
               order_id: str | None = None,
               limit_price: float | None = None) -> int | None:
    snap = db.fetchone("""
        SELECT best_ask, blended_confidence, edge_pct
        FROM mention_opportunities
        WHERE market_id = %s
    """, (market_id,)) or {}

    row = db.insert_returning("""
        INSERT INTO mention_trades (
            market_id, side, size_usd, price, confidence, edge_pct,
            status, telegram_chat_id, order_id, limit_price, approved_at
        ) VALUES (
            %(market_id)s, 'YES', %(size_usd)s, %(price)s, %(confidence)s, %(edge_pct)s,
            %(status)s, %(telegram_chat_id)s, %(order_id)s, %(limit_price)s, %(approved_at)s
        )
        RETURNING id
    """, {
        'market_id':       market_id,
        'size_usd':        size_usd,
        'price':           snap.get('best_ask'),
        'confidence':      snap.get('blended_confidence'),
        'edge_pct':        snap.get('edge_pct'),
        'status':          status,
        'telegram_chat_id': str(chat_id),
        'order_id':        order_id,
        'limit_price':     limit_price,
        'approved_at':     datetime.now(timezone.utc).isoformat(),
    })
    return row['id'] if row else None


# ── Button callback handler ───────────────────────────────────────────────────

def _handle_callback(update: dict) -> None:
    cq      = update['callback_query']
    cq_id   = cq['id']
    data    = cq.get('data', '')
    chat_id = cq['message']['chat']['id']

    if ':' not in data:
        return

    action, short_id = data.split(':', 1)
    market_id = resolve_market(short_id)

    if market_id is None:
        _ack(cq_id, 'Unknown — registry cleared on restart')
        return

    # ── Approve ───────────────────────────────────────────────────────────────
    if action == 'a':
        opp        = _snapshot_opp(market_id)
        size       = float(opp.get('max_size_usd') or 50)
        best_ask   = float(opp.get('best_ask') or 0)
        confidence = float(opp.get('blended_confidence') or 0)
        edge       = float(opp.get('edge_pct') or 0)
        q          = (opp.get('question') or market_id)[:80]
        clob_ids   = opp.get('clob_token_ids') or []

        if trading_state.is_paused() or not clob_ids:
            reason = 'paused' if trading_state.is_paused() else 'no token ID'
            _log_trade(market_id, size, chat_id, status='approved_dry_run')
            db.execute("""
                UPDATE mention_opportunities SET status = 'approved' WHERE market_id = %s
            """, (market_id,))
            _ack(cq_id, '✅ Logged (dry-run)')
            _send(chat_id,
                  f'✅ <b>DRY RUN</b> ({reason})\n\n'
                  f'{q}\n\n'
                  f'Size: <b>${size:.0f}</b>  •  Ask: {best_ask:.3f}  •  Edge: +{edge*100:.1f}%\n'
                  f'<i>Use /resume to enable live trading</i>')
            log.info('Approved dry-run (%s): %s  $%.0f', reason, market_id[:14], size)
            return

        try:
            from edge.executor import place_order
            result      = place_order(
                token_id=clob_ids[0],
                size_usd=size,
                best_ask=best_ask,
                blended_confidence=confidence,
            )
            order_id    = result['order_id']
            limit_price = result['limit_price']

            _log_trade(market_id, size, chat_id,
                       status='approved', order_id=order_id, limit_price=limit_price)
            db.execute("""
                UPDATE mention_opportunities SET status = 'approved' WHERE market_id = %s
            """, (market_id,))

            _ack(cq_id, '✅ Order placed')
            _send(chat_id,
                  f'✅ <b>Order placed</b>\n\n'
                  f'{q}\n\n'
                  f'Limit: <b>{limit_price:.3f}</b>  •  Size: <b>${size:.0f}</b>  •  Edge: +{edge*100:.1f}%\n'
                  f'Order: <code>{order_id[:20]}</code>\n'
                  f'<i>Fill monitor polling every 30s</i>')
            log.info('Order placed: %s  $%.0f @ %.3f', market_id[:14], size, limit_price)

        except Exception as e:
            log.error('Execution error for %s: %s', market_id[:14], e)
            _log_trade(market_id, size, chat_id, status='approved_dry_run')
            _ack(cq_id, '⚠️ Execution failed — dry-run logged')
            _send(chat_id,
                  f'⚠️ <b>Execution failed — dry-run logged</b>\n'
                  f'<code>{str(e)[:200]}</code>')

    # ── Skip ──────────────────────────────────────────────────────────────────
    elif action == 's':
        db.execute("""
            UPDATE mention_opportunities SET status = 'skipped' WHERE market_id = %s
        """, (market_id,))
        _ack(cq_id, 'Skipped')
        _send(chat_id, f'❌ Skipped <code>{market_id[:16]}</code>')
        log.info('Skipped: %s', market_id[:14])

    # ── Approve smaller ───────────────────────────────────────────────────────
    elif action == 'sm':
        _awaiting_size[str(chat_id)] = market_id
        _ack(cq_id)
        _send(chat_id, '📉 Reply with the size in USD (e.g. <code>25</code>)')
        log.info('Awaiting smaller size: %s', market_id[:14])


# ── Message handler (size replies + /pause /resume) ───────────────────────────

def _handle_message(update: dict) -> None:
    msg     = update.get('message', {})
    chat_id = str(msg.get('chat', {}).get('id', ''))
    text    = (msg.get('text') or '').strip()

    cmd = text.lower().split()[0] if text else ''

    if cmd == '/pause':
        trading_state.pause()
        _send(chat_id,
              '⏸ <b>Trading paused</b>\n'
              'Alerts and scanning continue. [Approve] falls back to dry-run.\n'
              'Use /resume to re-enable.')
        return

    if cmd == '/resume':
        trading_state.resume()
        _send(chat_id, '▶️ <b>Trading resumed</b> — [Approve] will place real orders.')
        return

    if cmd == '/status':
        state = '⏸ PAUSED' if trading_state.is_paused() else '▶️ LIVE'
        _send(chat_id, f'Trading state: <b>{state}</b>')
        return

    if cmd == '/calibration':
        from calibration.report import get_chunks
        for chunk in get_chunks():
            _send(chat_id, chunk)
        return

    # Size reply for approve_smaller
    if chat_id not in _awaiting_size:
        return

    market_id = _awaiting_size.pop(chat_id)

    try:
        size = float(text.replace('$', '').replace(',', ''))
        if size <= 0:
            raise ValueError('non-positive')
    except ValueError:
        _send(chat_id, '❌ Invalid — send a number like <code>25</code>')
        _awaiting_size[chat_id] = market_id
        return

    if trading_state.is_paused():
        _log_trade(market_id, size, chat_id, status='approved_dry_run')
        _send(chat_id,
              f'✅ <b>DRY RUN (paused)</b>\n'
              f'Size: <b>${size:.0f}</b> on <code>{market_id[:16]}</code>\n'
              f'<i>Use /resume to enable live trading</i>')
        log.info('Approved smaller dry-run (paused): %s  $%.0f', market_id[:14], size)
        return

    try:
        opp = db.fetchone("""
            SELECT mo.best_ask, mo.blended_confidence, mm.clob_token_ids
            FROM mention_opportunities mo
            JOIN mention_markets mm ON mm.market_id = mo.market_id
            WHERE mo.market_id = %s
        """, (market_id,)) or {}

        clob_ids = opp.get('clob_token_ids') or []

        from edge.executor import place_order
        result = place_order(
            token_id=clob_ids[0],
            size_usd=size,
            best_ask=float(opp.get('best_ask') or 0),
            blended_confidence=float(opp.get('blended_confidence') or 0),
        )
        _log_trade(market_id, size, chat_id,
                   status='approved', order_id=result['order_id'],
                   limit_price=result['limit_price'])
        _send(chat_id,
              f'✅ <b>Order placed (smaller)</b>\n'
              f'Size: <b>${size:.0f}</b>  •  Limit: {result["limit_price"]:.3f}\n'
              f'Order: <code>{result["order_id"][:20]}</code>')
        log.info('Approved smaller: %s  $%.0f', market_id[:14], size)

    except Exception as e:
        log.error('Smaller execution error: %s', e)
        _log_trade(market_id, size, chat_id, status='approved_dry_run')
        _send(chat_id,
              f'⚠️ <b>Execution failed — dry-run logged</b>\n<code>{str(e)[:200]}</code>')


# ── APScheduler job ───────────────────────────────────────────────────────────

def run_callback_poll() -> None:
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates',
                params={
                    'offset':  _state['last_update_id'] + 1,
                    'limit':   100,
                    'timeout': 0,
                },
            )
            r.raise_for_status()
            updates = r.json().get('result', [])
    except Exception as e:
        log.error('getUpdates error: %s', e)
        return

    for update in updates:
        _state['last_update_id'] = max(_state['last_update_id'], update['update_id'])
        try:
            if 'callback_query' in update:
                _handle_callback(update)
            elif 'message' in update:
                msg_text = (update['message'].get('text') or '').strip()
                chat_id  = str(update['message'].get('chat', {}).get('id', ''))
                if msg_text.startswith('/') or chat_id in _awaiting_size:
                    _handle_message(update)
        except Exception as e:
            log.error('Error processing update %s: %s', update.get('update_id'), e)
