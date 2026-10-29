"""Closed bars stay fresh by REST polling (ATL-47, brief §7.3, decision #242: no WebSocket feed yet).

``CandlePoller.tick()`` is the scheduler job (every ``candle_poller.tick_seconds``). On each tick it
works out, for every bar in ``candle_poller.bars``, the latest bar close at ``now -
close_delay_seconds``. A bar whose close has not been handled yet is *due*. For a due bar the poller
takes every instrument whose ``candles:<bar>`` backfill job is ``done`` and asks ``market/candles``
for the bars **newer than the newest stored one** (``before`` = that ts), so older bars are never
fetched again. Only confirmed bars are upserted; the still-forming bar is left for the next close.

Requests go through one ``OkxClient`` (one token bucket per endpoint, shared with the backfill and the
quality checker when the app passes one ``RateLimiter``), at most ``concurrency`` at a time, smallest
bar first, so the burst at the top of each hour/day is spread by the limiter instead of breaking it.
If more than ``page_limit`` bars are missing (the process was down), only the newest page is fetched;
the hole left behind is a ``gap`` the quality checker repairs from history.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas_shared.db import BackfillJob
from atlas_shared.db import Candle as CandleRow
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, func, select

from trading_core.backfill.plan import DONE
from trading_core.backfill.runner import upsert_rows
from trading_core.marketdata.grid import BAR_SECONDS, candle_rows, dataset, last_close
from trading_core.okx import OkxClient

logger = logging.getLogger("trading_core.marketdata")

JOB = "candle_poller"

ClientFactory = Callable[[str], OkxClient]


def poller_cfg() -> dict[str, Any]:
    cfg: dict[str, Any] = DEFAULTS["candle_poller"]
    return cfg


@dataclass
class PollResult:
    env: str
    bars: list[str] = field(default_factory=list)
    series: int = 0
    rows: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "bars": self.bars,
            "series": self.series,
            "rows": self.rows,
            "failed": self.failed,
        }


class CandlePoller:
    def __init__(
        self,
        env: str,
        engine: Engine,
        *,
        client_factory: ClientFactory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        cfg = poller_cfg()
        self.env = env
        self.engine = engine
        self._factory: ClientFactory = client_factory or OkxClient
        self._now = now or (lambda: datetime.now(UTC))
        self.delay = timedelta(seconds=float(cfg["close_delay_seconds"]))
        self.concurrency = max(1, int(cfg["concurrency"]))
        self.page_limit = int(cfg["page_limit"])
        self.bars: list[str] = sorted(cfg["bars"], key=lambda b: BAR_SECONDS[b])
        # bar -> the close already handled. Empty at start, so the first tick catches every bar up.
        self.handled: dict[str, datetime] = {}

    def due(self, at: datetime) -> list[tuple[str, datetime]]:
        """(bar, close) for each bar whose latest close (as of ``at - delay``) is not handled yet."""
        out: list[tuple[str, datetime]] = []
        for bar in self.bars:
            close = last_close(bar, at - self.delay)
            if self.handled.get(bar) != close:
                out.append((bar, close))
        return out

    def _targets(self, bar: str) -> list[tuple[str, datetime | None]]:
        """(inst_id, newest stored ts) for every instrument whose backfill of this bar is done."""
        with self.engine.connect() as conn:
            insts = list(
                conn.execute(
                    select(BackfillJob.inst_id)
                    .where(
                        BackfillJob.env == self.env,
                        BackfillJob.dataset == dataset(bar),
                        BackfillJob.status == DONE,
                    )
                    .order_by(BackfillJob.inst_id)
                ).scalars()
            )
            if not insts:
                return []
            newest: dict[str, datetime] = {
                inst_id: ts
                for inst_id, ts in conn.execute(
                    select(CandleRow.inst_id, func.max(CandleRow.ts))
                    .where(CandleRow.env == self.env, CandleRow.bar == bar, CandleRow.inst_id.in_(insts))
                    .group_by(CandleRow.inst_id)
                ).all()
            }
        return [(i, newest.get(i)) for i in insts]

    def _write(self, rows: Sequence[dict[str, Any]]) -> int:
        with self.engine.begin() as conn:
            return upsert_rows(conn, CandleRow, rows)

    async def _poll_one(
        self, okx: OkxClient, bar: str, inst_id: str, newest: datetime | None, result: PollResult
    ) -> None:
        try:
            page = await okx.get_candles(inst_id, bar=bar, before=newest, limit=self.page_limit)
            fresh = [c for c in page if newest is None or c.ts > newest]
            rows = candle_rows(self.env, inst_id, bar, fresh)
            if rows:
                result.rows += await asyncio.to_thread(self._write, rows)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.failed += 1
            logger.warning(
                "candle poll failed",
                extra={"inst_id": inst_id, "bar": bar, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )

    async def poll(self, bars: Sequence[str]) -> PollResult:
        """Fetch the bars newer than the newest stored one for every done series of ``bars``."""
        result = PollResult(self.env, list(bars))
        work: list[tuple[str, str, datetime | None]] = []
        for bar in bars:
            targets = await asyncio.to_thread(self._targets, bar)
            work.extend((bar, inst_id, newest) for inst_id, newest in targets)
        result.series = len(work)
        if not work:
            return result
        gate = asyncio.Semaphore(self.concurrency)
        async with self._factory(self.env) as okx:

            async def one(bar: str, inst_id: str, newest: datetime | None) -> None:
                async with gate:
                    await self._poll_one(okx, bar, inst_id, newest, result)

            await asyncio.gather(*(one(*w) for w in work))
        return result

    async def tick(self) -> dict[str, Any]:
        due = self.due(self._now())
        if not due:
            return {"env": self.env, "bars": [], "series": 0, "rows": 0, "failed": 0}
        result = await self.poll([bar for bar, _ in due])
        for bar, close in due:
            self.handled[bar] = close
        if result.rows or result.failed:
            logger.info("candles polled", extra=result.as_dict())
        return result.as_dict()


def candle_poller_job(
    env: str,
    engine: Engine,
    *,
    client_factory: ClientFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], Any]:
    poller = CandlePoller(env, engine, client_factory=client_factory, now=now)
    return poller.tick
