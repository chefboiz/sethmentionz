import logging
from datetime import datetime, timezone

import httpx

import db
import trading_state
from config import TELEGRAM_BOT_TOKEN
from telegram.alerts import (
    get_open_opportunity,
    advance_queue,
    skip_opp,
    resolve_dt_str,
)

log = logging.getLogger(__name__)

_state: dict = {'last_update_id': 0}


# ── Low-level send ────────────────────────────────────────────────────────────

def _send(chat_id, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        with httpx.Client(timeout=8) as c:
            c.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': chat_id, 'text': text},
            )
    except Exception as e:
        log.error('sendMessage error: %s', e)


# ── Trade logging ─────────────────────────────────────────────────────────────

def _log_trade(market_id: str, size_usd: float, chat_id: str,
               status: str = 'approved_dry_run',
               order_id: str | None = None,
               limit_price: float | None = None) -> int | None:
    snap = db.fetchone("""
        SELECT best_ask, blended_confidence, edge_pct
        FROM mention_opportunities WHERE market_id = %s
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
        'market_id':        market_id,
        'size_usd':         size_usd,
        'price':            snap.get('best_ask'),
        'confidence':       snap.get('blended_confidence'),
        'edge_pct':         snap.get('edge_pct'),
        'status':           status,
        'telegram_chat_id': str(chat_id),
        'order_id':         order_id,
        'limit_price':      limit_price,
        'approved_at':      datetime.now(timezone.utc).isoformat(),
    })
    return row['id'] if row else None


# ── Reply handlers ────────────────────────────────────────────────────────────

def _handle_y(opp: dict, amount: float, chat_id: str) -> None:
    market_id   = opp['market_id']
    best_ask    = float(opp.get('best_ask') or 0)
    confidence  = float(opp.get('blended_confidence') or 0)
    q           = (opp.get('question') or market_id)[:80]
    clob_ids    = opp.get('clob_token_ids') or []
    payout      = round(amount / best_ask, 2) if best_ask else 0.0
    resolve_str = resolve_dt_str(opp.get('resolution_deadline'))

    if trading_state.is_paused() or not clob_ids:
        reason = 'paused' if trading_state.is_paused() else 'no token ID'
        _log_trade(market_id, amount, chat_id, status='approved_dry_run')
        db.execute("""
            UPDATE mention_opportunities
            SET status = 'approved', queue_status = NULL
            WHERE market_id = %s
        """, (market_id,))
        _send(chat_id,
              f'✅ Trade Placed (dry-run — {reason}) — 🟢 YES · "{q}"\n\n'
              f'💵 Amount: ${amount:.0f}\n'
              f'📈 To return: ${payout:.2f}\n'
              f'⏱ Resolves: {resolve_str}')
        log.info('Dry-run trade (%s): %s  $%.0f', reason, market_id[:14], amount)
        advance_queue()
        return

    try:
        from edge.executor import place_order
        result      = place_order(
            token_id=clob_ids[0],
            size_usd=amount,
            best_ask=best_ask,
            blended_confidence=confidence,
        )
        order_id    = result['order_id']
        limit_price = result['limit_price']

        _log_trade(market_id, amount, chat_id,
                   status='approved', order_id=order_id, limit_price=limit_price)
        db.execute("""
            UPDATE mention_opportunities
            SET status = 'approved', queue_status = NULL
            WHERE market_id = %s
        """, (market_id,))
        _send(chat_id,
              f'✅ Trade Placed — 🟢 YES · "{q}"\n\n'
              f'💵 Amount: ${amount:.0f}\n'
              f'📈 To return: ${payout:.2f}\n'
              f'⏱ Resolves: {resolve_str}')
        log.info('Order placed: %s  $%.0f @ %.3f', market_id[:14], amount, limit_price)

    except Exception as e:
        log.error('Execution error for %s: %s', market_id[:14], e)
        _log_trade(market_id, amount, chat_id, status='approved_dry_run')
        _send(chat_id, f'⚠️ Execution failed — dry-run logged\n{str(e)[:200]}')

    advance_queue()


def _handle_n(opp: dict, chat_id: str) -> None:
    market_id = opp['market_id']
    q         = (opp.get('question') or market_id)[:80]
    skip_opp(market_id)
    _send(chat_id, f'❌ Trade Skipped — "{q}"')
    log.info('Skipped: %s', market_id[:14])
    advance_queue()


def _handle_w(opp: dict, chat_id: str) -> None:
    sig = db.fetchone("""
        SELECT llm_reasoning FROM mention_signals
        WHERE market_id = %s
        ORDER BY scored_at DESC
        LIMIT 1
    """, (opp['market_id'],))
    reasoning = (sig or {}).get('llm_reasoning') or 'No reasoning available.'
    _send(chat_id, f'🧠 Reasoning\n\n{reasoning}')


def _handle_d(opp: dict, chat_id: str) -> None:
    criteria = (opp.get('resolution_criteria_summary') or '').strip()
    desc     = (opp.get('description') or '').strip()
    body     = criteria or desc or 'No description available.'
    _send(chat_id, f'📄 Description\n\n{body}')


def _handle_command(cmd: str, chat_id: str) -> None:
    if cmd == '/pause':
        trading_state.pause()
        _send(chat_id, '⏸ Trading paused. Use /resume to re-enable.')
    elif cmd == '/resume':
        trading_state.resume()
        _send(chat_id, '▶️ Trading resumed.')
    elif cmd == '/status':
        state = '⏸ PAUSED' if trading_state.is_paused() else '▶️ LIVE'
        opp   = get_open_opportunity()
        q     = f'\n\nOpen trade: "{(opp.get("question") or "")[:60]}"' if opp else ''
        _send(chat_id, f'Trading state: {state}{q}')
    elif cmd == '/calibration':
        from calibration.report import get_chunks
        for chunk in get_chunks():
            _send(chat_id, chunk)


# ── Message dispatcher ────────────────────────────────────────────────────────

def _handle_message(update: dict) -> None:
    msg     = update.get('message', {})
    chat_id = str(msg.get('chat', {}).get('id', ''))
    text    = (msg.get('text') or '').strip()

    if not text:
        return

    if text.startswith('/'):
        _handle_command(text.lower().split()[0], chat_id)
        return

    opp = get_open_opportunity()
    if opp is None:
        _send(chat_id, 'No open trade right now.')
        return

    token = text.lower().split()[0]

    if token == 'y':
        parts = text.split()
        if len(parts) >= 2:
            try:
                amount = float(parts[1].replace('$', '').replace(',', ''))
                if amount <= 0:
                    raise ValueError
                _handle_y(opp, amount, chat_id)
                return
            except ValueError:
                pass
        _send(chat_id, "didn't catch that — reply y [amount], n, w, or d")

    elif token == 'n':
        _handle_n(opp, chat_id)

    elif token == 'w':
        _handle_w(opp, chat_id)

    elif token == 'd':
        _handle_d(opp, chat_id)

    else:
        _send(chat_id, "didn't catch that — reply y [amount], n, w, or d")


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
            if 'message' in update:
                _handle_message(update)
        except Exception as e:
            log.error('Error processing update %s: %s', update.get('update_id'), e)
