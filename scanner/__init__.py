import logging
from datetime import datetime, timezone, timedelta

from psycopg2.extras import Json

import db
from config import RESOLUTION_WINDOW_HOURS
from scanner.polymarket_client import fetch_active_markets
from scanner.mention_filter import is_mention_market
from scanner.extractor import extract_fields

log = logging.getLogger(__name__)


def _within_window(end_date: str | None) -> bool:
    if not end_date:
        return False
    try:
        dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return now <= dt <= now + timedelta(hours=RESOLUTION_WINDOW_HOURS)
    except ValueError:
        return False


def run_scan() -> None:
    log.info('=== Scanner run started ===')

    all_markets = fetch_active_markets()
    total       = len(all_markets)
    in_window   = [m for m in all_markets if _within_window(m.get('end_date'))]

    log.info('Fetched %d | outside %dh window: %d | checking: %d',
             total, RESOLUTION_WINDOW_HOURS, total - len(in_window), len(in_window))

    upserted = 0
    for market in in_window:
        question    = market.get('question', '')
        description = market.get('description', '') or ''

        if not is_mention_market(question, description):
            continue

        fields  = extract_fields(market)
        now_iso = datetime.now(timezone.utc).isoformat()

        db.execute("""
            INSERT INTO mention_markets (
                market_id, slug, question, description, resolution_source,
                subject, phrase_topic, context, resolution_criteria_summary,
                resolution_deadline, yes_price, no_price, clob_token_ids,
                volume24hr, liquidity, last_updated
            ) VALUES (
                %(market_id)s, %(slug)s, %(question)s, %(description)s, %(resolution_source)s,
                %(subject)s, %(phrase_topic)s, %(context)s, %(resolution_criteria_summary)s,
                %(resolution_deadline)s, %(yes_price)s, %(no_price)s, %(clob_token_ids)s,
                %(volume24hr)s, %(liquidity)s, %(last_updated)s
            )
            ON CONFLICT (market_id) DO UPDATE SET
                slug                        = EXCLUDED.slug,
                question                    = EXCLUDED.question,
                description                 = EXCLUDED.description,
                resolution_source           = EXCLUDED.resolution_source,
                subject                     = EXCLUDED.subject,
                phrase_topic                = EXCLUDED.phrase_topic,
                context                     = EXCLUDED.context,
                resolution_criteria_summary = EXCLUDED.resolution_criteria_summary,
                resolution_deadline         = EXCLUDED.resolution_deadline,
                yes_price                   = EXCLUDED.yes_price,
                no_price                    = EXCLUDED.no_price,
                clob_token_ids              = EXCLUDED.clob_token_ids,
                volume24hr                  = EXCLUDED.volume24hr,
                liquidity                   = EXCLUDED.liquidity,
                last_updated                = EXCLUDED.last_updated
        """, {
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
            'clob_token_ids':               Json(market.get('clob_token_ids', [])),
            'volume24hr':                   market.get('volume24hr'),
            'liquidity':                    market.get('liquidity'),
            'last_updated':                 now_iso,
        })

        upserted += 1
        log.info('  [%s] %s | YES %.2f  NO %.2f',
                 fields['context'], question[:80],
                 market.get('yes_price') or 0, market.get('no_price') or 0)

    # Archive rows whose deadline has passed
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute("""
        UPDATE mention_markets
        SET archived = TRUE, last_updated = %s
        WHERE resolution_deadline < %s AND archived = FALSE AND resolved = FALSE
    """, (now_iso, now_iso))

    log.info('=== Scan complete | upserted: %d ===', upserted)
