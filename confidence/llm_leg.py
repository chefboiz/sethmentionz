import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_PROMPT = """\
You are a prediction market analyst specialising in speech-act markets — markets that \
resolve on whether a specific person will say, mention, tweet, post, or otherwise utter \
a specific word, phrase, or topic.

Market question: {question}
Subject (who): {subject}
Phrase / topic (what): {phrase_topic}
Context (where / medium): {context}
Resolution criteria: {resolution_criteria}
Resolution deadline (UTC): {deadline}

Using everything you know about this subject's communication patterns, scheduled events, \
recent public statements, and the relevance of the phrase/topic to their current agenda, \
estimate the probability (0.0–1.0) that this speech act will occur before the deadline.

Respond with a JSON object only — no markdown fences, no surrounding text:
{{
  "probability": 0.XX,
  "reasoning": "one concise paragraph explaining the key factors driving your estimate",
  "context_confidence": "low|medium|high"
}}

context_confidence reflects how much relevant, up-to-date information you actually have \
to make this call — not the probability itself. Use "low" when you are missing recent \
context (e.g. you don't know what happened in the last few days), "high" when you have \
strong grounding."""

_claude: anthropic.Anthropic | None = None


def _get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude


def score(market: dict) -> dict:
    """
    Returns dict with keys:
      probability (float | None), reasoning (str), context_confidence (str)
    """
    prompt = _PROMPT.format(
        question=market.get('question', ''),
        subject=market.get('subject') or 'Unknown',
        phrase_topic=market.get('phrase_topic') or 'Unknown',
        context=market.get('context') or 'unknown',
        resolution_criteria=(market.get('resolution_criteria_summary') or '')[:400],
        deadline=market.get('resolution_deadline') or 'unknown',
    )

    for attempt in range(2):
        try:
            msg = _get_claude().messages.create(
                model='claude-sonnet-4-6',
                max_tokens=400,
                messages=[{'role': 'user', 'content': prompt}],
            )
            text = msg.content[0].text.strip()
            parsed = json.loads(text)

            prob = float(parsed['probability'])
            if not 0.0 <= prob <= 1.0:
                raise ValueError(f'probability out of range: {prob}')

            return {
                'probability':        prob,
                'reasoning':          str(parsed.get('reasoning', '')),
                'context_confidence': str(parsed.get('context_confidence', 'medium')),
            }
        except (json.JSONDecodeError, KeyError, ValueError, IndexError) as e:
            if attempt == 0:
                log.debug('LLM leg: parse error (%s), retrying "%s"',
                          e, market.get('question', '')[:60])
                continue
            log.warning('LLM leg: giving up on "%s"', market.get('question', '')[:70])
        except anthropic.APIError as e:
            log.error('LLM leg: API error: %s', e)
            break

    return {'probability': None, 'reasoning': '', 'context_confidence': 'low'}
