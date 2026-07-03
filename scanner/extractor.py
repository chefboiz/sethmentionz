import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY

log = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
Extract structured fields from this Polymarket mention market.
Respond with a JSON object only — no markdown fences.

Market question: {question}
Description / resolution criteria: {description}

Return exactly:
{{
  "subject": "the person or entity expected to make the speech act",
  "phrase_topic": "the specific word, phrase, or topic to be mentioned",
  "context": "one of: speech|tweet|interview|debate|post|press conference|hearing|other",
  "resolution_criteria_summary": "one sentence describing exactly how this resolves"
}}"""

_claude: anthropic.Anthropic | None = None


def _get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude


_EMPTY = {
    'subject': '',
    'phrase_topic': '',
    'context': 'other',
    'resolution_criteria_summary': '',
}


def extract_fields(market: dict) -> dict:
    description = (market.get('description') or market.get('resolution_source') or '')[:600]
    prompt = _EXTRACT_PROMPT.format(
        question=market.get('question', ''),
        description=description,
    )

    for attempt in range(2):
        try:
            msg = _get_claude().messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=200,
                messages=[{'role': 'user', 'content': prompt}],
            )
            parsed = json.loads(msg.content[0].text.strip())
            return {
                'subject':                    parsed.get('subject', ''),
                'phrase_topic':               parsed.get('phrase_topic', ''),
                'context':                    parsed.get('context', 'other'),
                'resolution_criteria_summary': parsed.get('resolution_criteria_summary', ''),
            }
        except json.JSONDecodeError:
            if attempt == 0:
                log.debug('Extractor: malformed JSON, retrying for "%s"',
                          market.get('question', '')[:60])
                continue
            log.warning('Extractor: gave up on "%s"', market.get('question', '')[:70])
        except Exception as e:
            log.error('Extractor error: %s', e)
            break

    return _EMPTY.copy()
