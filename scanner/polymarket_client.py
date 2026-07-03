import json
import logging
import time
import httpx
from config import POLYMARKET_API_URL

log = logging.getLogger(__name__)

_HEADERS = {'Content-Type': 'application/json'}


def _parse_json_field(val) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return []
    return val if isinstance(val, list) else []


def _normalize(m: dict) -> dict:
    outcomes  = _parse_json_field(m.get('outcomes'))
    prices    = _parse_json_field(m.get('outcomePrices'))
    clob_ids  = _parse_json_field(m.get('clobTokenIds'))

    yes_idx = next((i for i, o in enumerate(outcomes) if str(o).upper() == 'YES'), None)
    no_idx  = next((i for i, o in enumerate(outcomes) if str(o).upper() == 'NO'),  None)

    def _price(idx, fallback_idx):
        if idx is not None and idx < len(prices):
            return float(prices[idx])
        if fallback_idx < len(prices):
            return float(prices[fallback_idx])
        return None

    return {
        'id':               m.get('conditionId') or m.get('id'),
        'slug':             m.get('slug'),
        'question':         m.get('question', ''),
        'description':      m.get('description', ''),
        'resolution_source': m.get('resolutionSource', ''),
        'yes_price':        _price(yes_idx, 0),
        'no_price':         _price(no_idx,  1),
        'end_date':         m.get('endDateIso') or m.get('end_date_iso'),
        'closed':           m.get('closed', False),
        'clob_token_ids':   clob_ids,
        'volume':           m.get('volume'),
        'liquidity':        m.get('liquidity'),
    }


def fetch_active_markets(page_size: int = 200, max_pages: int = 25) -> list[dict]:
    """Paginate through all open Polymarket markets, ordered by soonest end date first."""
    markets: list[dict] = []
    offset = 0

    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        for _ in range(max_pages):
            params = {
                'closed':    'false',
                'limit':     page_size,
                'offset':    offset,
                'order':     'endDateIso',
                'ascending': 'true',
            }
            raw = _get_with_backoff(client, f'{POLYMARKET_API_URL}/markets', params)
            page = raw if isinstance(raw, list) else raw.get('markets', [])

            if not page:
                break

            markets.extend(_normalize(m) for m in page)

            if len(page) < page_size:
                break

            offset += page_size
            time.sleep(0.25)

    log.info('Gamma API: fetched %d open markets across %d pages',
             len(markets), max(1, offset // page_size + 1))
    return markets


def _get_with_backoff(client: httpx.Client, url: str, params: dict) -> dict | list:
    for attempt in range(3):
        try:
            r = client.get(url, params=params)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log.warning('Rate limited — retrying in %ds', wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except httpx.RequestError as e:
            if attempt == 2:
                raise
            log.warning('Request error (%s), retrying', e)
            time.sleep(2 ** attempt)
    return []
