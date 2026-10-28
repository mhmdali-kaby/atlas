"""ATL-10: balances, positions and the equity curve sync from OKX into Postgres.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with
respx using the recorded fixtures account_balance.json, asset_balances.json, account_positions.json
and market_tickers_swap.json. The connection row holds Laravel-format ciphertext made with a
throwaway test-only APP_KEY, so the real decrypt path is exercised.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import (
    BalanceSnapshot,
    Connection,
    EquityPoint,
    PositionSnapshot,
    SyncStatus,
    make_engine,
)
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.okx.models import AccountBalance, AssetBalance, Position
from trading_core.scheduler import Scheduler
from trading_core.sync.account import STREAM, account_job, funding_usd, upl_usd

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"
ENV = "demo"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
APP_KEY = "base64:" + base64.b64encode(b"atlas-test-only-app-key-32-bytes").decode()
BTC_SPOT_LAST = "80470.8"

TEST_DB = testing.db_settings()


def laravel_encrypt(plaintext: str, app_key: str = APP_KEY) -> str:
    """Crypt::encryptString's payload: base64(json{iv, value, mac, tag}), AES-256-CBC."""
    key = parse_app_key(app_key)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    value = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    iv_b64 = base64.b64encode(iv).decode()
    mac = hmac.new(key, (iv_b64 + value).encode(), hashlib.sha256).hexdigest()
    payload = {"iv": iv_b64, "value": value, "mac": mac, "tag": ""}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return body


def spot_tickers() -> dict[str, Any]:
    """SPOT tickers built from the recorded SWAP ones: BTC-USDT at the BTC swap's last price."""
    body = copy.deepcopy(fixture("market_tickers_swap"))
    rows = [r for r in body["data"] if r["instId"] == "BTC-USDT-SWAP"]
    for r in rows:
        r.update({"instId": "BTC-USDT", "instType": "SPOT", "last": BTC_SPOT_LAST})
    body["data"] = rows
    return body


def mock_okx(router: respx.MockRouter, *, positions: dict[str, Any] | None = None) -> dict[str, respx.Route]:
    router.get("/api/v5/public/time").respond(
        json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
    )
    return {
        "balance": router.get("/api/v5/account/balance").respond(json=fixture("account_balance")),
        "funding": router.get("/api/v5/asset/balances").respond(json=fixture("asset_balances")),
        "positions": router.get("/api/v5/account/positions").respond(
            json=positions if positions is not None else fixture("account_positions")
        ),
        "tickers": router.get("/api/v5/market/tickers").respond(json=spot_tickers()),
    }


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


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.env == ENV))
        conn.execute(delete(EquityPoint).where(EquityPoint.env == ENV))
        conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM))
        conn.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))


def _connection(engine: Engine, *, verified: bool, permissions: list[str] | None = None) -> None:
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(Connection).values(
                id=uuid.uuid4(),
                exchange="okx",
                env=ENV,
                label="OKX Demo",
                api_key=laravel_encrypt("test-api-key-0000"),
                api_secret=laravel_encrypt("test-secret"),
                passphrase=laravel_encrypt("test-passphrase"),
                last_verified_at=now if verified else None,
                permissions=permissions,
                created_at=now,
                updated_at=now,
            )
        )


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    yield engine
    _wipe(engine)


@pytest.fixture
def verified(clean: Engine) -> Engine:
    _connection(clean, verified=True)
    return clean


def clock(*stamps: datetime) -> Callable[[], datetime]:
    it = iter(stamps)
    return lambda: next(it)


T0 = datetime(2026, 10, 28, 17, 36, 12, 345000, tzinfo=UTC)


def counts(engine: Engine) -> tuple[int, int, int]:
    with engine.connect() as conn:
        return (
            conn.scalar(select(func.count()).select_from(BalanceSnapshot).where(BalanceSnapshot.env == ENV))
            or 0,
            conn.scalar(select(func.count()).select_from(PositionSnapshot).where(PositionSnapshot.env == ENV))
            or 0,
            conn.scalar(select(func.count()).select_from(EquityPoint).where(EquityPoint.env == ENV)) or 0,
        )


