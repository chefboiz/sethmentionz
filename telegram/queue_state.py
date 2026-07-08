"""
Single source of truth for which opportunity is currently open (alert sent, reply pending).
Set when an alert goes out; cleared when the slot is resolved (y/n/expire/skip).
Survives only within a single process lifetime — on restart it recovers from the DB.
"""
import logging

log = logging.getLogger(__name__)

_open_market_id: str | None = None


def get() -> str | None:
    return _open_market_id


def set_open(market_id: str | None) -> None:
    global _open_market_id
    if market_id != _open_market_id:
        log.debug('queue_state: %s → %s', _open_market_id, market_id)
    _open_market_id = market_id
