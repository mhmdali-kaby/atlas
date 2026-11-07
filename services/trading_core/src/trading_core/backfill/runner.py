"""The resumable history backfill (ATL-46, brief §7.3).

``BackfillRunner.run()`` claims runnable jobs (``pending``, or ``running`` left over from a killed
process) in ``backfill_runner.dataset_order`` and works ``concurrency`` of them at a time through one
shared ``OkxClient`` (so one rate limiter). Each job walks OKX history backwards from its
``cursor_ts`` (or from now) towards ``target_from``:

- ``candles:<bar>``: ``market/history-candles`` via ``OkxClient.iter_candles(history=True)``;
- ``funding``: ``public/funding-rate-history`` with the ``after`` cursor;
- ``open_interest``: rubik ``open-interest-volume`` for the currency, with ``end`` = cursor - 1 ms.

Every page is written in ONE transaction together with the job's new ``cursor_ts`` (the oldest ts of
that page) and ``rows_written``, so a process killed at any point resumes exactly after the last
committed page and never re-fetches a page newer than the cursor. The job row is only updated while
its status is still ``running``; a pause therefore stops a worker before its next write (that page is
rolled back and fetched again on resume). A job is ``done`` when OKX returns no older data (or the
walk reaches ``target_from``); any error after the client's own retries marks only that job
``failed`` with ``last_error``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from atlas_shared.db import BackfillJob
from atlas_shared.db import Candle as CandleRow
from atlas_shared.db import FundingRate as FundingRow
from atlas_shared.db import OpenInterest as OpenInterestRow
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, case, inspect, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from trading_core.backfill.plan import (
    CANDLES_PREFIX,
    DONE,
    FAILED,
    FUNDING,
    OPEN_INTEREST,
    PENDING,
    RUNNING,
    ensure_jobs,
    runner_cfg,
)
from trading_core.okx import OkxClient

logger = logging.getLogger("trading_core.backfill")

STREAM = "backfill"

ClientFactory = Callable[[str], OkxClient]
Row = dict[str, Any]


class _Stopped(Exception):
    """The job is no longer ``running`` (paused); roll back this page and stop."""


@dataclass(frozen=True)
class _ClaimedJob:
    id: uuid.UUID
    inst_id: str
    dataset: str
    target_from: datetime
    cursor_ts: datetime | None


@dataclass
class RunResult:
    env: str
    done: int = 0
    failed: int = 0
    stopped: int = 0
    rows: int = 0
    jobs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "jobs_run": len(self.jobs),
            "done": self.done,
            "failed": self.failed,
            "stopped": self.stopped,
            "rows": self.rows,
        }


def _ms(ts: datetime) -> int:
    return int(ts.timestamp()) * 1000 + ts.microsecond // 1000


def upsert_rows(conn: Connection, model: type[Any], rows: Sequence[Row]) -> int:
    """Upsert into a hypertable keyed by its primary key: deduplicated on the key (last wins), sorted
    by the key (stable lock order), and chunked so no statement binds more than
    ``backfill_runner.max_bind_params`` parameters. Returns the number of distinct rows sent."""
    if not rows:
        return 0
    key_cols = [c.name for c in inspect(model).primary_key]
    unique: dict[tuple[Any, ...], Row] = {}
    for row in rows:
        unique[tuple(row[k] for k in key_cols)] = row
    ordered = [unique[k] for k in sorted(unique)]
    width = len(ordered[0])
    cfg = runner_cfg()
    per_stmt = max(1, min(int(cfg["batch_rows"]), int(cfg["max_bind_params"]) // width))
    update_cols = [c for c in ordered[0] if c not in key_cols]
    for start in range(0, len(ordered), per_stmt):
        stmt = insert(model).values(ordered[start : start + per_stmt])
        stmt = stmt.on_conflict_do_update(
            index_elements=key_cols, set_={c: stmt.excluded[c] for c in update_cols}
        )
        conn.execute(stmt)
    return len(ordered)


class BackfillRunner:
    def __init__(
        self,
        env: str,
        engine: Engine,
        *,
        client_factory: ClientFactory | None = None,
        concurrency: int | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        cfg = runner_cfg()
        self.env = env
        self.engine = engine
        self._factory: ClientFactory = client_factory or OkxClient
        self.concurrency = max(1, int(cfg["concurrency"] if concurrency is None else concurrency))
        self._now = now or (lambda: datetime.now(UTC))
        self._order: list[str] = list(cfg["dataset_order"])
        self._oi_period = str(cfg["open_interest_period"])
        self._err_max = int(cfg["last_error_max_chars"])
        self._page = {
            "candles": int(DEFAULTS["okx_rest"]["page_limits"]["market/history-candles"]),
            "funding": int(DEFAULTS["okx_rest"]["page_limits"]["public/funding-rate-history"]),
        }

    # ---- claiming --------------------------------------------------------------------------

    def _claim(self, seen: set[uuid.UUID]) -> _ClaimedJob | None:
        priority = case(
            {ds: i for i, ds in enumerate(self._order)}, value=BackfillJob.dataset, else_=len(self._order)
        )
        with self.engine.begin() as conn:
            while True:
                stmt = (
                    select(BackfillJob)
                    .where(BackfillJob.env == self.env, BackfillJob.status.in_((PENDING, RUNNING)))
                    .order_by(priority, BackfillJob.inst_id)
                    .limit(1)
                )
                if seen:
                    stmt = stmt.where(BackfillJob.id.not_in(seen))
                job = conn.execute(stmt).first()
                if job is None:
                    return None
                seen.add(job.id)
                claimed = conn.execute(
                    update(BackfillJob)
                    .where(BackfillJob.id == job.id, BackfillJob.status.in_((PENDING, RUNNING)))
                    .values(status=RUNNING, updated_at=self._now())
                ).rowcount
                if claimed:
                    return _ClaimedJob(job.id, job.inst_id, job.dataset, job.target_from, job.cursor_ts)

    # ---- writing ---------------------------------------------------------------------------

    def _commit_page(self, job: _ClaimedJob, model: type[Any], rows: list[Row], oldest: datetime) -> int:
        with self.engine.begin() as conn:
            n = len({tuple(r[k.name] for k in inspect(model).primary_key) for r in rows})
            moved = conn.execute(
                update(BackfillJob)
                .where(BackfillJob.id == job.id, BackfillJob.status == RUNNING)
                .values(
                    cursor_ts=oldest,
                    rows_written=BackfillJob.rows_written + n,
                    updated_at=self._now(),
                )
            ).rowcount
            if not moved:
                raise _Stopped
            return upsert_rows(conn, model, rows)

    def _finish(self, job: _ClaimedJob, status: str, error: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(BackfillJob)
                .where(BackfillJob.id == job.id, BackfillJob.status == RUNNING)
                .values(status=status, last_error=error, updated_at=self._now())
            )

    # ---- per-dataset walks: yield (model, rows, oldest_ts) per page ------------------------

    async def _candle_pages(
        self, okx: OkxClient, job: _ClaimedJob
    ) -> AsyncIterator[tuple[type[Any], list[Row], datetime]]:
        bar = job.dataset[len(CANDLES_PREFIX) :]
        async for page in okx.iter_candles(
            job.inst_id,
            bar=bar,
            after=job.cursor_ts,
            since=job.target_from,
            limit=self._page["candles"],
            history=True,
        ):
            rows = [
                {
                    "env": self.env,
                    "inst_id": job.inst_id,
                    "bar": bar,
                    "ts": c.ts,
                    "o": c.o,
                    "h": c.h,
                    "l": c.l,
                    "c": c.c,
                    "vol": c.vol,
                    "vol_ccy": c.vol_ccy,
                    "vol_ccy_quote": c.vol_ccy_quote,
                    "confirmed": True,
                }
                # A still-forming bar is left to the live poller (ATL-47); NOT NULL columns need values.
                for c in page
                if c.confirm and None not in (c.o, c.h, c.l, c.c, c.vol)
            ]
            yield CandleRow, rows, min(c.ts for c in page)

    async def _funding_pages(
        self, okx: OkxClient, job: _ClaimedJob
    ) -> AsyncIterator[tuple[type[Any], list[Row], datetime]]:
        cursor = job.cursor_ts
        while True:
            page = await okx.get_funding_rate_history(job.inst_id, after=cursor, limit=self._page["funding"])
            page = [p for p in page if p.funding_time >= job.target_from]
            if not page:
                return
            oldest = min(p.funding_time for p in page)
            if cursor is not None and oldest >= cursor:
                return  # the cursor did not move: OKX has nothing older
            rows = [
                {
                    "env": self.env,
                    "inst_id": job.inst_id,
                    "ts": p.funding_time,
                    "funding_rate": p.funding_rate,
                    "realized_rate": p.realized_rate,
                }
                for p in page
                if p.funding_rate is not None
            ]
            yield FundingRow, rows, oldest
            cursor = oldest
            if oldest <= job.target_from:
                return

    async def _open_interest_pages(
        self, okx: OkxClient, job: _ClaimedJob
    ) -> AsyncIterator[tuple[type[Any], list[Row], datetime]]:
        cursor = job.cursor_ts
        while True:
            end = None if cursor is None else _ms(cursor) - 1
            page = await okx.get_open_interest_volume(job.inst_id, period=self._oi_period, end=end)
            page = [p for p in page if p.ts >= job.target_from and (cursor is None or p.ts < cursor)]
            if not page:
                return
            oldest = min(p.ts for p in page)
            rows = [
                {
                    "env": self.env,
                    "inst_id": job.inst_id,
                    "ts": p.ts,
                    "oi": None,
                    "oi_ccy": None,
                    "oi_usd": p.oi,
                }
                for p in page
            ]
            yield OpenInterestRow, rows, oldest
            cursor = oldest
            if oldest <= job.target_from:
                return

    def _pages(
        self, okx: OkxClient, job: _ClaimedJob
    ) -> AsyncIterator[tuple[type[Any], list[Row], datetime]]:
        if job.dataset.startswith(CANDLES_PREFIX):
            return self._candle_pages(okx, job)
        if job.dataset == FUNDING:
            return self._funding_pages(okx, job)
        if job.dataset == OPEN_INTEREST:
            return self._open_interest_pages(okx, job)
        raise ValueError(f"backfill does not handle dataset {job.dataset!r}")

    # ---- running ---------------------------------------------------------------------------

    async def _run_job(self, okx: OkxClient, job: _ClaimedJob, result: RunResult) -> None:
        result.jobs.append(f"{job.inst_id}:{job.dataset}")
        try:
            async for model, rows, oldest in self._pages(okx, job):
                result.rows += await asyncio.to_thread(self._commit_page, job, model, rows, oldest)
        except _Stopped:
            result.stopped += 1
            logger.info("backfill job stopped", extra={"inst_id": job.inst_id, "dataset": job.dataset})
            return
        except asyncio.CancelledError:
            raise  # left `running` with its cursor; the next run resumes it
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[: self._err_max]
            await asyncio.to_thread(self._finish, job, FAILED, error)
            result.failed += 1
            logger.warning(
                "backfill job failed",
                extra={"inst_id": job.inst_id, "dataset": job.dataset, "error": error},
            )
            return
        await asyncio.to_thread(self._finish, job, DONE)
        result.done += 1

    async def run(self) -> RunResult:
        """Work every runnable job of the env until none is left (jobs added meanwhile included)."""
        result = RunResult(self.env)
        seen: set[uuid.UUID] = set()
        claim_lock = asyncio.Lock()
        async with self._factory(self.env) as okx:

            async def worker() -> None:
                while True:
                    async with claim_lock:
                        job = await asyncio.to_thread(self._claim, seen)
                    if job is None:
                        return
                    await self._run_job(okx, job, result)

            await asyncio.gather(*(worker() for _ in range(self.concurrency)))
        return result


def backfill_job(
    env: str, engine: Engine, *, client_factory: ClientFactory | None = None
) -> Callable[[], Any]:
    """The scheduler job: make sure every eligible instrument has its jobs (first run creates them;
    paused/failed are left as the owner left them), then work the queue."""

    async def run() -> dict[str, Any]:
        ensured = await asyncio.to_thread(ensure_jobs, engine, env, resume=False)
        result = await BackfillRunner(env, engine, client_factory=client_factory).run()
        return {**result.as_dict(), "created": ensured.created}

    return run
