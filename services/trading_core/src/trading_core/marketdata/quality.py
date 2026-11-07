"""Candle data-quality checks and gap repair (ATL-47, brief §7.3).

``QualityChecker.run()`` looks at the newest ``quality.window_bars`` bars of every candle series whose
backfill job is ``done`` (optionally one instrument) and records ``data_quality_issues``:

- ``gap``: grid slots with no bar between two stored bars. One issue per run of missing slots
  (``from_ts`` = first missing open time, ``to_ts`` = last, ``detail.missing_bars``). Gaps are then
  re-fetched from ``market/history-candles``; when every missing slot has a bar the issue gets
  ``repaired_at``. A gap OKX does not have stays open, and is not re-fetched after
  ``quality.max_repair_attempts`` tries.
- ``duplicate``: two or more rows inside one grid slot (the primary key forbids an identical ts, so
  this is an off-grid ts beside the on-grid bar). One issue per slot; logged, nothing is deleted.
- ``zero_volume``: a run of consecutive bars with ``vol = 0``. One issue per run. Logged, never
  "repaired": stock and commodity perps legitimately have quiet hours.
- ``outlier``: a close-to-close log return beyond ``quality.outlier_sigma`` standard deviations of
  the previous ``quality.outlier_window`` returns (outliers already found are left out of that
  window). A spike that reverts on the next bar is one outlier, not two. Logged; the bar is kept.

Issues are keyed (env, inst_id, dataset, kind, from_ts); a rerun updates an open issue's ``to_ts``
and ``detail`` instead of adding another, and leaves repaired issues alone.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import BackfillJob, DataQualityIssue
from atlas_shared.db import Candle as CandleRow
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert

from trading_core.backfill.plan import DONE
from trading_core.backfill.runner import upsert_rows
from trading_core.marketdata.grid import CANDLES_PREFIX, bar_delta, candle_rows, slot
from trading_core.okx import OkxClient

logger = logging.getLogger("trading_core.marketdata")

JOB = "quality"

GAP = "gap"
DUPLICATE = "duplicate"
ZERO_VOLUME = "zero_volume"
OUTLIER = "outlier"

ClientFactory = Callable[[str], OkxClient]


def quality_cfg() -> dict[str, Any]:
    cfg: dict[str, Any] = DEFAULTS["quality"]
    return cfg


@dataclass(frozen=True)
class Bar:
    ts: datetime
    c: Decimal
    vol: Decimal


@dataclass(frozen=True)
class Finding:
    kind: str
    from_ts: datetime
    to_ts: datetime
    detail: dict[str, Any]


# ---- detection (pure, over one series in ts order) ------------------------------------------


def find_gaps(bar: str, bars: Sequence[Bar]) -> list[Finding]:
    step = bar_delta(bar)
    slots = sorted({slot(bar, b.ts) for b in bars})
    out: list[Finding] = []
    for prev, nxt in itertools.pairwise(slots):
        missing = int((nxt - prev) / step) - 1
        if missing > 0:
            out.append(Finding(GAP, prev + step, nxt - step, {"missing_bars": missing}))
    return out


def find_duplicates(bar: str, bars: Sequence[Bar]) -> list[Finding]:
    by_slot: dict[datetime, list[datetime]] = defaultdict(list)
    for b in bars:
        by_slot[slot(bar, b.ts)].append(b.ts)
    return [
        Finding(DUPLICATE, s, max(ts), {"rows": len(ts), "ts": [t.isoformat() for t in sorted(ts)]})
        for s, ts in sorted(by_slot.items())
        if len(ts) > 1
    ]


def find_zero_volume(bars: Sequence[Bar]) -> list[Finding]:
    out: list[Finding] = []
    run: list[Bar] = []
    for b in [*bars, None]:
        if b is not None and b.vol == 0:
            run.append(b)
            continue
        if run:
            out.append(Finding(ZERO_VOLUME, run[0].ts, run[-1].ts, {"bars": len(run)}))
            run = []
    return out


def find_outliers(bars: Sequence[Bar], *, sigma: float, window: int, min_samples: int) -> list[Finding]:
    """Close-to-close log returns beyond ``sigma`` std devs of the previous ``window`` normal returns."""
    out: list[Finding] = []
    history: list[float] = []
    flagged_prev = False
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if prev.c <= 0 or cur.c <= 0:
            flagged_prev = False
            continue
        r = math.log(cur.c / prev.c)  # Decimal ratio -> float log; statistics, not money
        sample = history[-window:]
        if len(sample) >= min_samples:
            mean = statistics.fmean(sample)
            sd = statistics.pstdev(sample)
            limit = sigma * sd
            is_out = sd > 0 and abs(r - mean) > limit
            if is_out and flagged_prev and i >= 2 and bars[i - 2].c > 0:
                # The bar after a spike: if it brings price back next to the pre-spike close, it is the
                # reversion of the same outlier, not a second one.
                if abs(math.log(cur.c / bars[i - 2].c) - mean) <= limit:
                    flagged_prev = False
                    continue  # nor does its return join the window
            if is_out:
                out.append(
                    Finding(
                        OUTLIER,
                        cur.ts,
                        cur.ts,
                        {
                            "sigma": round(abs(r - mean) / sd, 2),
                            "return": round(r, 8),
                            "window": len(sample),
                            "prev_c": str(prev.c),
                            "c": str(cur.c),
                        },
                    )
                )
                flagged_prev = True
                continue
        history.append(r)
        flagged_prev = False
    return out


# ---- the checker ----------------------------------------------------------------------------


@dataclass
class QualityResult:
    env: str
    series: int = 0
    found: dict[str, int] = field(default_factory=lambda: {GAP: 0, DUPLICATE: 0, ZERO_VOLUME: 0, OUTLIER: 0})
    new: int = 0
    repaired: int = 0
    repair_failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "series": self.series,
            "found": dict(self.found),
            "new": self.new,
            "repaired": self.repaired,
            "repair_failed": self.repair_failed,
        }


class QualityChecker:
    def __init__(
        self,
        env: str,
        engine: Engine,
        *,
        client_factory: ClientFactory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        cfg = quality_cfg()
        self.env = env
        self.engine = engine
        self._factory: ClientFactory = client_factory or OkxClient
        self._now = now or (lambda: datetime.now(UTC))
        self.window_bars = int(cfg["window_bars"])
        self.sigma = float(cfg["outlier_sigma"])
        self.outlier_window = int(cfg["outlier_window"])
        self.min_samples = int(cfg["outlier_min_samples"])
        self.page_limit = int(cfg["repair_page_limit"])
        self.max_attempts = int(cfg["max_repair_attempts"])

    # ---- reading ----------------------------------------------------------------------------

    def _series(self, inst_id: str | None) -> list[tuple[str, str]]:
        stmt = select(BackfillJob.inst_id, BackfillJob.dataset).where(
            BackfillJob.env == self.env,
            BackfillJob.status == DONE,
            BackfillJob.dataset.startswith(CANDLES_PREFIX),
        )
        if inst_id is not None:
            stmt = stmt.where(BackfillJob.inst_id == inst_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(BackfillJob.inst_id, BackfillJob.dataset)).all()
        return [(i, d[len(CANDLES_PREFIX) :]) for i, d in rows]

    def _bars(self, inst_id: str, bar: str) -> list[Bar]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(CandleRow.ts, CandleRow.c, CandleRow.vol)
                .where(CandleRow.env == self.env, CandleRow.inst_id == inst_id, CandleRow.bar == bar)
                .order_by(CandleRow.ts.desc())
                .limit(self.window_bars)
            ).all()
        return [Bar(r.ts, Decimal(r.c), Decimal(r.vol)) for r in reversed(rows)]

    def detect(self, bar: str, bars: Sequence[Bar]) -> list[Finding]:
        return [
            *find_gaps(bar, bars),
            *find_duplicates(bar, bars),
            *find_zero_volume(bars),
            *find_outliers(bars, sigma=self.sigma, window=self.outlier_window, min_samples=self.min_samples),
        ]

    # ---- recording --------------------------------------------------------------------------

    def _record(self, inst_id: str, bar: str, findings: Sequence[Finding]) -> int:
        """Upsert the findings; returns how many were new issues."""
        if not findings:
            return 0
        now = self._now()
        rows = [
            {
                "id": uuid.uuid4(),
                "env": self.env,
                "inst_id": inst_id,
                "dataset": CANDLES_PREFIX + bar,
                "kind": f.kind,
                "from_ts": f.from_ts,
                "to_ts": f.to_ts,
                "detail": f.detail,
                "detected_at": now,
                "repaired_at": None,
                "created_at": now,
                "updated_at": now,
            }
            for f in findings
        ]
        ins = insert(DataQualityIssue).values(rows)
        stmt: Any = ins.on_conflict_do_update(
            index_elements=["env", "inst_id", "dataset", "kind", "from_ts"],
            set_={
                "to_ts": ins.excluded.to_ts,
                # Keep the repair bookkeeping a gap has collected across runs.
                "detail": func.coalesce(DataQualityIssue.detail, literal_column("'{}'::jsonb")).op("||")(
                    ins.excluded.detail
                ),
                "updated_at": ins.excluded.updated_at,
            },
            where=DataQualityIssue.repaired_at.is_(None),
        ).returning(literal_column("(xmax = 0)"))
        with self.engine.begin() as conn:
            return sum(1 for (inserted,) in conn.execute(stmt).all() if inserted)

    # ---- repair -----------------------------------------------------------------------------

    def _open_gaps(self, inst_id: str | None) -> list[Any]:
        stmt = select(DataQualityIssue).where(
            DataQualityIssue.env == self.env,
            DataQualityIssue.kind == GAP,
            DataQualityIssue.repaired_at.is_(None),
            DataQualityIssue.dataset.startswith(CANDLES_PREFIX),
        )
        if inst_id is not None:
            stmt = stmt.where(DataQualityIssue.inst_id == inst_id)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(DataQualityIssue.from_ts)).all()
        return [r for r in rows if int((r.detail or {}).get("repair_attempts", 0)) < self.max_attempts]

    def _missing(self, inst_id: str, bar: str, from_ts: datetime, to_ts: datetime) -> int:
        step = bar_delta(bar)
        expected = int((to_ts - from_ts) / step) + 1
        with self.engine.connect() as conn:
            stored = conn.execute(
                select(CandleRow.ts).where(
                    CandleRow.env == self.env,
                    CandleRow.inst_id == inst_id,
                    CandleRow.bar == bar,
                    CandleRow.ts >= from_ts,
                    CandleRow.ts < to_ts + step,
                )
            ).scalars()
            have = {slot(bar, ts) for ts in stored}
        return max(0, expected - len(have))

    def _save_repair(self, issue: Any, rows: Sequence[dict[str, Any]], bar: str) -> bool:
        detail = dict(issue.detail or {})
        detail["repair_attempts"] = int(detail.get("repair_attempts", 0)) + 1
        with self.engine.begin() as conn:
            upsert_rows(conn, CandleRow, rows)
        still = self._missing(issue.inst_id, bar, issue.from_ts, issue.to_ts)
        detail["still_missing"] = still
        now = self._now()
        with self.engine.begin() as conn:
            conn.execute(
                update(DataQualityIssue)
                .where(DataQualityIssue.id == issue.id)
                .values(detail=detail, repaired_at=now if still == 0 else None, updated_at=now)
            )
        return still == 0

    async def _repair(self, okx: OkxClient, issue: Any, result: QualityResult) -> None:
        bar = issue.dataset[len(CANDLES_PREFIX) :]
        step = bar_delta(bar)
        rows: list[dict[str, Any]] = []
        try:
            async for page in okx.iter_candles(
                issue.inst_id,
                bar=bar,
                after=issue.to_ts + step,  # strictly older than the bar after the gap
                since=issue.from_ts,
                limit=self.page_limit,
                history=True,
            ):
                inside = [c for c in page if issue.from_ts <= c.ts <= issue.to_ts]
                rows.extend(candle_rows(self.env, issue.inst_id, bar, inside))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.repair_failed += 1
            logger.warning(
                "gap repair failed",
                extra={"inst_id": issue.inst_id, "bar": bar, "error": f"{type(exc).__name__}: {exc}"[:500]},
            )
            return
        if await asyncio.to_thread(self._save_repair, issue, rows, bar):
            result.repaired += 1
        else:
            result.repair_failed += 1

    # ---- run --------------------------------------------------------------------------------

    async def run(self, inst_id: str | None = None) -> QualityResult:
        result = QualityResult(self.env)
        for series_inst, bar in await asyncio.to_thread(self._series, inst_id):
            bars = await asyncio.to_thread(self._bars, series_inst, bar)
            findings = self.detect(bar, bars)
            for f in findings:
                result.found[f.kind] += 1
            result.new += await asyncio.to_thread(self._record, series_inst, bar, findings)
            result.series += 1
        gaps = await asyncio.to_thread(self._open_gaps, inst_id)
        if gaps:
            async with self._factory(self.env) as okx:
                for issue in gaps:
                    await self._repair(okx, issue, result)
        if result.new or result.repaired or result.repair_failed:
            logger.info("data quality checked", extra=result.as_dict())
        return result


def quality_job(
    env: str, engine: Engine, *, client_factory: ClientFactory | None = None
) -> Callable[[], Any]:
    async def run() -> dict[str, Any]:
        return (await QualityChecker(env, engine, client_factory=client_factory).run()).as_dict()

    return run
