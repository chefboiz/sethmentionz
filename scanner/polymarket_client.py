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
        'end_date':          m.get('endDate') or m.get('endDateIso') or m.get('end_date_iso'),
        'closed':            m.get('closed', False),
        'clob_token_ids':    clob_ids,
        'volume':            _to_float(m.get('volume')),
        'volume24hr':        _to_float(m.get('volume24hr') or m.get('volume_24hr')),
        'liquidity':         _to_float(m.get('liquidity') or m.get('liquidityClob')),
    }


# Confirmed by live testing 2026-07-04:
# - end_date_min/end_date_max as ISO strings are silently ignored by the API.
# - end_date_min/end_date_max as Unix epoch integers (seconds) DO filter server-side.
# - An 18h window produces ~2100 markets across ~21 pages on a typical day, all in-window.
# - endDateIso is date-only, so within-day sort order is non-deterministic — early-exit
#   on last_end > window_end only helps for multi-day windows.
_MAX_PAGES = 25   # 2100 markets / 100 per page = 21 pages; 25 gives comfortable headroom


def _parse_end_date(end_date_str: str | None) -> datetime | None:
    if not end_date_str:
        return None
    try:
        dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def fetch_active_markets(window_hours: int = RESOLUTION_WINDOW_HOURS) -> list[dict]:
    """
    Fetch Polymarket markets resolving within the next window_hours.

    end_date_min/end_date_max MUST be sent as Unix epoch integers — ISO strings are
    silently ignored by the API. Client-side _within_window is still the authoritative
    filter; the epoch params cut the result set from tens of thousands to ~2100.
    """
    now        = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    epoch_min  = int(now.timestamp())
    epoch_max  = int(window_end.timestamp())

    base_params = {
        'active':        'true',
        'closed':        'false',
        'end_date_min':  epoch_min,
        'end_date_max':  epoch_max,
        'limit':         _PAGE_CAP,
        'order':         'endDateIso',
        'ascending':     'true',
    }

    log.info('Gamma API: epoch window %d-%d (%s to %s)  max_pages=%d',
             epoch_min, epoch_max,
             now.strftime('%Y-%m-%dT%H:%M:%SZ'),
             window_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
             _MAX_PAGES)

    markets: list[dict] = []
    offset       = 0
    page_num     = 0
    first_logged = False

    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        while page_num < _MAX_PAGES:
            page_num += 1
            params = {**base_params, 'offset': offset}

            page, stop = _fetch_page(client, f'{POLYMARKET_API_URL}/markets', params, page_num)

            if not page:
                if not stop:
                    log.info('  page %d (offset=%d): empty -- done', page_num, offset)
                break

            first_end = (page[0].get('endDate') or page[0].get('endDateIso') or '')[:19]
            last_end  = (page[-1].get('endDate') or page[-1].get('endDateIso') or '')[:19]
            log.info('  page %d (offset=%d): %d markets  endDate [%s ... %s]',
                     page_num, offset, len(page), first_end, last_end)

            if not first_logged:
                first_logged = True
                log.info('  nearest market endDate: %s', first_end)

            for m in page:
                normalized = _normalize(m)
                if normalized:
                    markets.append(normalized)

            if stop:
                break

            # Early-exit when sort pushes us past window_end (effective for multi-day windows)
            last_dt = _parse_end_date(last_end)
            if last_dt and last_dt > window_end:
                log.info('  page %d last market past window_end -- stopping early', page_num)
                break

            if len(page) < _PAGE_CAP:
                break

            offset += _PAGE_CAP
            time.sleep(0.25)

        else:
            log.warning('Gamma API: hit %d-page safety cap at offset=%d -- investigate',
                        _MAX_PAGES, offset)

    log.info('Gamma API: %d raw markets across %d page(s) -- client-side window filter next',
             len(markets), page_num)
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
