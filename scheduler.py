import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

import pipeline

log = logging.getLogger(__name__)

_scheduler = None


def _run_job() -> None:
    log.info("Running daily analysis job...")
    try:
        result = pipeline.run_daily_analysis()
        log.info("Daily analysis done: %s", result)
    except Exception as exc:
        log.exception("Daily analysis job failed: %s", exc)


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(
        _run_job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=15,
        minute=45,
        id="daily_analysis",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("Scheduler started: 3:45 PM IST Mon-Fri")
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        _scheduler = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sched = start()
    log.info("Press Ctrl+C to stop. Next run at 3:45 PM IST weekdays.")
    try:
        while True:
            import time

            time.sleep(3600)
    except KeyboardInterrupt:
        stop()
