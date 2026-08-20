from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

LOGGER = logging.getLogger(__name__)

AsyncJob = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RecurringJob:
    name: str
    interval_seconds: float
    callback: AsyncJob

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("job interval must be positive")


class IsolatedScheduler:
    """Run each collector independently so one failure cannot stop other jobs."""

    async def run(
        self,
        jobs: tuple[RecurringJob, ...],
        *,
        stop: asyncio.Event | None = None,
    ) -> None:
        if not jobs:
            raise ValueError("at least one recurring job is required")
        stop_event = stop or asyncio.Event()
        tasks = [
            asyncio.create_task(self._run_job(job, stop_event), name=job.name)
            for job in jobs
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job: RecurringJob, stop: asyncio.Event) -> None:
        LOGGER.info(
            "Recurring job started: %s every %.1fs",
            job.name,
            job.interval_seconds,
        )
        while not stop.is_set():
            started = perf_counter()
            try:
                await job.callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Recurring job failed: %s", job.name)
            else:
                LOGGER.info(
                    "Recurring job completed: %s in %.2fs",
                    job.name,
                    perf_counter() - started,
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=job.interval_seconds)
