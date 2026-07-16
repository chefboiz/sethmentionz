"""
Polymarket CLOB order placement, via Polymarket's unified SDK (polymarket-client).

py-clob-client was archived by Polymarket in 2026-05 and its order-signing format
is now rejected by the live CLOB ("invalid order version"). This uses the
replacement SDK's SecureClient, which auto-derives API creds and auto-classifies
the wallet relationship (POLY_PROXY here) from POLYMARKET_PRIVATE_KEY /
POLYMARKET_PROXY_ADDRESS — no explicit signature_type needed.

Order type: GTC limit (no `expiration` passed) — we want the order to sit at our
price rather than cross the spread and eat the edge. The fill monitor polls for
fill status.
"""

import logging

from config import (
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_PROXY_ADDRESS,
    EDGE_MIN_PCT,
    SLIPPAGE_TOLERANCE,
)

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from polymarket import SecureClient

        if not POLYMARKET_PRIVATE_KEY:
            raise RuntimeError('POLYMARKET_PRIVATE_KEY not set')

        _client = SecureClient.create(
            private_key=POLYMARKET_PRIVATE_KEY,
            wallet=POLYMARKET_PROXY_ADDRESS,
        )
        log.info('SecureClient ready (wallet: %s…)', POLYMARKET_PROXY_ADDRESS[:10])
    return _client


def place_order(
    token_id: str,
    size_usd: float,
    best_ask: float,
    blended_confidence: float,
    force: bool = False,
) -> dict:
    """
    Place a GTC limit BUY order for YES tokens.

    Limit price logic:
      max_fill_price = blended_confidence - EDGE_MIN_PCT  (never pay above this)
      limit_price    = min(best_ask + SLIPPAGE_TOLERANCE, max_fill_price)

    Returns {'order_id': str, 'limit_price': float, 'shares': float}.
    Raises on failure — caller handles fallback to dry-run.
    """
    # Price: aggressive enough to fill promptly, never erodes edge below floor
    if force:
        limit_px = round(min(best_ask + SLIPPAGE_TOLERANCE, 0.97), 2)
    else:
        max_fill = round(blended_confidence - EDGE_MIN_PCT, 2)
        limit_px  = round(min(best_ask + SLIPPAGE_TOLERANCE, max_fill), 2)
    limit_px  = max(limit_px, 0.01)   # CLOB minimum

    # size = number of shares (tokens), not USDC
    shares = round(size_usd / limit_px, 2)

    # Polymarket enforces a $1 minimum notional — bump if needed
    if limit_px * shares < 1.0:
        shares = round(1.0 / limit_px + 0.01, 2)

    log.info(
        'Placing order: token=…%s  price=%.2f  shares=%.2f  (~$%.0f)',
        token_id[-8:], limit_px, shares, limit_px * shares,
    )

    client = _get_client()
    result = client.place_limit_order(
        token_id=token_id,
        price=limit_px,
        size=shares,
        side='BUY',
    )

    if not result.ok:
        raise RuntimeError(f'Order rejected: {result.code} — {result.message}')

    log.info('Order placed: %s  price=%.2f  shares=%.2f  status=%s',
              result.order_id[:16], limit_px, shares, result.status)
    return {'order_id': result.order_id, 'limit_price': limit_px, 'shares': shares}


def get_order_status(order_id: str) -> dict:
    """
    Returns {'status': str, 'size_matched': float, 'avg_price': float}.
    Possible statuses: live, matched, canceled, delayed, error.
    """
    try:
        order = _get_client().get_order(order_id=order_id)
        return {
            'status':       order.status or 'unknown',
            'size_matched': float(order.size_matched or 0),
            'avg_price':    float(order.price or 0),
        }
    except Exception as e:
        log.error('get_order_status(%s): %s', order_id[:16], e)
        return {'status': 'error', 'size_matched': 0, 'avg_price': 0}


def cancel_order(order_id: str) -> bool:
    try:
        resp = _get_client().cancel_order(order_id=order_id)
        ok = order_id in resp.canceled
        if ok:
            log.info('Cancelled order: %s', order_id[:16])
        else:
            log.warning('Cancel not confirmed for %s: %s', order_id[:16], resp.not_canceled)
        return ok
    except Exception as e:
        log.error('cancel_order(%s): %s', order_id[:16], e)
        return False
