import logging
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

import telegram as tg

log = logging.getLogger(__name__)

_failure_counts: dict[str, int] = {}


def _on_error(event) -> None:
    job_id = event.job_id
    exc    = event.exception

    _failure_counts[job_id] = _failure_counts.get(job_id, 0) + 1
    count = _failure_counts[job_id]

    log.error('Job [%s] failed (#%d): %s', job_id, count, exc)

    # Alert on 1st failure, then every 5th — avoids spamming on persistent errors
    if count == 1 or count % 5 == 0:
        tg.send_message(
            f'🚨 <b>{job_id} failed</b> (×{count})\n'
            f'<code>{str(exc)[:350]}</code>'
        )


def _on_success(event) -> None:
    job_id = event.job_id
    if job_id in _failure_counts:
        prev = _failure_counts.pop(job_id)
        log.info('Job [%s] recovered after %d failure(s)', job_id, prev)
        tg.send_message(f'✅ <b>{job_id} recovered</b> (was failing ×{prev})')


def register(scheduler) -> None:
    """Attach error and recovery listeners to the given APScheduler instance."""
    scheduler.add_listener(_on_error,   EVENT_JOB_ERROR)
    scheduler.add_listener(_on_success, EVENT_JOB_EXECUTED)
    log.info('Job error alerting registered')
