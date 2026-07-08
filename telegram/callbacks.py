import logging
from datetime import datetime, timezone

import httpx

import db
import trading_state
from config import TELEGRAM_BOT_TOKEN
from telegram import queue_state
from telegram.alerts import (
    get_open_opportunity,
    get_opportunity_by_id,
    advance_queue,
    skip_opp,
    resolve_dt_str,
    format_alert,
)

log = logging.getLogger(__name__)

_state: dict = {
    'last_update_id':    0,
    'longshot_pending':  None,  # set by _handle_b, cleared by y/n
}


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
            SET status = 'approved'
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
            SET status = 'approved'
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


def _handle_r(opp: dict, chat_id: str) -> None:
    """Re-fetch live price/edge for the currently open opportunity and resend the card."""
    from edge.clob_client import fetch_price, fetch_book
    from edge.calculator import compute

    market_id = opp['market_id']
    clob_ids  = opp.get('clob_token_ids') or []
    blended   = float(opp.get('blended_confidence') or 0)

    if not clob_ids:
        _send(chat_id, '⚠️ No token ID on record — cannot refresh.')
        return

    best_ask = fetch_price(clob_ids[0], side='BUY')
    if best_ask is None:
        _send(chat_id, '⚠️ Could not fetch live price — try again shortly.')
        return

    book = fetch_book(clob_ids[0])
    if not book:
        _send(chat_id, '⚠️ Could not fetch order book — try again shortly.')
        return

    result, reason = compute(book, blended, best_ask)

    if result is None:
        # No longer qualifies — auto-skip and advance
        q = (opp.get('question') or market_id)[:60]
        log.info('Refresh disqualified %s: %s', market_id[:14], reason)
        skip_opp(market_id)
        _send(chat_id, f'⏭ Refreshed — no longer qualifies ({reason})\nSkipping "{q}".')
        advance_queue()
        return

    # Update the DB row with fresh numbers
    db.execute("""
        UPDATE mention_opportunities SET
            best_ask        = %(best_ask)s,
            edge_pct        = %(edge_pct)s,
            max_size_usd    = %(max_size_usd)s,
            total_depth_usd = %(total_depth_usd)s,
            liquidity_flag  = %(liquidity_flag)s,
            last_price_check_at = %(now)s
        WHERE market_id = %(market_id)s
    """, {**result, 'now': datetime.now(timezone.utc).isoformat(), 'market_id': market_id})

    # Resend alert card with updated numbers
    updated_opp = {**opp, **result}
    _send(chat_id, format_alert(updated_opp, refreshed=True))
    log.info('Refreshed alert: %s  ask=%.3f  edge=+%.1f%%',
             market_id[:14], result['best_ask'], result['edge_pct'] * 100)


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


# ── Longshot buy handlers ─────────────────────────────────────────────────────

def _handle_b(text: str, chat_id: str) -> None:
    """Handle `b {row}{c|e} {amount}` — longshot buy intent, shows confirmation."""
    from longshot.digest import get_digest_rows

    parts = text.lower().split()
    if len(parts) < 3:
        _send(chat_id, 'Format: b {row}{c|e} {amount}  e.g. "b 1c 10"')
        return

    row_side_str = parts[1]
    amount_str   = parts[2]

    side_char = ''
    row_str   = row_side_str
    if row_side_str and row_side_str[-1] in ('c', 'e'):
        side_char = row_side_str[-1]
        row_str   = row_side_str[:-1]

    try:
        row_num = int(row_str)
        amount  = float(amount_str.replace('$', '').replace(',', ''))
        if row_num < 1 or amount <= 0:
            raise ValueError
    except ValueError:
        _send(chat_id, 'Format: b {row}{c|e} {amount}  e.g. "b 1c 10"')
        return

    digest_rows = get_digest_rows()
    if not digest_rows:
        _send(chat_id, 'No current longshot digest — wait for the next scorer run.')
        return
    if row_num > len(digest_rows):
        _send(chat_id, f'Only {len(digest_rows)} rows in the last digest.')
        return

    row        = digest_rows[row_num - 1]
    mid        = row['market_id']
    cheap_side = row.get('cheap_side', 'yes')
    exp_side   = 'no' if cheap_side == 'yes' else 'yes'

    if side_char == 'e':
        trade_side  = exp_side
        trade_price = float(row.get('expensive_price') or 0)
    else:
        trade_side  = cheap_side
        trade_price = float(row.get('cheap_price') or 0)

    if trade_price <= 0:
        _send(chat_id, 'No valid price for that row — try after the next scorer run.')
        return

    clob_ids   = row.get('clob_token_ids') or []
    token_idx  = 0 if trade_side.lower() == 'yes' else 1
    clob_token = clob_ids[token_idx] if token_idx < len(clob_ids) else None

    question = (row.get('question') or mid)[:60]
    payout   = round(amount / trade_price, 2) if trade_price else 0

    _state['longshot_pending'] = {
        'market_id':  mid,
        'side':       trade_side.upper(),
        'price':      trade_price,
        'amount':     amount,
        'payout':     payout,
        'question':   question,
        'clob_token': clob_token,
        'composite':  float(row.get('composite_score') or 0),
    }

    _send(chat_id,
          f'Confirm: BUY {trade_side.upper()} · "{question}"\n'
          f'Amount: ${amount:.0f} @ ${trade_price:.2f}\n'
          f'Est. payout: ${payout:.2f}\n\n'
          f'Reply y to confirm, n to cancel.')