def status(engine: Engine) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM)
        ).one_or_none()


# ---- pure helpers -----------------------------------------------------------------------------


def test_funding_usd_values_stablecoins_1to1_priced_coins_by_ticker_and_unpriced_as_none() -> None:
    tickers = {"BTC-USDT": Decimal("80000"), "ETH-USDC": Decimal("2000")}
    usdt = AssetBalance.model_validate({"ccy": "USDT", "bal": "1250.5"})
    usdc = AssetBalance.model_validate({"ccy": "USDC", "bal": "10"})
    btc = AssetBalance.model_validate({"ccy": "BTC", "bal": "0.0015"})
    eth = AssetBalance.model_validate({"ccy": "ETH", "bal": "2"})
    odd = AssetBalance.model_validate({"ccy": "ODD", "bal": "5"})
    empty = AssetBalance.model_validate({"ccy": "ODD", "bal": "0"})
    assert funding_usd(usdt, tickers) == Decimal("1250.5")
    assert funding_usd(usdc, tickers) == Decimal("10")
    assert funding_usd(btc, tickers) == Decimal("120.0000")
    assert funding_usd(eth, tickers) == Decimal("4000")  # falls back to the USDC quote
    assert funding_usd(odd, tickers) is None
    assert funding_usd(empty, tickers) == Decimal(0)


def test_upl_prefers_okx_account_upl_else_sums_usd_settled_positions() -> None:
    positions = [Position.model_validate(p) for p in fixture("account_positions")["data"]]
    inverse = Position.model_validate(
        {**fixture("account_positions")["data"][0], "instId": "BTC-USD-SWAP", "ccy": "BTC", "upl": "9"}
    )
    blank = AccountBalance.model_validate(fixture("account_balance")["data"][0])  # upl ""
    assert blank.upl is None
    assert upl_usd(blank, [*positions, inverse]) == Decimal("1.482") + Decimal("0.695")
    assert upl_usd(blank, []) == Decimal(0)
    with_upl = AccountBalance.model_validate({**fixture("account_balance")["data"][0], "upl": "12.5"})
    assert upl_usd(with_upl, positions) == Decimal("12.5")


