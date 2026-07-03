import logging
from datetime import datetime, timezone, timedelta

from config import RESOLUTION_WINDOW_HOURS
import db
from scanner.polymarket_client import fetch_active_markets
from scanner.mention_filter import is_mention_market
from scanner.extractor import extract_fields

log = logging.getLogger(__name__)


def _within_window(end_date: str | None) -> bool:
    if not end_date:
        return False
    try:
        dt  = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        return now <= dt <= now + timedelta(hours=RESOLUTION_WINDOW_HOURS)
    except ValueError:
        return False


def run_scan() -> None:
    log.info('=== Scanner run started ===')
    client = db.get_client()

    all_markets = fetch_active_markets()
    total = len(all_markets)

    in_window = [m for m in all_markets if _within_window(m.get('end_date'))]
    discarded_by_window = total - len(in_window)

    log.info('Fetched %d | outside %dh window: %d | checking: %d',
             total, RESOLUTION_WINDOW_HOURS, discarded_by_window, len(in_window))

    upserted = 0
    for market in in_window:
        question    = market.get('question', '')
        description = market.get('description', '') or ''

        if not is_mention_market(question, description):
            continue

        fields = extract_fields(market)
        now    = datetime.now(timezone.utc).isoformat()

        row = {
            'market_id':                    market['id'],
            'slug':                         market.get('slug'),
            'question':                     question,
            'description':                  description[:2000],
            'resolution_source':            market.get('resolution_source', ''),
            'subject':                      fields['subject'],
            'phrase_topic':                 fields['phrase_topic'],
            'context':                      fields['context'],
            'resolution_criteria_summary':  fields['resolution_criteria_summary'],
            'resolution_deadline':          market.get('end_date'),
            'yes_price':                    market.get('yes_price'),
            'no_price':                     market.get('no_price'),
            'clob_token_ids':               market.get('clob_token_ids', []),
            'last_updated':                 now,
        }

        client.table('mention_markets').upsert(row, on_conflict='market_id').execute()
        upserted += 1
        log.info('  [%s] %s | YES %.2f  NO %.2f',
                 fields['context'], question[:80],
                 market.get('yes_price') or 0, market.get('no_price') or 0)

    # Archive rows whose deadline has now passed
    now_iso = datetime.now(timezone.utc).isoformat()
    client.table('mention_markets') \
        .update({'archived': True, 'last_updated': now_iso}) \
        .lt('resolution_deadline', now_iso) \
        .eq('archived', False) \
        .execute()

    log.info('=== Scan complete | mention markets upserted: %d ===', upserted)
