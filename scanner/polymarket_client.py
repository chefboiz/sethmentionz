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


_MAX_PAGES = 20   # hard backstop — should never be reached in normal operation


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
    Fetch Polymarket markets sorted ascending by endDateIso, stopping as soon as
    the page's last market falls past now + window_hours.

    end_date_min/end_date_max are NOT sent — the API ignores them as a filter
    (confirmed: 20 pages all came back full regardless). The ascending sort IS
    honoured, so we can stop early once we've passed the window ceiling.
    Client-side _within_window in scanner/__init__.py is the authoritative filter.
    """
    now       = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)

    base_params = {
        'closed':    'false',
        'active':    'true',
        'limit':     _PAGE_CAP,
        'order':     'endDateIso',
        'ascending': 'true',
    }

    log.info('Gamma API query: active+closed=false ascending endDateIso  window_end=%s  max_pages=%d',
             window_end.strftime('%Y-%m-%dT%H:%M:%SZ'), _MAX_PAGES)

    markets: list[dict] = []
    offset      = 0
    page_num    = 0
    first_logged = False

    with httpx.Client(timeout=15, headers=_HEADERS) as client:
        while page_num < _MAX_PAGES:
            page_num += 1
            params = {**base_params, 'offset': offset}

            page, stop = _fetch_page(client, f'{POLYMARKET_API_URL}/markets', params, page_num)

            if not page:
                if not stop:
                    log.info('  page %d (offset=%d): empty — done', page_num, offset)
                break

            first_end = page[0].get('endDateIso') or page[0].get('end_date_iso', '')
            last_end  = page[-1].get('endDateIso') or page[-1].get('end_date_iso', '')
            log.info('  page %d (offset=%d): %d markets  end_date [%s … %s]',
                     page_num, offset, len(page),
                     first_end[:16], last_end[:16])

            # Log the very first market's end_date once per scan
            if not first_logged and page:
                first_logged = True
                log.info('  → nearest market end_date: %s', first_end)

            for m in page:
                normalized = _normalize(m)
                if normalized:
                    markets.append(normalized)

            if stop:
                break

            # Early-exit: results are sorted ascending — if the last item on this page
            # is already past the window ceiling, nothing further can be in-window.
            last_dt = _parse_end_date(last_end)
            if last_dt and last_dt > window_end:
                log.info('  page %d last market (%s) past window_end — stopping early',
                         page_num, last_end[:16])
                break

            if len(page) < _PAGE_CAP:
                break

            offset += _PAGE_CAP
            time.sleep(0.25)

        else:
            log.warning('Gamma API: hit %d-page safety cap — something may be wrong',
                        _MAX_PAGES)

    log.info('Gamma API: %d raw markets across %d page(s) — client-side window filter next',
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