# ---- one run ------------------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_one_run_writes_both_accounts_positions_and_the_equity_point(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    result = await account_job(ENV, verified, APP_KEY, now=clock(T0))()

    assert result["skipped"] is False
    assert routes["tickers"].calls.last.request.url.params["instType"] == "SPOT"
    balance = fixture("account_balance")["data"][0]
    funding = fixture("asset_balances")["data"]
    positions = fixture("account_positions")["data"]

    with verified.connect() as conn:
        rows = conn.execute(select(BalanceSnapshot).where(BalanceSnapshot.env == ENV)).all()
        pos_rows = conn.execute(select(PositionSnapshot).where(PositionSnapshot.env == ENV)).all()
        points = conn.execute(select(EquityPoint).where(EquityPoint.env == ENV)).all()

    trading = {r.ccy: r for r in rows if r.account == "trading"}
    funds = {r.ccy: r for r in rows if r.account == "funding"}
    assert set(trading) == {d["ccy"] for d in balance["details"]}
    assert set(funds) == {f["ccy"] for f in funding}
    for d in balance["details"]:  # eq_usd is OKX's own eqUsd, untouched
        assert trading[d["ccy"]].eq == Decimal(d["eq"])
        assert trading[d["ccy"]].eq_usd == Decimal(d["eqUsd"])
    assert funds["USDT"].eq == Decimal("1250.5") and funds["USDT"].eq_usd == Decimal("1250.5")
    assert funds["BTC"].frozen_bal == Decimal("0.0003")
    btc_usd = Decimal("0.0015") * Decimal(BTC_SPOT_LAST)
    assert funds["BTC"].eq_usd == btc_usd
    assert {r.taken_at for r in rows} == {T0}

    assert len(pos_rows) == len(positions)
    by_inst = {r.inst_id: r for r in pos_rows}
    for p in positions:
        row = by_inst[p["instId"]]
        assert (row.pos_side, row.pos, row.avg_px, row.mark_px) == (
            p["posSide"],
            Decimal(p["pos"]),
            Decimal(p["avgPx"]),
            Decimal(p["markPx"]),
        )
        assert row.upl == Decimal(p["upl"]) and row.upl_ratio == Decimal(p["uplRatio"])
        assert row.mgn_mode == p["mgnMode"] and row.lever == Decimal(p["lever"])
        assert row.taken_at == T0
    assert by_inst["XAU-USDT-SWAP"].liq_px is None  # OKX "" stays NULL

    assert len(points) == 1
    point = points[0]
    assert point.ts == T0.replace(second=0, microsecond=0)
    assert point.total_eq_usd == Decimal(balance["totalEq"]) + Decimal("1250.5") + btc_usd
    assert point.upl_usd == Decimal("1.482") + Decimal("0.695")

    st = status(verified)
    assert st.last_error is None and st.last_success_at == T0 and st.reconciled_at == T0


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_stablecoin_only_funding_does_not_fetch_tickers(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    routes["funding"].respond(json={"code": "0", "msg": "", "data": [{"ccy": "USDT", "bal": "3"}]})
    await account_job(ENV, verified, APP_KEY, now=clock(T0))()
    assert not routes["tickers"].called
    with verified.connect() as conn:
        total = conn.scalar(select(EquityPoint.total_eq_usd).where(EquityPoint.env == ENV))
    assert total == Decimal(fixture("account_balance")["data"][0]["totalEq"]) + Decimal("3")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_two_runs_in_one_minute_leave_one_equity_row(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    later = T0 + timedelta(seconds=30)  # 17:36:42, same minute
    job = account_job(ENV, verified, APP_KEY, now=clock(T0, later))
    await job()
    changed = copy.deepcopy(fixture("account_balance"))
    changed["data"][0]["totalEq"] = "98710"
    routes["balance"].respond(json=changed)
    await job()

    with verified.connect() as conn:
        points = conn.execute(select(EquityPoint).where(EquityPoint.env == ENV)).all()
        ticks = conn.scalars(
            select(BalanceSnapshot.taken_at).where(BalanceSnapshot.env == ENV).distinct()
        ).all()
    assert len(points) == 1
    assert points[0].total_eq_usd == Decimal("98710") + Decimal("1250.5") + Decimal("0.0015") * Decimal(
        BTC_SPOT_LAST
    )
    assert set(ticks) == {T0, later}  # snapshots are per tick; only the equity point is per minute


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_verified_demo_key_with_withdraw_permission_still_syncs(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    """ATL-30: a demo key OKX reports as read_only,withdraw,trade is verified with a warning and syncs."""
    _connection(clean, verified=True, permissions=["read_only", "trade", "withdraw"])
    mock_okx(respx_mock)

    result = await account_job(ENV, clean, APP_KEY, now=clock(T0))()

    assert result["skipped"] is False
    assert counts(clean)[2] == 1  # one equity_curve row
    assert status(clean).last_error is None


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_cross_and_isolated_positions_on_one_instrument_are_both_written(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    # Net mode allows a cross and an isolated position on the same instrument (ATL-10 QA round 1).
    positions = copy.deepcopy(fixture("account_positions"))
    first = positions["data"][0]
    other_mode = "cross" if first["mgnMode"] == "isolated" else "isolated"
    positions["data"].append({**first, "mgnMode": other_mode, "posId": "atl10-other-mode"})
    mock_okx(respx_mock, positions=positions)

    result = await account_job(ENV, verified, APP_KEY, now=clock(T0))()

    assert result["skipped"] is False
    with verified.connect() as conn:
        pos_rows = conn.execute(select(PositionSnapshot).where(PositionSnapshot.env == ENV)).all()
    assert len(pos_rows) == len(positions["data"])
    assert {r.mgn_mode for r in pos_rows if r.inst_id == first["instId"]} == {first["mgnMode"], other_mode}
    assert status(verified).last_error is None


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_okx_error_on_positions_writes_nothing_and_records_the_code(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    later = T0 + timedelta(minutes=1)
    job = account_job(ENV, verified, APP_KEY, now=clock(T0, later))
    await job()
    before = counts(verified)

    routes["positions"].respond(json={"code": "51000", "msg": "Parameter instType error", "data": []})
    with pytest.raises(Exception, match="51000"):
        await job()

    assert counts(verified) == before
    st = status(verified)
    assert "51000" in st.last_error
    assert st.last_success_at == T0 and st.reconciled_at == T0  # previous good run kept


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_network_failure_writes_nothing(respx_mock: respx.MockRouter, verified: Engine) -> None:
    import httpx

    routes = mock_okx(respx_mock)
    routes["funding"].mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(Exception, match="network"):
        await account_job(ENV, verified, APP_KEY, now=clock(T0))()
    assert counts(verified) == (0, 0, 0)
    assert "OkxNetworkError" in status(verified).last_error


@pytest.mark.parametrize("with_row", [False, True])
@respx.mock(base_url=BASE, assert_all_called=False)
async def test_without_a_verified_connection_nothing_is_written_and_one_info_line_logged(
    respx_mock: respx.MockRouter, clean: Engine, caplog: pytest.LogCaptureFixture, with_row: bool
) -> None:
    if with_row:
        _connection(clean, verified=False)  # e.g. a withdraw-enabled key that was refused
    routes = mock_okx(respx_mock)
    caplog.set_level(logging.INFO, logger="trading_core.sync.account")

    result = await account_job(ENV, clean, APP_KEY, now=clock(T0))()

    assert result == {"env": ENV, "skipped": True, "reason": "no_verified_connection"}
    assert not any(r.called for r in routes.values())
    assert counts(clean) == (0, 0, 0) and status(clean) is None
    mine = [r for r in caplog.records if r.name == "trading_core.sync.account"]
    assert len(mine) == 1 and mine[0].levelno == logging.INFO


async def test_wrong_app_key_records_the_failure(clean: Engine) -> None:
    _connection(clean, verified=True)
    other = "base64:" + base64.b64encode(b"another-test-only-app-key-32byte").decode()
    with pytest.raises(Exception):  # noqa: B017
        await account_job(ENV, clean, other, now=clock(T0))()
    assert counts(clean) == (0, 0, 0)
    assert "Decrypt" in status(clean).last_error


# ---- signed endpoint ----------------------------------------------------------------------------


def signed(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), (ts + method + path).encode() + body, hashlib.sha256).hexdigest()
    return {"X-Atlas-Timestamp": ts, "X-Atlas-Signature": sig}


def app_client(engine: Engine) -> TestClient:
    sched = Scheduler()
    sched.add(STREAM, 60, account_job(ENV, engine, APP_KEY))
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        app_key=APP_KEY,
    )
    return TestClient(create_app(settings, scheduler=sched, engine=engine))


PATH = "/internal/sync/account"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_signed_endpoint_runs_the_sync(respx_mock: respx.MockRouter, verified: Engine) -> None:
    respx_mock.route(host="testserver").pass_through()
    mock_okx(respx_mock)
    client = app_client(verified)

    assert client.post(PATH).status_code == 401
    response = client.post(PATH, headers=signed("POST", PATH))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["env"] == ENV
    assert body["trading_balances"] == 4 and body["funding_balances"] == 2 and body["positions"] == 2
    assert Decimal(body["total_eq_usd"]) == Decimal("98702.43") + Decimal("1250.5") + Decimal(
        "0.0015"
    ) * Decimal(BTC_SPOT_LAST)


@respx.mock(base_url=BASE, assert_all_called=False)
def test_signed_endpoint_errors(respx_mock: respx.MockRouter, clean: Engine) -> None:
    respx_mock.route(host="testserver").pass_through()
    routes = mock_okx(respx_mock)
    client = app_client(clean)

    response = client.post(PATH, headers=signed("POST", PATH))
    assert response.status_code == 409
    assert response.json() == {"ok": False, "error": "no_verified_connection", "env": ENV}

    _connection(clean, verified=True)
    routes["balance"].respond(json={"code": "50113", "msg": "Invalid Sign", "data": []})
    response = client.post(PATH, headers=signed("POST", PATH))
    assert response.status_code == 502
    assert response.json()["error"] == "account_sync_failed" and "50113" in response.json()["detail"]
