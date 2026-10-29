"""ATL-46: the resumable history backfill of candles, funding and open interest.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with respx (or served from the recorded fixtures with
ATLAS_OKX_FIXTURES=1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import BackfillJob, Candle, FundingRate, Instrument, OpenInterest, make_engine
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.backfill import BackfillRunner, ensure_jobs, pause_jobs, upsert_rows
from trading_core.backfill.plan import market_of
from trading_core.config import Settings
from trading_core.okx import OkxClient
from trading_core.okx.fixtures import ENABLE_VAR
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

BASE = "https://www.okx.com"
ENV = "demo"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
HOUR_MS = 3_600_000
TEST_DB = testing.db_settings()
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


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
    for model in (BackfillJob, Candle, FundingRate, OpenInterest, Instrument):
        conn.execute(delete(model).where(model.env == ENV))


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    with engine.begin() as conn:
        _wipe(conn)
    yield engine
    with engine.begin() as conn:
        _wipe(conn)


def add_instrument(
    engine: Engine,
    inst_id: str,
    *,
    inst_type: str = "SWAP",
    asset_class: str = "crypto",
    state: str = "live",
) -> None:
    family = None if inst_type == "SPOT" else inst_id.rsplit("-", 1)[0]
    with engine.begin() as conn:
        conn.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type=inst_type,
                inst_family=family,
                base_ccy=inst_id.split("-", 1)[0] if inst_type == "SPOT" else None,
                lot_sz=Decimal("1"),
                min_sz=Decimal("1"),
                tick_sz=Decimal("0.1"),
                state=state,
                asset_class=asset_class,
                created_at=NOW,
                updated_at=NOW,
            )
        )


def add_job(engine: Engine, inst_id: str, dataset: str, target_from: datetime) -> uuid.UUID:
    job_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            BackfillJob.__table__.insert().values(
                id=job_id,
                env=ENV,
                inst_id=inst_id,
                dataset=dataset,
                target_from=target_from,
                rows_written=0,
                status="pending",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return job_id


def job_row(engine: Engine, job_id: uuid.UUID) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(BackfillJob).where(BackfillJob.id == job_id)).one()


def jobs(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(BackfillJob).where(BackfillJob.env == ENV)).all())


def candle_ts(engine: Engine, inst_id: str, bar: str) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(Candle.ts).where(Candle.env == ENV, Candle.inst_id == inst_id, Candle.bar == bar)
        ).scalars()
        return sorted(int(ts.timestamp() * 1000) for ts in rows)


def bar_row(ts_ms: int, confirm: str = "1") -> list[str]:
    return [str(ts_ms), "100.0", "101.0", "99.0", "100.5", "12.5", "0.125", "1256.25", confirm]


class HistoryBook:
    """Serves market/history-candles for one or more instruments like OKX: newest first, ``after``
    returns bars strictly older than it, ``limit`` bars per page."""

    def __init__(self, bars: dict[str, list[int]]) -> None:
        self.bars = {k: sorted(v, reverse=True) for k, v in bars.items()}
        self.calls: list[dict[str, str]] = []
        self.block_on_call: int | None = None
        self.release = asyncio.Event()
        self.reached = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append(params)
        if self.block_on_call is not None and len(self.calls) == self.block_on_call:
            self.reached.set()
            await self.release.wait()  # "the process is killed" while this request is in flight
        bars = self.bars.get(params["instId"], [])
        after = params.get("after")
        if after is not None:
            bars = [b for b in bars if b < int(after)]
        page = bars[: int(params.get("limit", "100"))]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": [bar_row(b) for b in page]})


def factory(**kwargs: Any) -> Any:
    async def no_sleep(_: float) -> None:
        return None

    def make(env: str) -> OkxClient:
        return OkxClient(env, http=httpx.AsyncClient(base_url=BASE), sleep=no_sleep, **kwargs)

    return make


def hourly(start: datetime, n: int) -> list[int]:
    first = int(start.timestamp() * 1000)
    return [first + i * HOUR_MS for i in range(n)]


# ---- AC1: a 1H job writes every mocked bar once and ends done ----------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_hourly_job_writes_every_bar_once_and_ends_done(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    expected = hourly(NOW - timedelta(hours=250), 250)
    book = HistoryBook({"BTC-USDT-SWAP": expected})
    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=book)
    job_id = add_job(clean, "BTC-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))

    result = await BackfillRunner(ENV, clean, client_factory=factory()).run()

    assert candle_ts(clean, "BTC-USDT-SWAP", "1H") == expected  # every bar, once (the key forbids dupes)
    job = job_row(clean, job_id)
    assert job.status == "done"
    assert job.rows_written == 250
    assert job.cursor_ts is not None and int(job.cursor_ts.timestamp() * 1000) == expected[0]
    assert result.done == 1 and result.failed == 0
    # 100 + 100 + 50 bars, then one empty page that says "nothing older".
    assert len(book.calls) == 4
    assert all(c["bar"] == "1H" and c["limit"] == "100" for c in book.calls)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_job_stops_at_target_from_and_skips_forming_bar(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    bars = hourly(NOW - timedelta(hours=300), 300)
    book = HistoryBook({"BTC-USDT-SWAP": bars})

    async def with_forming(request: httpx.Request) -> httpx.Response:
        resp = await book(request)
        body = json.loads(resp.content)
        if "after" not in request.url.params and body["data"]:
            body["data"][0][8] = "0"  # the newest bar is still forming
        return httpx.Response(200, json=body)

    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=with_forming)
    target = datetime.fromtimestamp(bars[150] / 1000, UTC)
    job_id = add_job(clean, "BTC-USDT-SWAP", "candles:1H", target)

    await BackfillRunner(ENV, clean, client_factory=factory()).run()

    written = candle_ts(clean, "BTC-USDT-SWAP", "1H")
    assert written == bars[150:-1]  # nothing older than target_from, no forming bar
    assert job_row(clean, job_id).status == "done"
    assert len(book.calls) == 2  # the walk reached target_from; no extra request


# ---- AC2: a killed runner resumes from cursor_ts -----------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_cancelled_runner_resumes_from_cursor(respx_mock: respx.MockRouter, clean: Engine) -> None:
    expected = hourly(NOW - timedelta(hours=450), 450)
    book = HistoryBook({"BTC-USDT-SWAP": expected})
    book.block_on_call = 3  # two pages are committed, the third request is in flight when we kill it
    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=book)
    job_id = add_job(clean, "BTC-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))

    task = asyncio.create_task(BackfillRunner(ENV, clean, client_factory=factory()).run())
    await asyncio.wait_for(book.reached.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    killed = job_row(clean, job_id)
    assert killed.status == "running"  # left as it was; the next run claims it again
    assert killed.rows_written == 200
    cursor_ms = int(killed.cursor_ts.timestamp() * 1000) if killed.cursor_ts else None
    assert cursor_ms == sorted(expected, reverse=True)[199]

    first_run_calls = len(book.calls)
    book.block_on_call = None
    await BackfillRunner(ENV, clean, client_factory=factory()).run()

    resumed = book.calls[first_run_calls:]
    assert resumed[0]["after"] == str(cursor_ms)  # starts exactly at the cursor
    assert all("after" in c and int(c["after"]) <= cursor_ms for c in resumed)  # nothing newer again
    assert candle_ts(clean, "BTC-USDT-SWAP", "1H") == expected
    done = job_row(clean, job_id)
    assert done.status == "done"
    assert done.rows_written == 450


# ---- AC3: a failing page fails only its own job ------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_failing_page_marks_only_that_job_failed(respx_mock: respx.MockRouter, clean: Engine) -> None:
    good = hourly(NOW - timedelta(hours=120), 120)
    book = HistoryBook({"ETH-USDT-SWAP": good})
    bad_calls = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal bad_calls
        if request.url.params["instId"] == "BTC-USDT-SWAP":
            bad_calls += 1
            return httpx.Response(500, text="upstream down")
        return await book(request)

    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=respond)
    bad = add_job(clean, "BTC-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))
    ok = add_job(clean, "ETH-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))

    result = await BackfillRunner(ENV, clean, client_factory=factory(), concurrency=2).run()

    failed = job_row(clean, bad)
    assert bad_calls == 3  # the client's own three attempts
    assert failed.status == "failed"
    assert failed.last_error and "500" in failed.last_error
    assert failed.rows_written == 0
    assert job_row(clean, ok).status == "done"
    assert candle_ts(clean, "ETH-USDT-SWAP", "1H") == good
    assert result.failed == 1 and result.done == 1


# ---- funding and open interest walks -----------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_funding_and_open_interest_walk_back_until_okx_has_nothing_older(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    eight_h = 8 * HOUR_MS
    funding = [int(NOW.timestamp() * 1000) - i * eight_h for i in range(150)]

    def funding_page(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        rows = [t for t in funding if after is None or t < int(after)][: int(request.url.params["limit"])]
        data = [
            {
                "instId": "BTC-USDT-SWAP",
                "instType": "SWAP",
                "fundingRate": "0.0001",
                "realizedRate": "0.0001",
                "fundingTime": str(t),
                "method": "current_period",
                "formulaType": "withRate",
            }
            for t in rows
        ]
        return httpx.Response(200, json={"code": "0", "msg": "", "data": data})

    day = 24 * HOUR_MS
    oi = [int(NOW.timestamp() * 1000) - i * day for i in range(30)]
    oi_calls: list[dict[str, str]] = []

    def oi_page(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        oi_calls.append(params)
        end = params.get("end")
        rows = [t for t in oi if end is None or t <= int(end)][:20]
        return httpx.Response(
            200, json={"code": "0", "msg": "", "data": [[str(t), "1000.5", "20"] for t in rows]}
        )

    respx_mock.get("/api/v5/public/funding-rate-history").mock(side_effect=funding_page)
    respx_mock.get("/api/v5/rubik/stat/contracts/open-interest-volume").mock(side_effect=oi_page)
    far = datetime(2009, 1, 1, tzinfo=UTC)
    f_job = add_job(clean, "BTC-USDT-SWAP", "funding", far)
    o_job = add_job(clean, "BTC", "open_interest", far)

    await BackfillRunner(ENV, clean, client_factory=factory()).run()

    with clean.connect() as conn:
        n_funding = conn.execute(
            select(func.count()).select_from(FundingRate).where(FundingRate.env == ENV)
        ).scalar()
        oi_rows = conn.execute(select(OpenInterest).where(OpenInterest.env == ENV)).all()
    assert n_funding == 150
    assert len(oi_rows) == 30 and {r.inst_id for r in oi_rows} == {"BTC"}
    assert all(r.oi_usd == Decimal("1000.5") for r in oi_rows)
    assert oi_calls[0]["period"] == "1D" and oi_calls[0]["ccy"] == "BTC"
    for job_id, n in ((f_job, 150), (o_job, 30)):
        row = job_row(clean, job_id)
        assert (row.status, row.rows_written) == ("done", n)


# ---- pause -------------------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_pause_stops_before_the_next_write(respx_mock: respx.MockRouter, clean: Engine) -> None:
    bars = hourly(NOW - timedelta(hours=450), 450)
    book = HistoryBook({"BTC-USDT-SWAP": bars})
    book.block_on_call = 2
    respx_mock.get("/api/v5/market/history-candles").mock(side_effect=book)
    job_id = add_job(clean, "BTC-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))

    task = asyncio.create_task(BackfillRunner(ENV, clean, client_factory=factory()).run())
    await asyncio.wait_for(book.reached.wait(), timeout=10)
    assert pause_jobs(clean, ENV) == 1
    book.release.set()
    result = await asyncio.wait_for(task, timeout=10)

    row = job_row(clean, job_id)
    assert row.status == "paused"
    assert row.rows_written == 100  # the page fetched after the pause was not written
    assert len(candle_ts(clean, "BTC-USDT-SWAP", "1H")) == 100
    assert result.stopped == 1


# ---- batching ----------------------------------------------------------------------------------


def test_upsert_rows_dedupes_sorts_and_chunks(clean: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from trading_core.backfill import plan

    cfg = dict(plan.runner_cfg())
    cfg["max_bind_params"] = 12 * 7  # 7 candle rows of 12 columns per statement
    monkeypatch.setitem(plan.DEFAULTS, "backfill_runner", cfg)
    ts = hourly(NOW - timedelta(hours=50), 50)
    rows = [
        {
            "env": ENV,
            "inst_id": "BTC-USDT-SWAP",
            "bar": "1H",
            "ts": datetime.fromtimestamp(t / 1000, UTC),
            "o": Decimal(1),
            "h": Decimal(2),
            "l": Decimal(1),
            "c": Decimal(i),
            "vol": Decimal(1),
            "vol_ccy": None,
            "vol_ccy_quote": None,
            "confirmed": True,
        }
        for i, t in enumerate(ts + ts)  # every key twice in one batch: Postgres would refuse it undeduped
    ]
    with clean.begin() as conn:
        assert upsert_rows(conn, Candle, rows) == 50
    assert candle_ts(clean, "BTC-USDT-SWAP", "1H") == ts
    with clean.connect() as conn:
        last = (
            conn.execute(select(Candle.c).where(Candle.env == ENV).order_by(Candle.ts.desc()))
            .scalars()
            .first()
        )
    assert last == Decimal(99)  # the later duplicate wins


# ---- AC4: the signed endpoint -----------------------------------------------------------------


def universe(engine: Engine) -> None:
    add_instrument(engine, "BTC-USDT-SWAP")
    add_instrument(engine, "ETH-USDT-SWAP")
    add_instrument(engine, "TSLA-USDT-SWAP", asset_class="stock")
    add_instrument(engine, "XAU-USDT-SWAP", asset_class="commodity")
    add_instrument(engine, "BTC-USDT", inst_type="SPOT")
    add_instrument(engine, "BTC-USD-261225", inst_type="FUTURES")  # dated futures: off by default
    add_instrument(engine, "ODD-USDT-SWAP", asset_class="other")  # unknown category: no market toggle
    add_instrument(engine, "DOGE-USDT-SWAP", state="suspend")  # not live


def app_client(engine: Engine) -> TestClient:
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    return TestClient(create_app(settings, scheduler=Scheduler(), engine=engine))


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
    raw = json.dumps(body).encode() if body is not None else b""
    return client.post(
        path,
        content=raw,
        headers={"Content-Type": "application/json", **signed_headers(SECRET, "POST", path, raw)},
    )


def test_start_endpoint_needs_a_signature_and_creates_jobs_once(clean: Engine) -> None:
    universe(clean)
    client = app_client(clean)

    assert client.post("/internal/backfill/start", json={"env": ENV}).status_code == 401
    bad = signed_headers("wrong-secret", "POST", "/internal/backfill/start", b"{}")
    assert client.post("/internal/backfill/start", content=b"{}", headers=bad).status_code == 401
    assert jobs(clean) == []

    first = post(client, "/internal/backfill/start", {"env": ENV, "inst_id": None})
    assert first.status_code == 202, first.text
    created = {(j.inst_id, j.dataset) for j in jobs(clean)}
    candles = ["candles:1D", "candles:1H", "candles:4H", "candles:15m", "candles:5m", "candles:1m"]
    expected = {
        (i, d)
        for i in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "TSLA-USDT-SWAP", "XAU-USDT-SWAP", "BTC-USDT")
        for d in candles
    }
    expected |= {
        (i, "funding") for i in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "TSLA-USDT-SWAP", "XAU-USDT-SWAP")
    }
    expected |= {("BTC", "open_interest"), ("ETH", "open_interest")}
    assert created == expected
    assert first.json() == {
        "ok": True,
        "env": ENV,
        "inst_id": None,
        "instruments": 5,
        "jobs": len(expected),
        "created": len(expected),
        "resumed": 0,
        "started": False,
    }
    assert all(j.status == "pending" and j.rows_written == 0 for j in jobs(clean))

    by_key = {(j.inst_id, j.dataset): j for j in jobs(clean)}
    one_h = by_key[("BTC-USDT-SWAP", "candles:1H")]
    assert one_h.created_at is not None
    assert one_h.created_at - one_h.target_from == timedelta(days=730)
    assert by_key[("BTC-USDT-SWAP", "candles:1m")].created_at - by_key[
        ("BTC-USDT-SWAP", "candles:1m")
    ].target_from == timedelta(days=30)
    assert by_key[("BTC-USDT-SWAP", "candles:1D")].target_from == datetime(2009, 1, 1, tzinfo=UTC)

    second = post(client, "/internal/backfill/start", {"env": ENV})
    assert second.status_code == 202
    assert second.json()["created"] == 0
    assert len(jobs(clean)) == len(expected)  # no duplicates


def test_pause_then_start_requeues_and_one_instrument_scope(clean: Engine) -> None:
    universe(clean)
    client = app_client(clean)
    post(client, "/internal/backfill/start", {"env": ENV})

    paused = post(client, "/internal/backfill/pause", {"env": ENV, "inst_id": "XAU-USDT-SWAP"})
    assert paused.status_code == 202 and paused.json()["jobs"] == 7
    assert {j.status for j in jobs(clean) if j.inst_id == "XAU-USDT-SWAP"} == {"paused"}
    assert {j.status for j in jobs(clean) if j.inst_id != "XAU-USDT-SWAP"} == {"pending"}

    again = post(client, "/internal/backfill/start", {"env": ENV, "inst_id": "XAU-USDT-SWAP"})
    assert again.status_code == 202
    assert again.json()["resumed"] == 7 and again.json()["created"] == 0
    assert {j.status for j in jobs(clean)} == {"pending"}

    assert (
        post(client, "/internal/backfill/start", {"env": ENV, "inst_id": "NOPE-USDT-SWAP"}).status_code == 404
    )
    assert post(client, "/internal/backfill/start", {"env": "live"}).status_code == 409
    assert post(client, "/internal/backfill/start", {"env": "prod"}).status_code == 422
    assert post(client, "/internal/backfill/start", {"inst_id": "x; drop"}).status_code == 422


def test_start_triggers_the_scheduled_runner(clean: Engine) -> None:
    universe(clean)
    calls: list[str] = []

    async def fake_run() -> dict[str, Any]:
        calls.append("run")
        return {}

    sched = Scheduler()
    sched.add("backfill", 3600, fake_run)
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    with TestClient(create_app(settings, scheduler=sched, engine=clean)) as client:
        resp = post(client, "/internal/backfill/start", {"env": ENV})
        assert resp.status_code == 202 and resp.json()["started"] is True
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    assert calls == ["run"]


def test_market_mapping() -> None:
    assert market_of("SWAP", "crypto") == "crypto_perp"
    assert market_of("SWAP", "etf") == "stock_perp"
    assert market_of("SWAP", "commodity") == "commodity_perp"
    assert market_of("SPOT", "crypto") == "spot"
    assert market_of("FUTURES", "crypto") == "futures"
    assert market_of("SWAP", "other") == "other_perp"


# ---- AC5: a full run over 3 instruments with the recorded fixtures -----------------------------


async def test_full_fixture_run_over_three_instruments(
    clean: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENABLE_VAR, "1")
    add_instrument(clean, "BTC-USDT-SWAP")
    add_instrument(clean, "ETH-USDT-SWAP")
    add_instrument(clean, "XAU-USDT-SWAP", asset_class="commodity")
    ensured = ensure_jobs(clean, ENV)
    assert ensured.created == 3 * 7 + 2  # 6 bars + funding each, OI for BTC and ETH

    started = time.monotonic()
    result = await BackfillRunner(ENV, clean).run()  # the production client, served from fixtures
    elapsed = time.monotonic() - started

    assert elapsed < 60
    assert {j.status for j in jobs(clean)} == {"done"}
    assert result.done == ensured.created and result.failed == 0
    with clean.connect() as conn:
        assert conn.execute(select(func.count()).select_from(Candle).where(Candle.env == ENV)).scalar()
        assert conn.execute(
            select(func.count()).select_from(FundingRate).where(FundingRate.env == ENV)
        ).scalar()
        assert conn.execute(
            select(func.count()).select_from(OpenInterest).where(OpenInterest.env == ENV)
        ).scalar()


# ---- logging at INFO (QA round 1: `created` is a reserved LogRecord attribute) -----------------


def test_start_and_pause_answer_202_with_info_logging(
    clean: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    universe(clean)
    client = app_client(clean)
    with caplog.at_level(logging.INFO, logger="trading_core"):
        start = post(client, "/internal/backfill/start", {"env": ENV})
        pause = post(client, "/internal/backfill/pause", {"env": ENV})
    assert start.status_code == 202, start.text
    assert pause.status_code == 202, pause.text
    started = next(r for r in caplog.records if r.getMessage() == "backfill start requested")
    assert started.jobs_created == start.json()["created"]
    assert any(r.getMessage() == "backfill pause requested" for r in caplog.records)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_runner_logs_at_info_without_crashing(
    respx_mock: respx.MockRouter, clean: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.get("/api/v5/market/history-candles").mock(return_value=httpx.Response(500, text="down"))
    job_id = add_job(clean, "BTC-USDT-SWAP", "candles:1H", NOW - timedelta(days=730))
    with caplog.at_level(logging.INFO, logger="trading_core"):
        await BackfillRunner(ENV, clean, client_factory=factory()).run()
    assert job_row(clean, job_id).status == "failed"
    assert any(r.getMessage() == "backfill job failed" for r in caplog.records)
