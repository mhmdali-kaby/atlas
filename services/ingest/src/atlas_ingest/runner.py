"""The ingest poll loop (ATL-59, decision #243).

Every ``tick`` the runner looks at the enabled ``news_sources`` (re-read every ``reload_seconds``)
and starts each source whose ``poll_seconds`` has elapsed. Before a run it re-reads that source's
row, so disabling a source, removing its key or a degraded window applies at once. Skipped without
a request and without touching any column: keyed sources with no key (status ``needs_key``) and
sources whose ``disabled_until`` is in the future (``degraded``).

Outcomes: success resets ``consecutive_failures`` and ``last_error`` and sets ``last_success_at``;
a failure increments the counter and stores a short ``last_error`` (an HTTP status, an exception
class name or a CollectError message, never a URL or key); reaching ``failure_threshold`` sets
``disabled_until = now + disable_minutes``. After the window the source is tried again; one more
failure disables it again straight away, one success resets it. A collector raising
``RateLimited`` (HTTP 429) is one failure, and the next poll waits at least its retry window.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx
from atlas_shared.laravel_crypt import decrypt_string
from sqlalchemy import Engine

from atlas_ingest import sources as db
from atlas_ingest.collectors import (
    CollectContext,
    CollectError,
    Collector,
    RateLimited,
    SourceConfig,
    factory_for,
)
from atlas_ingest.pipeline import Pipeline

logger = logging.getLogger("atlas_ingest.runner")

Clock = Callable[[], datetime]
MAX_ERROR_CHARS = 200


def utcnow() -> datetime:
    return datetime.now(UTC)


def short_error(exc: BaseException, secret: str | None = None) -> str:
    """A last_error safe to store and show: no URL (httpx messages carry it), no key."""
    if isinstance(exc, httpx.HTTPStatusError):
        message = f"HTTP {exc.response.status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        message = "timeout"
    elif isinstance(exc, httpx.HTTPError):
        message = f"network error ({type(exc).__name__})"
    elif isinstance(exc, CollectError):
        message = str(exc) or "collect error"
    else:
        message = type(exc).__name__
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:MAX_ERROR_CHARS]


@dataclass(frozen=True)
class RunnerConfig:
    reload_seconds: float
    failure_threshold: int
    disable_minutes: int


class IngestRunner:
    def __init__(
        self,
        *,
        engine: Engine,
        http: httpx.AsyncClient,
        pipeline: Pipeline,
        config: RunnerConfig,
        app_key: str,
        clock: Clock = utcnow,
        env: str | None = None,
    ) -> None:
        self.engine = engine
        self.http = http
        self.pipeline = pipeline
        self.config = config
        self._app_key = app_key
        self.clock = clock
        if env is None:
            from atlas_ingest.aliases import active_env

            env = active_env()
        self.env = env
        """The active env: per-symbol collectors only see this env's stock/ETF instruments."""
        self._sources: dict[str, db.SourceRow] = {}
        self._next_due: dict[str, datetime] = {}
        self._loaded_at: datetime | None = None
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._collectors: dict[str, Collector] = {}
        self._warned_no_collector: set[str] = set()

    @property
    def sources(self) -> dict[str, db.SourceRow]:
        return dict(self._sources)

    async def reload(self) -> None:
        rows = await asyncio.to_thread(db.load_enabled, self.engine)
        self._sources = {r.key: r for r in rows}
        self._loaded_at = self.clock()
        for key in list(self._next_due):
            if key not in self._sources:
                del self._next_due[key]

    def _collector(self, row: db.SourceRow) -> Collector | None:
        if row.key not in self._collectors:
            factory = factory_for(row.key, row.kind)
            if factory is None:
                if row.key not in self._warned_no_collector:
                    self._warned_no_collector.add(row.key)
                    logger.warning("no collector for source", extra={"source": row.key, "kind": row.kind})
                return None
            self._collectors[row.key] = factory()
        return self._collectors[row.key]

    def _runnable(self, row: db.SourceRow, now: datetime) -> bool:
        if not row.enabled or row.missing_key:
            return False
        return not (row.disabled_until is not None and row.disabled_until > now)

    async def start_due(self) -> list[asyncio.Task[None]]:
        """Start every due source that is not already running; returns the started tasks."""
        now = self.clock()
        if self._loaded_at is None or (now - self._loaded_at).total_seconds() >= self.config.reload_seconds:
            await self.reload()
        started: list[asyncio.Task[None]] = []
        for key, row in self._sources.items():
            if key in self._in_flight or self._next_due.get(key, now) > now:
                continue
            if not self._runnable(row, now) or self._collector(row) is None:
                continue
            task = asyncio.create_task(self._run(key), name=f"ingest:{key}")
            self._in_flight[key] = task
            task.add_done_callback(self._finished)
            started.append(task)
        return started

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._in_flight.pop(task.get_name().removeprefix("ingest:"), None)

    async def tick(self) -> list[str]:
        """Start the due sources and wait for them (tests and one-shot runs). Returns their keys."""
        tasks = await self.start_due()
        await asyncio.gather(*tasks)
        return [t.get_name().removeprefix("ingest:") for t in tasks]

    async def run_forever(self, stop: asyncio.Event, tick_seconds: float) -> None:
        while not stop.is_set():
            try:
                await self.start_due()
            except Exception:
                logger.exception("tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick_seconds)
            except TimeoutError:
                pass
        if self._in_flight:
            await asyncio.gather(*self._in_flight.values(), return_exceptions=True)

    async def _run(self, key: str) -> None:
        now = self.clock()
        # The row may have changed since the last reload: honour enabled/key/disabled_until now.
        row = await asyncio.to_thread(db.load_one, self.engine, key)
        if row is None or not self._runnable(row, now):
            if row is None or not row.enabled:
                self._sources.pop(key, None)
            elif row is not None:
                self._sources[key] = row
            return
        self._sources[key] = row
        self._next_due[key] = now + timedelta(seconds=max(row.poll_seconds, 1))
        collector = self._collector(row)
        if collector is None:
            return

        secret: str | None = None
        started = time.perf_counter()
        try:
            if row.api_key_ciphertext:
                try:
                    secret = decrypt_string(row.api_key_ciphertext, self._app_key)
                except Exception as exc:
                    raise CollectError("api_key cannot be decrypted (check APP_KEY)") from exc
            source = SourceConfig(
                key=row.key,
                kind=row.kind,
                category=row.category,
                name=row.name,
                url=row.url,
                poll_seconds=row.poll_seconds,
                api_key=secret,
            )
            ctx = CollectContext(
                http=self.http,
                now=now,
                stock_symbols=lambda: asyncio.to_thread(db.stock_symbols, self.engine, self.env),
                engine=self.engine,
            )
            items = await collector.collect(source, ctx)
            result = await self.pipeline.process(row.key, items, now)
            next_poll = getattr(collector, "next_poll_seconds", None)
            if next_poll is not None:
                delay = await next_poll(source, ctx)
                if delay is not None:
                    self._next_due[key] = now + timedelta(seconds=max(float(delay), 1.0))
        except Exception as exc:
            error = short_error(exc, secret)
            if isinstance(exc, RateLimited):
                # Honour the provider's window even when poll_seconds is shorter.
                self._next_due[key] = max(
                    self._next_due[key], now + timedelta(seconds=exc.retry_after_seconds)
                )
            failures, disabled_until = await asyncio.to_thread(
                db.record_failure,
                self.engine,
                key,
                now,
                error,
                threshold=self.config.failure_threshold,
                disable_minutes=self.config.disable_minutes,
            )
            if disabled_until is not None:
                self._sources[key] = replace(row, disabled_until=disabled_until)
            logger.warning(
                "poll failed",
                extra={
                    "source": key,
                    "error": error,
                    "consecutive_failures": failures,
                    "disabled_until": disabled_until.isoformat() if disabled_until else None,
                },
            )
            return
        written = result.inserted + ctx.stats.written
        await asyncio.to_thread(db.record_success, self.engine, key, now, inserted=written)
        logger.info(
            "poll ok",
            extra={
                "source": key,
                "fetched": result.fetched,
                "new": result.new,
                "inserted": result.inserted,
                "written": ctx.stats.written,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
