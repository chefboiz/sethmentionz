import logging
from abc import ABC, abstractmethod
from config import NEWS_API_KEY

log = logging.getLogger(__name__)


class SignalSource(ABC):
    @abstractmethod
    def score(self, subject: str, phrase_topic: str) -> float:
        """Return a momentum score in [0.0, 1.0] for this subject + phrase combination."""


class StubSignalSource(SignalSource):
    """Neutral placeholder — returns 0.5 until a real source is wired in."""

    def score(self, subject: str, phrase_topic: str) -> float:
        return 0.5


class NewsAPISignalSource(SignalSource):
    """
    Score based on recent article volume mentioning subject + phrase_topic.

    Normalization:
      0 articles  → 0.20  (absence of coverage ≠ won't happen)
      BASELINE    → 0.50  (average coverage)
      3×BASELINE  → 0.80  (high coverage)
    """

    BASELINE     = 8   # articles per LOOKBACK_DAYS considered average
    LOOKBACK_DAYS = 2

    def __init__(self) -> None:
        from newsapi import NewsApiClient
        self._api = NewsApiClient(api_key=NEWS_API_KEY)

    def score(self, subject: str, phrase_topic: str) -> float:
        from datetime import datetime, timezone, timedelta

        from_date = (
            datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS)
        ).strftime('%Y-%m-%d')

        query = f'"{subject}" "{phrase_topic}"' if phrase_topic else f'"{subject}"'

        try:
            resp  = self._api.get_everything(
                q=query,
                from_param=from_date,
                language='en',
                sort_by='relevancy',
                page_size=1,
            )
            count = resp.get('totalResults', 0)

            # linear scale: 0 → 0.2, 2×BASELINE → 0.8, capped at 0.8
            normalized = min(count / (self.BASELINE * 2), 1.0)
            result     = round(0.2 + 0.6 * normalized, 4)

            log.debug('NewsAPI "%s / %s": %d articles → %.2f',
                      subject, phrase_topic, count, result)
            return result

        except Exception as e:
            log.warning('NewsAPI score error (%s) — falling back to 0.5', e)
            return 0.5


def get_source() -> SignalSource:
    if NEWS_API_KEY:
        try:
            src = NewsAPISignalSource()
            log.info('Signal leg: NewsAPI active')
            return src
        except Exception as e:
            log.warning('NewsAPI init failed (%s) — using stub', e)
    log.info('Signal leg: stub (set NEWS_API_KEY to enable NewsAPI)')
    return StubSignalSource()
