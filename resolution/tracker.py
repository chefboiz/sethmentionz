import json
import logging
import time
from datetime import datetime, timezone

import httpx

import db
from config import POLYMARKET_API_URL

log = logging.getLogger(__name__)


def _parse_prices(val) -> list[float]:
    if isinstance(val, str):
        try:
            return [float(x) for x in json.loads(val)]
        except Exception:
            return []
    return [float(x) for x in (val or [])] if val else []


def _parse_outcomes(val) -> list[str]:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return list(val or [])


def _resolve_from_prices(outcomes: list[str], prices: list[float]) -> str | None:
    """
    Return 'YES' or 'NO' based on which outcome price resolved to ~1.0.
    Polymarket sets the winning side to 1.0 and losing side to 0.0 on resolution.
    """
    for i, p in enumerate(prices):
        if p >= 0.99:
            label = str(outcomes[i]).upper() if i < len(outcomes) else ''
            return label if label in ('YES', 'NO') else ('YES' if i == 0 else 'NO')
    return None


def _fetch_market(market_id: str) -> dict | None:
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f'{POLYMARKET_API_URL}/markets/{market_id}')
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning('Gamma fetch failed for %s: %s', market_id[:14], e)
        return None


def run_resolution_check() -> None:
    """
    For each unresolved mention market that has at least one trade, check the Gamma API
    for resolution. On resolution, store the outcome and mark every trade as won/lost.
    """
    log.info('=== Resolution check started ===')
    client = db.get_client()

    # Markets that have trades but aren't marked resolved
    unresolved = (
        client.table('mention_markets')
        .select('market_id')
        .eq('resolved', False)
        .execute()
    ).data or []

    traded_ids = {
        r['market_id'] for r in (
            client.table('mention_trades')
            .select('market_id')
            .execute()
        ).data or []
    }

    to_check = [m['market_id'] for m in unresolved if m['market_id'] in traded_ids]

    if not to_check:
        log.info('No unresolved traded markets')
        return

    log.info('Checking %d market(s)', len(to_check))
    resolved_n = 0

    for mid in to_check:
        raw = _fetch_market(mid)
        if not raw:
            continue

        if not raw.get('closed', False):
            continue

        outcomes = _parse_outcomes(raw.get('outcomes'))
        prices   = _parse_prices(raw.get('outcomePrices'))
        outcome  = _resolve_from_prices(outcomes, prices)

        if not outcome:
            log.warning('Closed but outcome unclear for %s (prices=%s)', mid[:14], prices)
            continue

        now_iso = datetime.now(timezone.utc).isoformat()

        client.table('mention_markets').update({
            'resolved':           True,
            'resolution_outcome': outcome,
            'last_updated':       now_iso,
        }).eq('market_id', mid).execute()

        trades = (
            client.table('mention_trades')
            .select('id, side')
            .eq('market_id', mid)
            .execute()
        ).data or []

        for trade in trades:
            won = (str(trade.get('side', 'YES')).upper() == outcome)
            client.table('mention_trades').update({
                'actual_outcome': outcome,
                'won':            won,
                'resolved_at':    now_iso,
            }).eq('id', trade['id']).execute()

        resolved_n += 1
        log.info('Resolved: %s → %s  (%d trade(s) updated)', mid[:14], outcome, len(trades))
        time.sleep(0.2)

    log.info('=== Resolution check complete | resolved: %d ===', resolved_n)
