"""ATL-9: the instrument universe syncs into Postgres with a derived asset_class.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with
respx; universes are built from the recorded demo fixture public_instruments_swap.json.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import Instrument, SyncStatus, make_engine
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.okx import OkxClient
from trading_core.okx.models import Instrument as OkxInstrument
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers
from trading_core.sync.instruments import derive_asset_class, instruments_job, sync_instruments

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"
ENV = "demo"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105

TEST_DB = testing.db_settings()


def recorded() -> dict[str, dict[str, Any]]:
    data = json.loads((FIXTURES / "public_instruments_swap.json").read_text(encoding="utf-8"))["data"]
    return {row["instId"]: row for row in data}


def clone(src: dict[str, Any], inst_id: str, **changes: Any) -> dict[str, Any]:
    row = copy.deepcopy(src)
    family = inst_id.rsplit("-", 1)[0]
    row.update({"instId": inst_id, "instFamily": family, "uly": family, **changes})
    return row


def first_universe() -> list[dict[str, Any]]:
    rec = recorded()
    return [
        rec["XAU-USDT-SWAP"],  # instCategory 4
        rec["TSLA-USDT-SWAP"],  # 3
        clone(rec["TSLA-USDT-SWAP"], "SPY-USDT-SWAP"),  # 3, but SPY is an ETF
        clone(rec["TSLA-USDT-SWAP"], "US100-USDT-SWAP"),  # 3, but US100 is an index
        clone(rec["BTC-USDT-SWAP"], "BTC-USDT-SWAP", lotSz="0.0001", ctVal="0.0001"),  # 1
        clone(rec["BTC-USDT-SWAP"], "ODD-USDT-SWAP", instCategory="9", tickSz="0.00000001"),  # unknown
    ]


def okx_body(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"code": "0", "msg": "", "data": rows}


def mock_universe(router: respx.MockRouter, swaps: list[dict[str, Any]]) -> respx.Route:
    def respond(request: httpx.Request) -> httpx.Response:
        inst_type = request.url.params.get("instType")
        return httpx.Response(200, json=okx_body(swaps if inst_type == "SWAP" else []))

    return router.get("/api/v5/public/instruments").mock(side_effect=respond)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(
            f"{TEST_DB['DB_DATABASE']} is not reachable on {TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']}: {exc}"
        )
    return eng


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(Instrument).where(Instrument.env == ENV))
            conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "instruments"))

    wipe()
    yield engine
    wipe()


def rows_by_id(engine: Engine) -> dict[str, Instrument]:
    with engine.connect() as conn:
        result = conn.execute(select(Instrument).where(Instrument.env == ENV))
        return {r.inst_id: r for r in result}  # type: ignore[misc]


async def run_sync(engine: Engine) -> Any:
    async with OkxClient(ENV, http=httpx.AsyncClient(base_url=BASE)) as okx:
        return await sync_instruments(ENV, okx=okx, engine=engine)


# ---- asset class derivation (no DB) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("inst_id", "expected"),
    [
        ("XAU-USDT-SWAP", "commodity"),
        ("TSLA-USDT-SWAP", "stock"),
        ("SPY-USDT-SWAP", "etf"),
        ("US100-USDT-SWAP", "index"),
        ("BTC-USDT-SWAP", "crypto"),
        ("ODD-USDT-SWAP", "other"),
    ],
)
def test_asset_class_is_derived_from_category(inst_id: str, expected: str) -> None:
    rows = {r["instId"]: r for r in first_universe()}
    assert derive_asset_class(OkxInstrument.model_validate(rows[inst_id])) == expected


def test_missing_category_and_spot_base_ccy() -> None:
    rec = recorded()["BTC-USDT-SWAP"]
    no_cat = clone(rec, "NOCAT-USDT-SWAP", instCategory="")
    assert derive_asset_class(OkxInstrument.model_validate(no_cat)) == "other"
    spot_etf = clone(rec, "QQQ-USDT", instType="SPOT", instCategory="3", baseCcy="QQQ")
    assert derive_asset_class(OkxInstrument.model_validate(spot_etf)) == "etf"


# ---- sync into Postgres -----------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_sync_stores_rows_with_asset_class_and_exact_sizes(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    route = mock_universe(respx_mock, first_universe())
    result = await run_sync(clean)

    assert {c.request.url.params["instType"] for c in route.calls} == {"SPOT", "SWAP", "FUTURES"}
    assert "OPTION" not in {c.request.url.params["instType"] for c in route.calls}
    assert result.upserted == 6 and result.delisted == 0

    rows = rows_by_id(clean)
    assert {k: v.asset_class for k, v in rows.items()} == {
        "XAU-USDT-SWAP": "commodity",
        "TSLA-USDT-SWAP": "stock",
        "SPY-USDT-SWAP": "etf",
        "US100-USDT-SWAP": "index",
        "BTC-USDT-SWAP": "crypto",
        "ODD-USDT-SWAP": "other",
    }
    btc = rows["BTC-USDT-SWAP"]
    assert btc.lot_sz == Decimal("0.0001") and btc.ct_val == Decimal("0.0001")
    assert btc.min_sz == Decimal("0.01") and btc.tick_sz == Decimal("0.01")
    assert rows["ODD-USDT-SWAP"].tick_sz == Decimal("0.00000001")
    assert rows["XAU-USDT-SWAP"].ct_val == Decimal("0.001")
    assert btc.inst_category == "1" and btc.state == "live" and btc.max_lever == Decimal("100")
    assert btc.list_time is not None and btc.list_time.year == 2020
    assert btc.created_at is not None and btc.updated_at is not None and btc.last_seen_at is not None

    with clean.connect() as conn:
        status = conn.execute(
            select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "instruments")
        ).one()
    assert status.last_success_at is not None and status.last_error is None


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_second_sync_keeps_count_and_delists_missing(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    first = first_universe()
    mock_universe(respx_mock, first)
    await run_sync(clean)
    before = rows_by_id(clean)

    respx_mock.routes.clear()
    second = [r for r in first if r["instId"] != "TSLA-USDT-SWAP"]
    mock_universe(respx_mock, second)
    result = await run_sync(clean)

    after = rows_by_id(clean)
    assert len(after) == len(before) == 6
    assert result.delisted == 1
    assert after["TSLA-USDT-SWAP"].state == "delisted_local"
    assert after["TSLA-USDT-SWAP"].id == before["TSLA-USDT-SWAP"].id
    assert all(after[k].state == "live" for k in after if k != "TSLA-USDT-SWAP")
    assert all(after[k].id == before[k].id for k in after)  # upserted in place, never re-created

    # It comes back: the next sync restores OKX's own state.
    respx_mock.routes.clear()
    mock_universe(respx_mock, first)
    await run_sync(clean)
    assert rows_by_id(clean)["TSLA-USDT-SWAP"].state == "live"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_failed_fetch_records_error_and_delists_nothing(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    mock_universe(respx_mock, first_universe())
    await run_sync(clean)

    respx_mock.routes.clear()
    respx_mock.get("/api/v5/public/instruments").mock(
        return_value=httpx.Response(200, json={"code": "51000", "msg": "Parameter error", "data": []})
    )
    with pytest.raises(Exception, match="51000"):
        await run_sync(clean)

    rows = rows_by_id(clean)
    assert len(rows) == 6 and all(r.state == "live" for r in rows.values())
    with clean.connect() as conn:
        status = conn.execute(
            select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "instruments")
        ).one()
        count = conn.execute(
            select(func.count()).select_from(Instrument).where(Instrument.env == ENV)
        ).scalar()
    assert status.last_error is not None and "51000" in status.last_error
    assert status.last_success_at is not None  # the earlier good run is kept
    assert count == 6


# ---- scheduler and signed endpoint ------------------------------------------------------------


async def test_scheduler_runs_at_start_then_every_interval() -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    gate = asyncio.Event()

    async def job() -> int:
        calls.append(1)
        if len(calls) >= 3:
            gate.set()
        return len(calls)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    sched = Scheduler(sleep=fake_sleep)
    sched.add("instruments", 3600, job)
    sched.start()
    await asyncio.wait_for(gate.wait(), timeout=2)
    await sched.stop()
    assert len(calls) >= 3 and set(sleeps) == {3600}
    assert not sched.running


async def test_scheduler_survives_a_failing_job_and_run_now_raises() -> None:
    attempts: list[int] = []
    gate = asyncio.Event()

    async def job() -> None:
        attempts.append(1)
        if len(attempts) >= 2:
            gate.set()
        raise RuntimeError("boom")

    async def fake_sleep(_: float) -> None:
        await asyncio.sleep(0)

    sched = Scheduler(sleep=fake_sleep)
    sched.add("instruments", 60, job)
    sched.start()
    await asyncio.wait_for(gate.wait(), timeout=2)
    await sched.stop()
    with pytest.raises(RuntimeError):
        await sched.run_now("instruments")
    with pytest.raises(KeyError):
        await sched.run_now("missing")


def signed(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    return signed_headers(SECRET, method, path, body)


@respx.mock(base_url=BASE, assert_all_called=False)
def test_signed_endpoint_runs_the_sync(respx_mock: respx.MockRouter, clean: Engine) -> None:
    respx_mock.route(host="testserver").pass_through()
    mock_universe(respx_mock, first_universe())
    sched = Scheduler()
    sched.add("instruments", 3600, instruments_job(ENV, clean))
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    client = TestClient(create_app(settings, scheduler=sched))

    assert client.post("/internal/sync/instruments").status_code == 401  # unsigned is refused
    assert rows_by_id(clean) == {}

    response = client.post("/internal/sync/instruments", headers=signed("POST", "/internal/sync/instruments"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["env"] == ENV and body["upserted"] == 6
    assert body["by_asset_class"]["commodity"] == 1
    assert rows_by_id(clean)["XAU-USDT-SWAP"].asset_class == "commodity"


def test_signed_endpoint_reports_failure_as_502() -> None:
    async def failing() -> None:
        raise RuntimeError("okx down")

    sched = Scheduler()
    sched.add("instruments", 3600, failing)
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET, run_scheduler=False
    )
    client = TestClient(create_app(settings, scheduler=sched))
    response = client.post("/internal/sync/instruments", headers=signed("POST", "/internal/sync/instruments"))
    assert response.status_code == 502
    assert response.json()["error"] == "instruments_sync_failed"
