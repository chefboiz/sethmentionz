import logging
from datetime import datetime, timezone

from config import LLM_WEIGHT, SIGNAL_WEIGHT
import db
from confidence import llm_leg, signal_leg

log = logging.getLogger(__name__)

_signal_source: signal_leg.SignalSource | None = None


def _get_signal_source() -> signal_leg.SignalSource:
    global _signal_source
    if _signal_source is None:
        _signal_source = signal_leg.get_source()
    return _signal_source


def run_confidence() -> None:
    log.info('=== Confidence run started ===')
    client = db.get_client()

    result = (
        client.table('mention_markets')
        .select(
            'market_id, question, subject, phrase_topic, context, '
            'resolution_criteria_summary, resolution_deadline'
        )
        .eq('resolved', False)
        .eq('archived', False)
        .execute()
    )
    markets = result.data or []

    if not markets:
        log.info('No active mention markets to score')
        return

    log.info('Scoring %d market(s)', len(markets))
    sig_src = _get_signal_source()
    scored  = 0

    for market in markets:
        mid = market['market_id']

        llm_result = llm_leg.score(market)
        llm_score  = llm_result['probability']

        if llm_score is None:
            log.warning('LLM leg returned None for %s — skipping', mid)
            continue

        sig_score = sig_src.score(
            market.get('subject') or '',
            market.get('phrase_topic') or '',
        )

        blended = round(LLM_WEIGHT * llm_score + SIGNAL_WEIGHT * sig_score, 4)

        client.table('mention_signals').insert({
            'market_id':              mid,
            'llm_score':              round(llm_score, 4),
            'signal_score':           round(sig_score, 4),
            'blended_score':          blended,
            'llm_weight':             LLM_WEIGHT,
            'signal_weight':          SIGNAL_WEIGHT,
            'llm_reasoning':          llm_result['reasoning'],
            'llm_context_confidence': llm_result['context_confidence'],
            'scored_at':              datetime.now(timezone.utc).isoformat(),
        }).execute()

        scored += 1
        log.info('  %s | llm=%.3f  sig=%.3f  blend=%.3f  [ctx:%s]',
                 mid[:14], llm_score, sig_score, blended,
                 llm_result['context_confidence'])

    log.info('=== Confidence complete | scored: %d / %d ===', scored, len(markets))
