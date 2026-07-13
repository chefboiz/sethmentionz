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
    for i, p in enumerate(prices):
        if p >= 0.99:
            label = str(outcomes[i]).upper() if i < len(outcomes) else ''
            return label if label in ('YES', 'NO') else ('YES' if i == 0 else 'NO')
    return None


def _fetch_market(market_id: str) -> dict | None:
    # The /markets/{id} path form only accepts Gamma's internal numeric id and
    # returns 422 for a conditionId once the market is archived. The list form
    # with condition_ids works for both open and archived/closed markets.
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f'{POLYMARKET_API_URL}/markets',
                      params={'condition_ids': market_id, 'closed': 'true'})
            r.raise_for_status()
            results = r.json()
            return results[0] if results else None
    except Exception as e:
        log.warning('Gamma fetch failed for %s: %s', market_id[:14], e)
        return None


def run_resolution_check() -> None:
    log.info('=== Resolution check started ===')

    # Markets that have trades but aren't marked resolved
    unresolved = db.fetchall("""
        SELECT DISTINCT mm.market_id
        FROM mention_markets mm
        JOIN mention_trades mt ON mt.market_id = mm.market_id
        WHERE mm.resolved = FALSE
    """)

    if not unresolved:
        log.info('No unresolved traded markets')
        return

    to_check = [r['market_id'] for r in unresolved]
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

        db.execute("""
            UPDATE mention_markets SET
                resolved           = TRUE,
                resolution_outcome = %s,
                last_updated       = %s
            WHERE market_id = %s
        """, (outcome, now_iso, mid))

        trades = db.fetchall("""
            SELECT id, side FROM mention_trades WHERE market_id = %s
        """, (mid,))

        for trade in trades:
            won = (str(trade.get('side', 'YES')).upper() == outcome)
            db.execute("""
                UPDATE mention_trades SET
                    actual_outcome = %s,
                    won            = %s,
                    resolved_at    = %s
                WHERE id = %s
            """, (outcome, won, now_iso, trade['id']))

        resolved_n += 1
        log.info('Resolved: %s → %s  (%d trade(s) updated)', mid[:14], outcome, len(trades))
        time.sleep(0.2)

    log.info('=== Resolution check complete | resolved: %d ===', resolved_n)
