import logging
import os

log = logging.getLogger(__name__)

# Honour the env var at startup so Railway deploys can boot paused
_paused: bool = os.getenv('TRADING_PAUSED', '').lower() in ('1', 'true', 'yes')

if _paused:
    log.warning('Trading started in PAUSED state (TRADING_PAUSED env var)')


def is_paused() -> bool:
    return _paused


def pause() -> None:
    global _paused
    _paused = True
    log.warning('Trading PAUSED — alerts continue, [Approve] falls back to dry-run')


def resume() -> None:
    global _paused
    _paused = False
    log.info('Trading RESUMED')
