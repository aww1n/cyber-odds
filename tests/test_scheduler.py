from __future__ import annotations

import asyncio

import pytest

from app.workers.scheduler import IsolatedScheduler, RecurringJob


@pytest.mark.anyio
async def test_failed_job_does_not_stop_other_recurring_jobs() -> None:
    stop = asyncio.Event()
    successful_calls = 0
    failed_calls = 0

    async def successful() -> None:
        nonlocal successful_calls
        successful_calls += 1
        if successful_calls == 3:
            stop.set()

    async def failing() -> None:
        nonlocal failed_calls
        failed_calls += 1
        raise RuntimeError("isolated failure")

    jobs = (
        RecurringJob("successful", 0.001, successful),
        RecurringJob("failing", 0.001, failing),
    )

    await asyncio.wait_for(IsolatedScheduler().run(jobs, stop=stop), timeout=1)

    assert successful_calls == 3
    assert failed_calls >= 1


def test_recurring_job_rejects_non_positive_interval() -> None:
    async def callback() -> None:
        return None

    with pytest.raises(ValueError, match="positive"):
        RecurringJob("invalid", 0, callback)
