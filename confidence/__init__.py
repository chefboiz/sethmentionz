import logging
from datetime import datetime, timezone

import analysis_state
import db
from config import LLM_WEIGHT, SIGNAL_WEIGHT, CONFIDENCE_THRESHOLD
from confidence.llm_leg import score as llm_score_fn
from confidence.signal_leg import get_source

log = logging.getLogger(__name__)

_signal_source = None


def _get_signal_source():
    global _signal_source
    if _signal_source is None:
        _signal_source = get_source()
    return _signal_source


def run_confidence() -> None:
    """Scheduled job — no-op while analysis is paused, so no LLM spend happens
    automatically. See analysis_state.py and telegram/callbacks.py `analyze`."""
    if analysis_state.is_paused():
        log.debug('Confidence run skipped — analysis paused')
        return
    score_pending_markets()


def score_pending_markets() -> None:
    log.info('=== Confidence run started ===')

    markets = db.fetchall("""
        SELECT market_id, question, subject, phrase_topic, context,
               resolution_criteria_summary, resolution_deadline
        FROM mention_markets
        WHERE resolved = FALSE AND archived = FALSE
        ORDER BY resolution_deadline ASC
    """)

    if not markets:
        log.info('No active mention markets to score')
        return

    log.info('Scoring %d market(s)', len(markets))
    sig_src = _get_signal_source()
    scored  = 0

    for market in markets:
        mid = market['market_id']
        try:
            llm_result = llm_score_fn(market)
            llm_val    = llm_result['probability']

            if llm_val is None:
                log.warning('LLM leg returned None for %s — skipping', mid)
                continue

            sig_val = sig_src.score(
                market.get('subject') or '',
                market.get('phrase_topic') or '',
            )

            blended = round(LLM_WEIGHT * llm_val + SIGNAL_WEIGHT * sig_val, 4)

            db.execute("""
                INSERT INTO mention_signals (
                    market_id, llm_score, signal_score, blended_score,
                    llm_weight, signal_weight, llm_reasoning, llm_context_confidence,
                    scored_at
                ) VALUES (
                    %(market_id)s, %(llm_score)s, %(signal_score)s, %(blended_score)s,
                    %(llm_weight)s, %(signal_weight)s, %(llm_reasoning)s,
                    %(llm_context_confidence)s, %(scored_at)s
                )
            """, {
                'market_id':             mid,
                'llm_score':             round(llm_val, 4),
                'signal_score':          round(sig_val, 4),
                'blended_score':         blended,
                'llm_weight':            LLM_WEIGHT,
                'signal_weight':         SIGNAL_WEIGHT,
                'llm_reasoning':         llm_result['reasoning'],
                'llm_context_confidence': llm_result['context_confidence'],
                'scored_at':             datetime.now(timezone.utc).isoformat(),
            })

            scored += 1
            log.info('  %s | llm=%.3f  sig=%.3f  blend=%.3f  [ctx:%s]',
                     mid[:14], llm_val, sig_val, blended,
                     llm_result['context_confidence'])

        except Exception:
            log.exception('Confidence failed for %s', mid)

    log.info('=== Confidence complete | scored: %d / %d ===', scored, len(markets))
