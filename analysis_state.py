import logging

log = logging.getLogger(__name__)

# LLM confidence scoring (the Sonnet call) is paused by default -- the scheduled
# job still fires but is a no-op while paused, so no API spend happens until the
# user texts "analyze" for a one-shot batch run.
_paused: bool = True


def is_paused() -> bool:
    return _paused


def pause() -> None:
    global _paused
    _paused = True
    log.info('Analysis PAUSED — confidence scoring skipped until "analyze"')


def resume() -> None:
    global _paused
    _paused = False
    log.info('Analysis RESUMED')
