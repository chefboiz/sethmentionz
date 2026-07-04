import logging
import time
import httpx

log = logging.getLogger(__name__)

CLOB_BASE = 'https://clob.polymarket.com'


def fetch_price(token_id: str, side: str = 'BUY') -> float | None:
    """
    Fetch the accurate real-time price for one token via GET /price.

    /book returns stale placeholder data (~0.01 bid / ~0.99 ask) for many active
    markets (known Polymarket API bug). /price is the authoritative source for
    the current best price and must be used for edge calculations.

    Returns the price as a float, or None on failure.
    """
    for attempt in range(3):
        try:
            with httpx.Client(timeout=8) as client:
                r = client.get(f'{CLOB_BASE}/price',
                               params={'token_id': token_id, 'side': side})
                if r.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                return float(r.json()['price'])
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as e:
            if attempt == 2:
                log.warning('CLOB price %s: %s', token_id[:12], e)
                return None
            time.sleep(2 ** attempt)
    return None


def fetch_book(token_id: str) -> dict | None:
    """
    Fetch the full order book for one token.
    Returns {'asks': [{price, size}, ...], 'bids': [...]} or None on failure.
    Asks are sorted cheapest-first (best ask = asks[0]).
    Bids are sorted highest-first (best bid = bids[0]).
    """
    for attempt in range(3):
        try:
            with httpx.Client(timeout=8) as client:
                r = client.get(f'{CLOB_BASE}/book', params={'token_id': token_id})
                if r.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                return {
                    'asks': [
                        {'price': float(lvl['price']), 'size': float(lvl['size'])}
                        for lvl in data.get('asks', [])
                    ],
                    'bids': [
                        {'price': float(lvl['price']), 'size': float(lvl['size'])}
                        for lvl in data.get('bids', [])
                    ],
                }
        except httpx.HTTPStatusError as e:
            if attempt == 2:
                log.warning('CLOB book %s: HTTP %s', token_id[:12], e.response.status_code)
                return None
            time.sleep(2 ** attempt)
        except httpx.RequestError as e:
            if attempt == 2:
                log.warning('CLOB book %s: %s', token_id[:12], e)
                return None
            time.sleep(2 ** attempt)
    return None
