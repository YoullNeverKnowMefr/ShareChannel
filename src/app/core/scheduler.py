from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pytz

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Scheduler:

    def __init__(self) -> None:
        tz = pytz.timezone(settings.timezone)
        self.scheduler = AsyncIOScheduler(timezone=tz)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("scheduler_started")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("scheduler_stopped")

    def add_interval_job(
        self,
        func: Callable[..., Any],
        *,
        job_id: str,
        seconds: float,
        jitter: float | None = None,
        kwargs: dict[str, Any] | None = None,
        replace_existing: bool = True,
    ) -> None:
        trigger = IntervalTrigger(seconds=seconds, jitter=jitter)
        self.scheduler.add_job(
            func,
            id=job_id,
            trigger=trigger,
            kwargs=kwargs or {},
            replace_existing=replace_existing,
            coalesce=True,
            misfire_grace_time=30,
        )
        logger.info("scheduler_job_scheduled", job_id=job_id, seconds=seconds)

    def add_one_off_job(
        self,
        func: Callable[..., Any],
        *,
        job_id: str,
        run_date: datetime,
        kwargs: dict[str, Any] | None = None,
        replace_existing: bool = True,
    ) -> None:
        trigger = DateTrigger(run_date=run_date)
        self.scheduler.add_job(
            func,
            id=job_id,
            trigger=trigger,
            kwargs=kwargs or {},
            replace_existing=replace_existing,
        )
        logger.info("scheduler_job_enqueued", job_id=job_id, run_date=run_date.isoformat())

    def remove(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
            logger.info("scheduler_job_removed", job_id=job_id)
        except Exception:
            logger.warning("scheduler_job_remove_failed", job_id=job_id)


scheduler = Scheduler()

__all__ = ["scheduler", "Scheduler"]
