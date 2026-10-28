"""The asyncio scheduler that runs trading_core's periodic sync jobs (decision #235).

One scheduler lives inside the FastAPI process. Each job runs once at startup and then every
``interval_seconds``. A job never overlaps itself: the loop and ``run_now`` (used by the signed
"sync now" endpoints) share a per-job lock, so a manual run waits for a scheduled one to finish.

Register a job with ``scheduler.add(name, interval_seconds, fn)`` where ``fn`` is an async
callable taking no arguments. ATL-9 registers ``instruments``; ATL-10 adds the account jobs.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("trading_core.scheduler")

JobFn = Callable[[], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Job:
    name: str
    interval_seconds: float
    fn: JobFn
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    runs: int = 0
    failures: int = 0


class UnknownJobError(KeyError):
    """``run_now`` was asked for a job that was never registered."""


class Scheduler:
    def __init__(self, *, sleep: Sleep = asyncio.sleep) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._sleep = sleep

    @property
    def jobs(self) -> dict[str, Job]:
        return dict(self._jobs)

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    def add(self, name: str, interval_seconds: float, fn: JobFn) -> Job:
        if name in self._jobs:
            raise ValueError(f"job {name!r} is already registered")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        job = Job(name=name, interval_seconds=interval_seconds, fn=fn)
        self._jobs[name] = job
        return job

    async def run_now(self, name: str) -> Any:
        """Run one job immediately (after any in-flight run of it) and return its result."""
        job = self._jobs.get(name)
        if job is None:
            raise UnknownJobError(name)
        async with job.lock:
            try:
                result = await job.fn()
            except Exception:
                job.failures += 1
                raise
            finally:
                job.runs += 1
            return result

    async def _loop(self, job: Job) -> None:
        while True:
            try:
                await self.run_now(job.name)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The job records its own error (sync_status.last_error); keep the loop alive.
                logger.exception("scheduled job failed", extra={"job": job.name})
            await self._sleep(job.interval_seconds)

    def start(self) -> None:
        if self.running:
            return
        self._tasks = [
            asyncio.create_task(self._loop(job), name=f"job:{job.name}") for job in self._jobs.values()
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
