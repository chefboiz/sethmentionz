import logging
from datetime import datetime, timezone, timedelta

import db
import telegram as tg
from config import FILL_TIMEOUT_MINUTES
from edge.executor import get_order_status, cancel_order

log = logging.getLogger(__name__)


def run_fill_monitor() -> None:
    """
    Poll CLOB for fill status on all pending (status='approved') trades.
    - Filled:     update to 'filled', log actuals, send Telegram confirmation.
    - Timed out:  cancel the order, mark 'no_fill', send Telegram alert.
    - Cancelled by CLOB: mark 'cancelled', send Telegram alert.
    - Still live: update fill_checked_at timestamp, continue polling.
    """
    client  = db.get_client()
    now     = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    all_approved = (
        client.table('mention_trades')
        .select('id, market_id, order_id, size_usd, limit_price, approved_at')
        .eq('status', 'approved')
        .execute()
    ).data or []

    # Filter in Python — avoids PostgREST IS NOT NULL syntax edge cases
    trades = [t for t in all_approved if t.get('order_id')]

    if not trades:
        return

    log.debug('Fill monitor: checking %d live order(s)', len(trades))

    for trade in trades:
        order_id  = trade['order_id']
        trade_id  = trade['id']
        market_id = trade['market_id']
        approved  = datetime.fromisoformat(
            trade['approved_at'].replace('Z', '+00:00')
        )
        timed_out = (now - approved) > timedelta(minutes=FILL_TIMEOUT_MINUTES)

        info     = get_order_status(order_id)
        status   = info['status']
        matched  = info['size_matched']
        avg_px   = info['avg_price']

        if matched > 0 or status == 'matched':
            fill_usd = round(matched * avg_px, 2) if avg_px else round(
                matched * float(trade.get('limit_price') or 0), 2
            )
            client.table('mention_trades').update({
                'status':          'filled',
                'fill_price':      avg_px,
                'fill_shares':     matched,
                'fill_size_usd':   fill_usd,
                'fill_checked_at': now_iso,
            }).eq('id', trade_id).execute()

            log.info('Filled: trade=%s  %.2f shares @ %.4f  ($%.2f)', trade_id, matched, avg_px, fill_usd)
            tg.send_message(
                f'✅ <b>Order filled</b>\n'
                f'Market: <code>{market_id[:16]}</code>\n'
                f'{matched:.2f} shares @ {avg_px:.4f}  ≈ <b>${fill_usd:.0f}</b>'
            )

        elif status == 'canceled':
            client.table('mention_trades').update({
                'status':          'cancelled',
                'cancelled_at':    now_iso,
                'fill_checked_at': now_iso,
            }).eq('id', trade_id).execute()

            log.info('Order cancelled by CLOB: trade=%s', trade_id)
            tg.send_message(
                f'❌ <b>Order cancelled by CLOB</b>\n'
                f'Market: <code>{market_id[:16]}</code>\n'
                f'Order: <code>{order_id[:16]}</code>'
            )

        elif status == 'live' and timed_out:
            cancel_order(order_id)
            client.table('mention_trades').update({
                'status':          'no_fill',
                'cancelled_at':    now_iso,
                'fill_checked_at': now_iso,
            }).eq('id', trade_id).execute()

            log.info('Order timed out, cancelled: trade=%s', trade_id)
            tg.send_message(
                f'⏱ <b>Order timed out ({FILL_TIMEOUT_MINUTES}min) — cancelled</b>\n'
                f'Market: <code>{market_id[:16]}</code>\n'
                f'Order: <code>{order_id[:16]}</code>'
            )

        else:
            # Still live, within timeout — just bump the check timestamp
            client.table('mention_trades').update({
                'fill_checked_at': now_iso,
            }).eq('id', trade_id).execute()
