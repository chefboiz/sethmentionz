import logging
from datetime import datetime, timezone, timedelta

import db
import telegram as tg
from config import FILL_TIMEOUT_MINUTES
from edge.executor import get_order_status, cancel_order

log = logging.getLogger(__name__)


def run_fill_monitor() -> None:
    now     = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    trades = db.fetchall("""
        SELECT t.id, t.market_id, t.order_id, t.size_usd, t.limit_price, t.approved_at,
               mm.question
        FROM mention_trades t
        LEFT JOIN mention_markets mm ON mm.market_id = t.market_id
        WHERE t.status = 'approved' AND t.order_id IS NOT NULL
    """)

    if not trades:
        return

    log.debug('Fill monitor: checking %d live order(s)', len(trades))

    for trade in trades:
        order_id  = trade['order_id']
        trade_id  = trade['id']
        market_id = trade['market_id']
        raw      = trade['approved_at']
        approved = (raw if isinstance(raw, datetime) else
                    datetime.fromisoformat(str(raw).replace('Z', '+00:00')))
        if approved.tzinfo is None:
            approved = approved.replace(tzinfo=timezone.utc)
        timed_out = (now - approved) > timedelta(minutes=FILL_TIMEOUT_MINUTES)

        info    = get_order_status(order_id)
        status  = info['status']
        matched = info['size_matched']
        avg_px  = info['avg_price']

        if matched > 0 or status == 'matched':
            fill_usd = round(matched * avg_px, 2) if avg_px else round(
                matched * float(trade.get('limit_price') or 0), 2
            )
            db.execute("""
                UPDATE mention_trades SET
                    status          = 'filled',
                    fill_price      = %(fill_price)s,
                    fill_shares     = %(fill_shares)s,
                    fill_size_usd   = %(fill_size_usd)s,
                    fill_checked_at = %(now)s
                WHERE id = %(id)s
            """, {'fill_price': avg_px, 'fill_shares': matched,
                  'fill_size_usd': fill_usd, 'now': now_iso, 'id': trade_id})

            log.info('Filled: trade=%s  %.2f shares @ %.4f  ($%.2f)',
                     trade_id, matched, avg_px, fill_usd)
            q_short = (trade.get('question') or market_id)[:60]
            tg.send_message(
                'Filled: ' + q_short + chr(10)
                + f'${fill_usd:.0f} ({matched:.2f} shares @ ${avg_px:.4f})'
            )

        elif status == 'canceled':
            db.execute("""
                UPDATE mention_trades SET
                    status          = 'cancelled',
                    cancelled_at    = %(now)s,
                    fill_checked_at = %(now)s
                WHERE id = %(id)s
            """, {'now': now_iso, 'id': trade_id})

            log.info('Order cancelled by CLOB: trade=%s', trade_id)
            tg.send_message(
                f'❌ <b>Order cancelled by CLOB</b>\n'
                f'Market: <code>{market_id[:16]}</code>\n'
                f'Order: <code>{order_id[:16]}</code>'
            )

        elif status == 'live' and timed_out:
            cancel_order(order_id)
            db.execute("""
                UPDATE mention_trades SET
                    status          = 'no_fill',
                    cancelled_at    = %(now)s,
                    fill_checked_at = %(now)s
                WHERE id = %(id)s
            """, {'now': now_iso, 'id': trade_id})

            log.info('Order timed out, cancelled: trade=%s', trade_id)
            tg.send_message(
                f'⏱ <b>Order timed out ({FILL_TIMEOUT_MINUTES}min) — cancelled</b>\n'
                f'Market: <code>{market_id[:16]}</code>\n'
                f'Order: <code>{order_id[:16]}</code>'
            )

        else:
            db.execute("""
                UPDATE mention_trades SET fill_checked_at = %s WHERE id = %s
            """, (now_iso, trade_id))
