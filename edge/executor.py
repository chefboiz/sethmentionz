"""
Polymarket CLOB order placement — Python equivalent of sethbetz's getClobClient / placeBuyOrder.

Auth pattern matches sethbetz exactly:
  - POLYMARKET_PRIVATE_KEY  (0x-prefixed EVM private key)
  - POLYMARKET_PROXY_ADDRESS (your Polymarket profile/proxy wallet)
  - POLY_API_KEY / POLY_API_SECRET / POLY_API_PASSPHRASE (from polymarket.com → Settings → API)
  - signature_type=1  (POLY_PROXY — same as sethbetz's signatureType: 1)

Order type: GTC limit (not FAK market) — we want the order to sit at our price rather than
cross the spread and eat the edge. The fill monitor polls for fill status.
"""

import json
import logging

from config import (
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_PROXY_ADDRESS,
    POLY_API_KEY,
    POLY_API_SECRET,
    POLY_API_PASSPHRASE,
    EDGE_MIN_PCT,
    SLIPPAGE_TOLERANCE,
)

log = logging.getLogger(__name__)

_clob = None


def _get_clob():
    global _clob
    if _clob is None:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        if not POLYMARKET_PRIVATE_KEY:
            raise RuntimeError('POLYMARKET_PRIVATE_KEY not set')

        _clob = ClobClient(
            host='https://clob.polymarket.com',
            chain_id=137,          # Polygon mainnet
            key=POLYMARKET_PRIVATE_KEY,
            creds=ApiCreds(
                api_key=POLY_API_KEY,
                api_secret=POLY_API_SECRET,
                api_passphrase=POLY_API_PASSPHRASE,
            ),
            signature_type=1,      # POLY_PROXY — matches sethbetz
            funder=POLYMARKET_PROXY_ADDRESS,
        )
        log.info('CLOB client ready (proxy: %s…)', POLYMARKET_PROXY_ADDRESS[:10])
    return _clob


def place_order(
    token_id: str,
    size_usd: float,
    best_ask: float,
    blended_confidence: float,
) -> dict:
    """
    Place a GTC limit BUY order for YES tokens.

    Limit price logic:
      max_fill_price = blended_confidence - EDGE_MIN_PCT  (never pay above this)
      limit_price    = min(best_ask + SLIPPAGE_TOLERANCE, max_fill_price)

    Returns {'order_id': str, 'limit_price': float, 'shares': float}.
    Raises on failure — caller handles fallback to dry-run.
    """
    from py_clob_client.clob_types import OrderArgs, OrderType

    # Price: aggressive enough to fill promptly, never erodes edge below floor
    max_fill = round(blended_confidence - EDGE_MIN_PCT, 2)
    limit_px  = round(min(best_ask + SLIPPAGE_TOLERANCE, max_fill), 2)
    limit_px  = max(limit_px, 0.01)   # CLOB minimum

    # size in py-clob-client OrderArgs = number of shares (tokens), not USDC
    shares = round(size_usd / limit_px, 2)

    # Polymarket enforces a $1 minimum notional — bump if needed
    if limit_px * shares < 1.0:
        shares = round(1.0 / limit_px + 0.01, 2)

    log.info(
        'Placing order: token=…%s  price=%.2f  shares=%.2f  (~$%.0f)',
        token_id[-8:], limit_px, shares, limit_px * shares,
    )

    clob = _get_clob()

    # Diagnostic: log the values the CLOB API returns for this token before signing
    try:
        tick_size = clob.get_tick_size(token_id)
        neg_risk  = clob.get_neg_risk(token_id)
        fee_bps   = clob.get_fee_rate_bps(token_id)
        log.info('CLOB meta: token=…%s  tick=%s  neg_risk=%s  fee_bps=%s',
                 token_id[-8:], tick_size, neg_risk, fee_bps)
    except Exception as e:
        log.warning('CLOB meta lookup failed: %s', e)

    order_args = OrderArgs(
        token_id=token_id,
        price=limit_px,
        size=shares,
        side='BUY',
    )
    signed = clob.create_order(order_args)
    log.info('signed order: %s', json.dumps(signed.dict(), separators=(',', ':')))
    resp = clob.post_order(signed, OrderType.GTC)

    log.debug('CLOB raw response: %s', resp)

    order_id = (
        resp.get('orderID') or resp.get('order_id') or
        (resp.get('order') or {}).get('id') or ''
    )
    if not order_id:
        raise RuntimeError(f'CLOB returned no order ID: {resp}')

    log.info('Order placed: %s  price=%.2f  shares=%.2f', order_id[:16], limit_px, shares)
    return {'order_id': order_id, 'limit_price': limit_px, 'shares': shares}


def get_order_status(order_id: str) -> dict:
    """
    Returns {'status': str, 'size_matched': float, 'avg_price': float}.
    Possible statuses: live, matched, canceled, delayed, error.
    """
    try:
        resp = _get_clob().get_order(order_id)
        return {
            'status':       resp.get('status', 'unknown'),
            'size_matched': float(resp.get('size_matched') or 0),
            'avg_price':    float(
                resp.get('average_price') or resp.get('price') or 0
            ),
        }
    except Exception as e:
        log.error('get_order_status(%s): %s', order_id[:16], e)
        return {'status': 'error', 'size_matched': 0, 'avg_price': 0}


def cancel_order(order_id: str) -> bool:
    try:
        _get_clob().cancel({'orderID': order_id})
        log.info('Cancelled order: %s', order_id[:16])
        return True
    except Exception as e:
        log.error('cancel_order(%s): %s', order_id[:16], e)
        return False
