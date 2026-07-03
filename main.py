import logging
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from apscheduler.schedulers.blocking import BlockingScheduler

from config import (
    SCAN_INTERVAL_MINUTES,
    PRICE_REFRESH_SECONDS,
    ALERT_CHECK_SECONDS,
    CALLBACK_POLL_SECONDS,
    FILL_MONITOR_SECONDS,
    RESOLUTION_CHECK_MINUTES,
)
import db
import telegram as tg
import trading_state
import monitoring
from scanner import run_scan
from confidence import run_confidence
from edge import run_edge, run_price_refresh
from edge.fill_monitor import run_fill_monitor
from resolution.tracker import run_resolution_check
from telegram.alerts import run_alert_check
from telegram.callbacks import run_callback_poll

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('sethmentionz')


def main() -> None:
    log.info('SethMentionz starting up')

    db.get_client()

    paused_note = '  [TRADING PAUSED]' if trading_state.is_paused() else ''
    tg.send_message(f'SethMentionz online{paused_note}')
    log.info('Telegram: announced online%s', paused_note)

    now = datetime.now(timezone.utc)
    scheduler = BlockingScheduler(timezone='UTC')

    # ── Phase 1 — scanner ────────────────────────────────────────────────────
    scheduler.add_job(run_scan, 'interval',
                      minutes=SCAN_INTERVAL_MINUTES, id='scanner',
                      next_run_time=now)

    # ── Phase 2 — confidence ─────────────────────────────────────────────────
    scheduler.add_job(run_confidence, 'interval',
                      minutes=SCAN_INTERVAL_MINUTES, id='confidence',
                      next_run_time=now + timedelta(minutes=2))

    # ── Phase 3a — edge calc ─────────────────────────────────────────────────
    scheduler.add_job(run_edge, 'interval',
                      minutes=SCAN_INTERVAL_MINUTES, id='edge',
                      next_run_time=now + timedelta(minutes=4))

    # ── Phase 3b — fast price refresh ────────────────────────────────────────
    scheduler.add_job(run_price_refresh, 'interval',
                      seconds=PRICE_REFRESH_SECONDS, id='price_refresh',
                      next_run_time=now + timedelta(minutes=5))

    # ── Phase 4a — alert check ────────────────────────────────────────────────
    scheduler.add_job(run_alert_check, 'interval',
                      seconds=ALERT_CHECK_SECONDS, id='alert_check',
                      next_run_time=now + timedelta(seconds=10))

    # ── Phase 4b — Telegram callback poll ────────────────────────────────────
    scheduler.add_job(run_callback_poll, 'interval',
                      seconds=CALLBACK_POLL_SECONDS, id='callback_poll',
                      next_run_time=now)

    # ── Phase 5 — fill monitor ────────────────────────────────────────────────
    scheduler.add_job(run_fill_monitor, 'interval',
                      seconds=FILL_MONITOR_SECONDS, id='fill_monitor',
                      next_run_time=now + timedelta(seconds=15))

    # ── Phase 6 — resolution tracker ─────────────────────────────────────────
    scheduler.add_job(run_resolution_check, 'interval',
                      minutes=RESOLUTION_CHECK_MINUTES, id='resolution',
                      next_run_time=now + timedelta(minutes=6))

    # ── Phase 6 — error alerting ──────────────────────────────────────────────
    monitoring.register(scheduler)

    log.info(
        'All jobs registered:\n'
        '  scanner/confidence/edge  every %dmin\n'
        '  price_refresh            every %ds\n'
        '  alert_check              every %ds\n'
        '  callback_poll            every %ds\n'
        '  fill_monitor             every %ds\n'
        '  resolution               every %dmin',
        SCAN_INTERVAL_MINUTES,
        PRICE_REFRESH_SECONDS,
        ALERT_CHECK_SECONDS,
        CALLBACK_POLL_SECONDS,
        FILL_MONITOR_SECONDS,
        RESOLUTION_CHECK_MINUTES,
    )
    scheduler.start()


if __name__ == '__main__':
    main()