def _handle_longshot_confirm(chat_id: str) -> None:
    """Execute confirmed longshot trade (after `b` → `y`)."""
    pending = _state['longshot_pending']
    _state['longshot_pending'] = None

    mid        = pending['market_id']
    side       = pending['side']
    price      = pending['price']
    amount     = pending['amount']
    payout     = pending['payout']
    question   = pending['question']
    clob_token = pending['clob_token']
    composite  = pending['composite']

    if trading_state.is_paused() or not clob_token:
        reason = 'paused' if trading_state.is_paused() else 'no token ID'
        db.execute("""
            INSERT INTO mention_trades (
                market_id, side, size_usd, price, confidence, edge_pct,
                status, telegram_chat_id, approved_at, strategy
            ) VALUES (%s, %s, %s, %s, %s, NULL, 'approved_dry_run', %s, NOW(), 'longshot_momentum')
        """, (mid, side, amount, price, composite, str(chat_id)))
        _send(chat_id,
              f'✅ Longshot Logged (dry-run — {reason}) · {side} · "{question}"\n\n'
              f'💵 Amount: ${amount:.0f}\n'
              f'📈 To return: ${payout:.2f}')
        log.info('Longshot dry-run (%s): %s  $%.0f @ %.3f', reason, mid[:14], amount, price)
        return

    try:
        from edge.executor import place_order
        result      = place_order(
            token_id=clob_token,
            size_usd=amount,
            best_ask=price,
            blended_confidence=max(composite, price + 0.10),
        )
        order_id    = result['order_id']
        limit_price = result['limit_price']
        db.execute("""
            INSERT INTO mention_trades (
                market_id, side, size_usd, price, confidence, edge_pct,
                status, telegram_chat_id, order_id, limit_price, approved_at, strategy
            ) VALUES (%s, %s, %s, %s, %s, NULL, 'approved', %s, %s, %s, NOW(), 'longshot_momentum')
        """, (mid, side, amount, price, composite, str(chat_id), order_id, limit_price))
        _send(chat_id,
              f'✅ Longshot Trade Placed · {side} · "{question}"\n\n'
              f'💵 Amount: ${amount:.0f}\n'
              f'📈 To return: ${payout:.2f}')
        log.info('Longshot order: %s  $%.0f @ %.3f  order=%s',
                 mid[:14], amount, limit_price, order_id[:12])

    except Exception as e:
        log.error('Longshot execution error for %s: %s', mid[:14], e)
        db.execute("""
            INSERT INTO mention_trades (
                market_id, side, size_usd, price, confidence, edge_pct,
                status, telegram_chat_id, approved_at, strategy
            ) VALUES (%s, %s, %s, %s, %s, NULL, 'approved_dry_run', %s, NOW(), 'longshot_momentum')
        """, (mid, side, amount, price, composite, str(chat_id)))
        _send(chat_id, f'⚠️ Execution failed — dry-run logged\n{str(e)[:200]}')


# ── Message dispatcher ────────────────────────────────────────────────────────

def _resolve_open_opp(chat_id: str) -> dict | None:
    """
    Return the currently open opportunity, bound to the specific market_id that
    was last alerted.  Handles three cases:

    1. queue_state has a market_id → fetch that specific row.
       If it's no longer pending+alerted (expired, skipped, approved between alert
       and reply), warn the user and return None.
    2. queue_state is empty (process restart) → fall back to DB query with ORDER BY
       to get a deterministic row; self-heal queue_state from it.
    3. Nothing open at all → return None.
    """
    mid = queue_state.get()

    if mid:
        opp = get_opportunity_by_id(mid)
        if not opp:
            log.warning('queue_state had %s but row not found — clearing', mid[:14])
            queue_state.set_open(None)
            _send(chat_id, '⚠️ The tracked opportunity no longer exists. '
                           'Queue state cleared — next alert coming shortly.')
            return None
        if opp.get('status') != 'pending' or not opp.get('alerted'):
            log.warning('queue_state %s is %s/alerted=%s — stale, clearing',
                        mid[:14], opp.get('status'), opp.get('alerted'))
            queue_state.set_open(None)
            _send(chat_id, f'⚠️ That trade ({opp.get("question","")[:40]}…) '
                           f'is no longer open (status: {opp.get("status")}). '
                           f'Advancing queue.')
            advance_queue()
            return None
        return opp

    # Startup/restart recovery path
    opp = get_open_opportunity()  # also sets queue_state as a side effect
    return opp


def _handle_message(update: dict) -> None:
    msg     = update.get('message', {})
    chat_id = str(msg.get('chat', {}).get('id', ''))
    text    = (msg.get('text') or '').strip()

    if not text:
        return

    if text.startswith('/'):
        _handle_command(text.lower().split()[0], chat_id)
        return

    token = text.lower().split()[0]

    # Longshot buy intent
    if token == 'b':
        _handle_b(text, chat_id)
        return

    # Longshot confirmation takes priority (y/n confirm the pending b-command)
    if _state.get('longshot_pending'):
        if token == 'y':
            _handle_longshot_confirm(chat_id)
            return
        elif token == 'n':
            _state['longshot_pending'] = None
            _send(chat_id, '❌ Longshot trade cancelled.')
            return

    # LLM queue handlers — resolve against the SPECIFIC open opportunity
    opp = _resolve_open_opp(chat_id)
    if opp is None:
        if token not in ('y', 'n', 'w', 'd', 'r'):
            _send(chat_id, 'No open trade right now.')
        return

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
        _send(chat_id, "didn't catch that — reply y [amount], n, w, d, or r")

    elif token == 'n':
        _handle_n(opp, chat_id)

    elif token == 'w':
        _handle_w(opp, chat_id)

    elif token == 'd':
        _handle_d(opp, chat_id)

    elif token in ('r', 'refresh'):
        _handle_r(opp, chat_id)

    else:
        _send(chat_id, "didn't catch that — reply y [amount], n, w, d, or r")


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
