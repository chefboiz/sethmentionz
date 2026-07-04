import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY
from utils.llm_json import parse_llm_json

log = logging.getLogger(__name__)

# Pass 1: cheap regex — catches the common surface forms
_PATTERNS = [
    re.compile(r'\bwill\b.{1,80}\b(say|mention|tweet|post|write|announce|utter|use the (word|phrase|term))\b', re.I),
    re.compile(r'\bdoes\b.{1,80}\b(mention|say|tweet|post|write)\b', re.I),
    re.compile(r'\b(mention|say|tweet|post|write)\b.{1,80}\bduring\b', re.I),
    re.compile(r'\bwill\b.{1,80}\b(bring up|reference|name-check|call out|shout out)\b', re.I),
    re.compile(r'\buse (the )?(word|phrase|term)\b', re.I),
    re.compile(r'\bwill .{1,40} (be mentioned|get a mention)\b', re.I),
]

_CLASSIFY_PROMPT = """\
You are classifying a Polymarket prediction market.
Respond with a JSON object only — no markdown fences, no explanation.

Market question: {question}
Description / resolution criteria: {description}

Is this market specifically about whether a person will SAY, MENTION, TWEET, POST, WRITE, \
or UTTER a specific word, phrase, name, or topic? The market must resolve based on a \
speech act — not on an outcome, price, vote, or election result.

False positives to reject:
- Legislation or bills named after a term (e.g. "the DOGE Act")
- Price or outcome markets that happen to contain a person's name
- Markets about whether a policy will pass or an event will happen

Return: {{"is_mention_market": true|false, "reason": "one short sentence"}}"""

_claude: anthropic.Anthropic | None = None


def _get_claude() -> anthropic.Anthropic:
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude


def _regex_pass(question: str) -> bool:
    return any(p.search(question) for p in _PATTERNS)


def _llm_pass(question: str, description: str) -> bool:
    prompt = _CLASSIFY_PROMPT.format(
        question=question,
        description=(description or '')[:500],
    )
    for attempt in range(2):
        try:
            msg = _get_claude().messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=100,
                messages=[{'role': 'user', 'content': prompt}],
            )
            return bool(parse_llm_json(msg.content[0].text, 'mention_filter').get('is_mention_market'))
        except json.JSONDecodeError:
            if attempt == 0:
                continue
            log.warning('LLM classification: gave up on "%s"', question[:70])
            return False
        except Exception as e:
            log.error('LLM classification error: %s', e)
            return False
    return False


def is_mention_market(question: str, description: str) -> bool:
    if not _regex_pass(question):
        return False
    return _llm_pass(question, description)
