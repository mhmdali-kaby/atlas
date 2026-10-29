"""ATL-47: closed bars kept fresh by REST polling, and candle quality checks with gap repair.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with respx.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import statistics
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import BackfillJob, Candle, DataQualityIssue, Instrument, make_engine
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.backfill import ensure_jobs, pause_jobs
from trading_core.config import Settings
from trading_core.marketdata import CandlePoller, QualityChecker
from trading_core.marketdata.grid import slot
from trading_core.okx import OkxClient
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

BASE = "https://www.okx.com"
ENV = "demo"
INST = "BTC-USDT-SWAP"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
HOUR = timedelta(hours=1)
TEST_DB = testing.db_settings()
DAY = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


def _wipe(conn: Connection) -> None:
    for model in (DataQualityIssue, BackfillJob, Candle, Instrument):
        conn.execute(delete(model).where(model.env == ENV))


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    with engine.begin() as conn:
        _wipe(conn)
    yield engine
    with engine.begin() as conn:
        _wipe(conn)


def ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def done_job(engine: Engine, dataset: str, inst_id: str = INST) -> None:
    with engine.begin() as conn:
        conn.execute(
            BackfillJob.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                dataset=dataset,
                target_from=DAY - timedelta(days=730),
                rows_written=0,
                status="done",
                created_at=DAY,
                updated_at=DAY,
            )
        )


def seed(engine: Engine, bars: list[tuple[datetime, Decimal, Decimal]], bar: str = "1H") -> None:
    """(ts, close, volume) rows; open/high/low derived from the close."""
    with engine.begin() as conn:
        conn.execute(
            Candle.__table__.insert(),
            [
                {
                    "env": ENV,
                    "inst_id": INST,
                    "bar": bar,
                    "ts": ts,
                    "o": c,
                    "h": c,
                    "l": c,
                    "c": c,
                    "vol": v,
                    "confirmed": True,
                }
                for ts, c, v in bars
            ],
        )


def stored(engine: Engine, bar: str = "1H") -> dict[datetime, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(Candle).where(Candle.env == ENV, Candle.inst_id == INST, Candle.bar == bar)
        ).all()
    return {r.ts: r for r in rows}


def issues(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(DataQualityIssue)
                .where(DataQualityIssue.env == ENV)
                .order_by(DataQualityIssue.kind, DataQualityIssue.from_ts)
            ).all()
        )


def okx_row(ts: datetime, close: str, confirm: bool) -> list[str]:
    return [str(ms(ts)), close, close, close, close, "10", "0.1", "1000", "1" if confirm else "0"]


class Book:
    """Serves market/candles and market/history-candles like OKX: newest first, ``after`` = strictly
    older, ``before`` = strictly newer, ``limit`` newest matching bars. ``bars()`` is evaluated per
    request so a test clock can move bars from forming to confirmed."""

    def __init__(self, bars: Callable[[], list[list[str]]]) -> None:
        self.bars = bars
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append((request.url.path, params))
        rows = sorted(self.bars(), key=lambda r: int(r[0]), reverse=True)
        if "after" in params:
            rows = [r for r in rows if int(r[0]) < int(params["after"])]
        if "before" in params:
            rows = [r for r in rows if int(r[0]) > int(params["before"])]
        rows = rows[: int(params.get("limit", "100"))]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


def factory() -> Callable[[str], OkxClient]:
    async def no_sleep(_: float) -> None:
        return None

    def make(env: str) -> OkxClient:
        return OkxClient(env, http=httpx.AsyncClient(base_url=BASE), sleep=no_sleep)

    return make


def smooth(n: int, start: datetime, *, seed_: int = 7) -> list[tuple[datetime, Decimal, Decimal]]:
    """n hourly bars with small, deterministic close-to-close noise (about 0.1 % per bar)."""
    rng = random.Random(seed_)  # noqa: S311 - deterministic test data
    price = 100.0
    out = []
    for i in range(n):
        price *= math.exp(rng.gauss(0, 0.001))
        out.append((start + i * HOUR, Decimal(f"{price:.6f}"), Decimal("10")))
    return out


# ---- AC1: the poller writes a closed 1H bar within the delay, never refetching older bars --------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_poller_writes_the_closed_hour_within_the_delay(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    clock = [DAY.replace(hour=10, minute=59, second=58)]
    # Stored: 00:00 .. 09:00 opens (09:00 bar closed at 10:00), with a close OKX does not have.
    seed(clean, [(DAY + i * HOUR, Decimal("50"), Decimal("1")) for i in range(10)])
    done_job(clean, "candles:1H")

    def okx_bars() -> list[list[str]]:
        forming = slot("1H", clock[0])
        n = int((forming - DAY) / HOUR)
        return [okx_row(DAY + i * HOUR, "100", True) for i in range(n)] + [okx_row(forming, "101", False)]

    book = Book(okx_bars)
    respx_mock.get("/api/v5/market/candles").mock(side_effect=book)
    poller = CandlePoller(ENV, clean, client_factory=factory(), now=lambda: clock[0])
    close = DAY.replace(hour=11)
    written_at: datetime | None = None

    for _ in range(30):  # one tick per second, like the scheduler's tick_seconds = 1
        await poller.tick()
        if DAY.replace(hour=10) in stored(clean) and written_at is None:
            written_at = clock[0]
        clock[0] += timedelta(seconds=1)

    assert written_at is not None
    assert close <= written_at <= close + poller.delay + timedelta(seconds=1)
    rows = stored(clean)
    assert sorted(rows) == [DAY + i * HOUR for i in range(11)]  # the 10:00 bar, and not the forming 11:00
    assert rows[DAY.replace(hour=10)].c == Decimal("100") and rows[DAY.replace(hour=10)].confirmed
    assert all(rows[DAY + i * HOUR].c == Decimal("50") for i in range(10))  # older bars untouched
    # Two requests: the catch-up at start (nothing closed yet) and the one after the 11:00 close; both
    # ask only for bars newer than the newest stored one (09:00), never older ones.
    assert [p["before"] for _, p in book.calls] == [str(ms(DAY.replace(hour=9)))] * 2
    assert all("after" not in p and p["bar"] == "1H" for _, p in book.calls)

    await poller.tick()  # same hour again: nothing due, no request
    assert len(book.calls) == 2


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_poller_skips_series_whose_backfill_is_not_done(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    seed(clean, [(DAY, Decimal("50"), Decimal("1"))])
    with clean.begin() as conn:
        conn.execute(
            BackfillJob.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=INST,
                dataset="candles:1H",
                target_from=DAY,
                rows_written=0,
                status="running",
                created_at=DAY,
                updated_at=DAY,
            )
        )
    route = respx_mock.get("/api/v5/market/candles").mock(
        return_value=httpx.Response(200, json={"code": "0", "msg": "", "data": []})
    )
    poller = CandlePoller(ENV, clean, client_factory=factory(), now=lambda: DAY + 5 * HOUR)
    result = await poller.tick()
    assert result["series"] == 0 and not route.called


# ---- AC2: a 3-bar gap is one issue, repaired from REST ------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_three_bar_gap_is_one_issue_and_is_repaired(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    start = DAY - timedelta(days=3)
    full = smooth(60, start)
    missing = {start + i * HOUR for i in (30, 31, 32)}
    seed(clean, [b for b in full if b[0] not in missing])
    done_job(clean, "candles:1H")
    book = Book(lambda: [okx_row(ts, str(c), True) for ts, c, _ in full])
    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=book)
    now = DAY + HOUR

    first = await QualityChecker(ENV, clean, client_factory=factory(), now=lambda: now).run()

    found = issues(clean)
    assert [(i.kind, i.from_ts, i.to_ts) for i in found] == [("gap", start + 30 * HOUR, start + 32 * HOUR)]
    gap = found[0]
    assert gap.dataset == "candles:1H" and gap.detail["missing_bars"] == 3
    assert gap.repaired_at == now and gap.detail["still_missing"] == 0
    rows = stored(clean)
    assert all(ts in rows for ts in missing)
    assert rows[start + 31 * HOUR].c == full[31][1]
    assert first.new == 1 and first.repaired == 1
    # The repair asked for history strictly older than the bar after the gap.
    assert book.calls[0][0].endswith("history-candles")
    assert book.calls[0][1]["after"] == str(ms(start + 33 * HOUR))

    second = await QualityChecker(ENV, clean, client_factory=factory(), now=lambda: now).run()
    assert second.new == 0 and second.found["gap"] == 0
    assert len(issues(clean)) == 1


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_gap_okx_does_not_have_stays_open_and_stops_retrying(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    start = DAY - timedelta(days=3)
    full = smooth(40, start)
    seed(clean, [b for i, b in enumerate(full) if i != 20])
    done_job(clean, "candles:1H")
    have = [okx_row(ts, str(c), True) for i, (ts, c, _) in enumerate(full) if i != 20]
    book = Book(lambda: have)
    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=book)

    for _ in range(5):
        await QualityChecker(ENV, clean, client_factory=factory(), now=lambda: DAY).run()

    (gap,) = issues(clean)
    assert gap.repaired_at is None
    assert gap.detail["repair_attempts"] == 3 and gap.detail["still_missing"] == 1
    assert len(book.calls) == 3  # quality.max_repair_attempts, then no more requests


# ---- AC3: a 10-sigma spike is an outlier and the bar is kept ------------------------------------


async def test_ten_sigma_spike_is_logged_and_kept(clean: Engine) -> None:
    start = DAY - timedelta(days=3)
    bars = smooth(60, start)
    closes = [float(c) for _, c, _ in bars[:45]]
    returns = [math.log(b / a) for a, b in itertools.pairwise(closes)]
    sd = statistics.pstdev(returns)
    spike_ts, _, vol = bars[45]
    spike = Decimal(f"{float(bars[44][1]) * math.exp(10.5 * sd):.6f}")  # a ~10-sigma jump
    bars[45] = (spike_ts, spike, vol)  # ... that reverts on the next bar
    seed(clean, bars)
    done_job(clean, "candles:1H")

    result = await QualityChecker(ENV, clean, now=lambda: DAY).run()

    found = issues(clean)
    assert [(i.kind, i.from_ts) for i in found] == [("outlier", spike_ts)]
    assert found[0].detail["sigma"] >= 10
    assert found[0].repaired_at is None
    assert result.found["outlier"] == 1
    rows = stored(clean)
    assert len(rows) == 60 and rows[spike_ts].c == spike  # kept, unchanged


# ---- AC4: duplicates and zero-volume bars: one issue each, never duplicated on rerun -------------


async def test_duplicate_and_zero_volume_are_one_issue_each_and_rerun_adds_none(clean: Engine) -> None:
    start = DAY - timedelta(days=3)
    bars = smooth(60, start)
    zero_ts, c, _ = bars[20]
    bars[20] = (zero_ts, c, Decimal("0"))
    dup_slot = start + 40 * HOUR
    bars.append((dup_slot + timedelta(seconds=30), bars[40][1], Decimal("10")))  # off-grid second row
    seed(clean, bars)
    done_job(clean, "candles:1H")

    first = await QualityChecker(ENV, clean, now=lambda: DAY).run()
    second = await QualityChecker(ENV, clean, now=lambda: DAY + HOUR).run()

    found = issues(clean)
    assert [(i.kind, i.from_ts) for i in found] == [("duplicate", dup_slot), ("zero_volume", zero_ts)]
    assert found[0].detail["rows"] == 2
    assert found[1].detail["bars"] == 1
    assert first.new == 2 and second.new == 0
    assert all(i.detected_at == DAY and i.repaired_at is None for i in found)
    assert len(stored(clean)) == 61  # nothing deleted


async def test_zero_volume_run_is_one_issue(clean: Engine) -> None:
    start = DAY - timedelta(days=3)
    bars = smooth(50, start)
    for i in range(10, 16):  # a quiet stock-perp session
        bars[i] = (bars[i][0], bars[i][1], Decimal("0"))
    seed(clean, bars)
    done_job(clean, "candles:1H")

    await QualityChecker(ENV, clean, now=lambda: DAY).run()

    (issue,) = issues(clean)
    assert issue.kind == "zero_volume"
    assert (issue.from_ts, issue.to_ts) == (bars[10][0], bars[15][0]) and issue.detail["bars"] == 6


# ---- on-demand endpoint -------------------------------------------------------------------------


def post(client: TestClient, path: str, body: dict[str, Any]) -> httpx.Response:
    raw = json.dumps(body).encode()
    return client.post(
        path,
        content=raw,
        headers={"Content-Type": "application/json", **signed_headers(SECRET, "POST", path, raw)},
    )


def test_quality_check_endpoint(clean: Engine) -> None:
    start = DAY - timedelta(days=3)
    bars = smooth(50, start)
    bars[5] = (bars[5][0], bars[5][1], Decimal("0"))
    seed(clean, bars)
    done_job(clean, "candles:1H")
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    client = TestClient(create_app(settings, scheduler=Scheduler(), engine=clean))

    assert client.post("/internal/quality/check", json={"inst_id": INST}).status_code == 401
    one = post(client, "/internal/quality/check", {"env": ENV, "inst_id": INST})
    assert one.status_code == 200
    assert one.json()["found"]["zero_volume"] == 1 and one.json()["new"] == 1 and one.json()["series"] == 1
    everything = post(client, "/internal/quality/check", {"env": ENV})
    assert everything.status_code == 202 and everything.json()["started"] is False  # no job registered
    assert post(client, "/internal/quality/check", {"env": "live"}).status_code == 409
    assert post(client, "/internal/quality/check", {"inst_id": "x; drop"}).status_code == 422


def test_default_scheduler_registers_poller_and_quality_jobs(clean: Engine) -> None:
    from trading_core.app import default_scheduler

    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    jobs = default_scheduler(settings, clean).jobs
    assert jobs["candle_poller"].interval_seconds == 1
    assert jobs["quality"].interval_seconds == 900


# ---- criterion 5 and the TL's pause follow-up ---------------------------------------------------


def _instrument(engine: Engine, inst_id: str, asset_class: str = "crypto") -> None:
    with engine.begin() as conn:
        conn.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                inst_family=inst_id.rsplit("-", 1)[0],
                lot_sz=Decimal("1"),
                min_sz=Decimal("1"),
                tick_sz=Decimal("0.1"),
                state="live",
                asset_class=asset_class,
                created_at=DAY,
                updated_at=DAY,
            )
        )


def test_new_5m_job_goes_back_180_days(clean: Engine) -> None:
    _instrument(clean, INST)
    ensure_jobs(clean, ENV, now=DAY)
    with clean.connect() as conn:
        job = conn.execute(
            select(BackfillJob).where(BackfillJob.env == ENV, BackfillJob.dataset == "candles:5m")
        ).one()
    assert job.created_at - job.target_from == timedelta(days=180)


def test_pause_with_inst_id_pauses_its_currency_open_interest_job(clean: Engine) -> None:
    _instrument(clean, INST)
    _instrument(clean, "ETH-USDT-SWAP")
    ensure_jobs(clean, ENV, now=DAY)

    paused = pause_jobs(clean, ENV, inst_id=INST, now=DAY)

    with clean.connect() as conn:
        rows = conn.execute(select(BackfillJob).where(BackfillJob.env == ENV)).all()
    status = {(r.inst_id, r.dataset): r.status for r in rows}
    assert status[("BTC", "open_interest")] == "paused"
    assert {s for (i, _), s in status.items() if i == INST} == {"paused"}
    assert status[("ETH", "open_interest")] == "pending"
    assert {s for (i, _), s in status.items() if i == "ETH-USDT-SWAP"} == {"pending"}
    assert paused == sum(1 for (i, _) in status if i in (INST, "BTC"))
