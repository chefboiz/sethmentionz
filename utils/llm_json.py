import json
import logging
import re

log = logging.getLogger(__name__)


def parse_llm_json(text: str, caller: str = '') -> dict:
    """
    Parse a JSON object from a model response that may be wrapped in markdown
    code fences or have stray preamble/postamble text.

    Strategy:
      1. Strip ```json / ``` fences.
      2. Try json.loads on the stripped text.
      3. Fall back to extracting the first {...} block via regex.

    Raises json.JSONDecodeError (with the raw text logged at DEBUG) if nothing parses.
    """
    label = f'[{caller}] ' if caller else ''

    # 1. Strip markdown code fences
    stripped = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.I)
    stripped = re.sub(r'\s*```$', '', stripped.strip())
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 2. Find the first {...} block (handles preamble/postamble)
    m = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    log.debug('%sJSON parse failed — raw response: %r', label, text[:400])
    raise json.JSONDecodeError('no valid JSON object found in LLM response', text, 0)
