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


_MAX_PAGES = 20   # safety cap — an 18h window should never need anywhere near this


def fetch_active_markets(window_hours: int = RESOLUTION_WINDOW_HOURS) -> list[dict]:
    """
    Fetch Polymarket markets whose end date falls within [now, now + window_hours].

    Stops when a page returns fewer than _PAGE_CAP results, after _MAX_PAGES pages,
    or when any page fetch returns an error (processes whatever was already fetched).
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

    log.info('Gamma API query: end_date in [%s, %s]  window=%dh  max_pages=%d',
             end_min, end_max, window_hours, _MAX_PAGES)

    markets: list[dict] = []
    offset   = 0
    page_num = 0

    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        while page_num < _MAX_PAGES:
            page_num += 1
            params = {**base_params, 'offset': offset}

            page, stop = _fetch_page(client, f'{POLYMARKET_API_URL}/markets', params, page_num)

            log.info('  page %d (offset=%d): %d raw markets', page_num, offset, len(page))

            for m in page:
                normalized = _normalize(m)
                if normalized:
                    markets.append(normalized)

            if stop or len(page) < _PAGE_CAP:
                break

            offset += _PAGE_CAP
            time.sleep(0.25)

        else:
            log.warning('Gamma API: hit %d-page safety cap at offset=%d — stopping',
                        _MAX_PAGES, offset)

    log.info('Gamma API: %d markets collected across %d page(s)  [end_date %s → %s]',
             len(markets), page_num, end_min, end_max)
    return markets


def _fetch_page(client: httpx.Client, url: str, params: dict,
                page_num: int) -> tuple[list, bool]:
    """
    Fetch one page. Returns (page_list, stop).
    stop=True means the caller should not request the next page
    (either an error occurred or the API returned nothing useful).
    """
    for attempt in range(3):
        try:
            r = client.get(url, params=params)

            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log.warning('Rate limited on page %d — retrying in %ds', page_num, wait)
                time.sleep(wait)
                continue

            if r.status_code >= 400:
                log.warning(
                    'Gamma API %s on page %d (offset=%s) — stopping pagination. '
                    'Processed pages before this are still usable.',
                    r.status_code, page_num, params.get('offset', 0),
                )
                return [], True

            r.raise_for_status()
            raw  = r.json()
            page = raw if isinstance(raw, list) else raw.get('markets', [])
            return page, False

        except httpx.RequestError as e:
            if attempt == 2:
                log.warning('Request error on page %d: %s — stopping pagination', page_num, e)
                return [], True
            log.warning('Request error on page %d (%s), retrying', page_num, e)
            time.sleep(2 ** attempt)

    return [], True
