import json
import logging
import time
from datetime import datetime, timezone, timedelta

import httpx

from config import POLYMARKET_API_URL, RESOLUTION_WINDOW_HOURS

log = logging.getLogger(__name__)

_HEADERS  = {'Content-Type': 'application/json'}
_PAGE_CAP = 100   # Gamma API ignores limit values above 100


def _parse_json_field(val) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return []
    return val if isinstance(val, list) else []


def _normalize(m: dict) -> dict:
    outcomes = _parse_json_field(m.get('outcomes'))
    prices   = _parse_json_field(m.get('outcomePrices'))
    clob_ids = _parse_json_field(m.get('clobTokenIds'))

    yes_idx = next((i for i, o in enumerate(outcomes) if str(o).upper() == 'YES'), None)
    no_idx  = next((i for i, o in enumerate(outcomes) if str(o).upper() == 'NO'),  None)

    def _price(idx, fallback_idx):
        if idx is not None and idx < len(prices):
            return float(prices[idx])
        if fallback_idx < len(prices):
            return float(prices[fallback_idx])
        return None

    market_id = m.get('conditionId') or m.get('id')
    if not market_id:
        return None

    def _to_float(val):
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    return {
        'id':                market_id,
        'slug':              m.get('slug'),
        'question':          m.get('question', ''),
        'description':       m.get('description', ''),
        'resolution_source': m.get('resolutionSource', ''),
        'yes_price':         _price(yes_idx, 0),
        'no_price':          _price(no_idx,  1),
        'end_date':          m.get('endDateIso') or m.get('end_date_iso'),
        'closed':            m.get('closed', False),
        'clob_token_ids':    clob_ids,
        'volume':            _to_float(m.get('volume')),
        'volume24hr':        _to_float(m.get('volume24hr') or m.get('volume_24hr')),
        'liquidity':         _to_float(m.get('liquidity') or m.get('liquidityClob')),
    }


def fetch_active_markets(window_hours: int = RESOLUTION_WINDOW_HOURS) -> list[dict]:
    """
    Fetch Polymarket markets whose end date falls within [now, now + window_hours].

    Uses server-side date-range filtering to avoid the stale-oracle-resolution problem
    (old unresolved markets sorting to the front of an ascending endDateIso page).
    Falls back to a client-side _within_window check as a defensive double-check.
    """
    now     = datetime.now(timezone.utc)
    end_min = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_max = (now + timedelta(hours=window_hours)).strftime('%Y-%m-%dT%H:%M:%SZ')

    base_params = {
        'closed':        'false',
        'active':        'true',
        'end_date_min':  end_min,
        'end_date_max':  end_max,
        'limit':         _PAGE_CAP,
        'order':         'endDateIso',
        'ascending':     'true',
    }

    log.info('Gamma API query: end_date in [%s, %s]  window=%dh',
             end_min, end_max, window_hours)

    markets: list[dict] = []
    offset   = 0
    page_num = 0

    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        while True:
            page_num += 1
            params = {**base_params, 'offset': offset}
            raw    = _get_with_backoff(client, f'{POLYMARKET_API_URL}/markets', params)
            page   = raw if isinstance(raw, list) else raw.get('markets', [])

            log.info('  page %d (offset=%d): %d raw markets', page_num, offset, len(page))

            for m in page:
                normalized = _normalize(m)
                if normalized:
                    markets.append(normalized)

            if len(page) < _PAGE_CAP:
                break   # last page

            offset += _PAGE_CAP
            time.sleep(0.25)

    log.info('Gamma API: fetched %d markets in window across %d page(s)',
             len(markets), page_num)
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
