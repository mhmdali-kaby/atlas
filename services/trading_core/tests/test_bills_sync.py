"""ATL-11: bills, deposits and withdrawals sync from OKX into Postgres.

DB tests run against this checkout's own test DB, atlas_test_<hash> from
atlas_shared.testing.db_settings() (decision #239; migrated by `cd backend && php artisan test`).
OKX is mocked with respx (unmatched routes raise), using the recorded account_bills /
account_bills_archive fixtures, the hand-written deposit/withdrawal fixtures, and a synthetic full
first page so the `after` cursor is exercised.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import Bill, Connection, DepositWithdrawal, SyncStatus, make_engine
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.okx import OkxClient, client_for_connection
from trading_core.scheduler import Scheduler
from trading_core.sync import bill_types
from trading_core.sync.bills import PAGE_LIMIT, STREAM, bills_job

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"
ENV = "demo"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
APP_KEY = "base64:" + base64.b64encode(b"atlas-test-only-app-key-32-bytes").decode()
TEST_DB = testing.db_settings()  # this checkout's atlas_test_<hash> (decision #239)


def laravel_encrypt(plaintext: str) -> str:
    """Crypt::encryptString's payload: base64(json{iv, value, mac, tag}), AES-256-CBC."""
    key = parse_app_key(APP_KEY)
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


def clock(*stamps: datetime) -> Callable[[], datetime]:
    it = iter(stamps)
    return lambda: next(it)


def signed(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), (ts + method + path).encode() + body, hashlib.sha256).hexdigest()
    return {"X-Atlas-Timestamp": ts, "X-Atlas-Signature": sig}


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        where = f"{TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']}"
        pytest.fail(f"test DB {TEST_DB['DB_DATABASE']} is not reachable on {where}: {exc}")
    return eng


def _connection(engine: Engine, *, verified: bool) -> None:
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
                created_at=now,
                updated_at=now,
            )
        )


T0 = datetime(2026, 10, 28, 17, 40, 0, tzinfo=UTC)
API = "/api/v5/"  # paths are written without the prefix, like the client (and NoWithdrawalCodeTest)
ALLOWED = {
    API + "public/time",
    API + "account/bills",
    API + "account/bills-archive",
    API + "asset/deposit-history",
    API + "asset/withdrawal-history",
}

FUNDING_BILL = {
    **{k: "" for k in fixture("account_bills")["data"][0]},
    "billId": "4049999999999000001",
    "ccy": "USDT",
    "instId": "BTC-USDT-SWAP",
    "instType": "SWAP",
    "mgnMode": "cross",
    "type": "8",
    "subType": "174",
    "bal": "98700.123",
    "balChg": "-0.01234567",
    "pnl": "",
    "fee": "0",
    "ts": "1793180000000",
}
TRADE_BILL = {
    **FUNDING_BILL,
    "billId": "4049999999999000002",
    "type": "2",
    "subType": "2",
    "execType": "T",
    "balChg": "1.48312345",
    "pnl": "1.5",
    "fee": "-0.01687655",
    "sz": "0.01",
    "px": "80470.8",
    "ts": "1793180001000",
}


def _full_page() -> list[dict[str, Any]]:
    """PAGE_LIMIT synthetic transfer bills, so the client must ask for a second page."""
    template = fixture("account_bills")["data"][0]
    return [
        {**template, "billId": str(4049900000000000000 - i), "ts": str(1793170000000 - i)}
        for i in range(PAGE_LIMIT)
    ]


PAGE1 = _full_page()
PAGE2 = [FUNDING_BILL, TRADE_BILL, *fixture("account_bills")["data"]]


def _envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"code": "0", "msg": "", "data": rows}


def mock_okx(router: respx.MockRouter) -> dict[str, respx.Route]:
    """Only the listed read endpoints exist; any other request raises (respx assert_all_mocked)."""

    def bills(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json=_envelope(PAGE1))
        if after == PAGE1[-1]["billId"]:
            return httpx.Response(200, json=_envelope(PAGE2))
        return httpx.Response(200, json=_envelope([]))

    def history(name: str) -> Any:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("after") is None:
                return httpx.Response(200, json=fixture(name))
            return httpx.Response(200, json=_envelope([]))

        return respond

    archive = copy.deepcopy(fixture("account_bills_archive"))
    return {
        "time": router.get(API + "public/time").respond(
            json=_envelope([{"ts": str(int(time.time() * 1000))}])
        ),
        "bills": router.get(API + "account/bills").mock(side_effect=bills),
        "archive": router.get(API + "account/bills-archive").respond(json=archive),
        "deposits": router.get(API + "asset/deposit-history").mock(
            side_effect=history("asset_deposit_history")
        ),
        "withdrawals": router.get(API + "asset/withdrawal-history").mock(
            side_effect=history("asset_withdrawal_history")
        ),
    }


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Bill).where(Bill.env == ENV))
        conn.execute(delete(DepositWithdrawal).where(DepositWithdrawal.env == ENV))
        conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM))
        conn.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))


@pytest.fixture
def verified(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    _connection(engine, verified=True)
    yield engine
    _wipe(engine)


def bill_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(Bill).where(Bill.env == ENV)) or 0


def status(engine: Engine) -> Any:
    with engine.connect() as conn:
        return conn.execute(
            select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM)
        ).one_or_none()


def all_bill_ids() -> set[str]:
    archive = fixture("account_bills_archive")["data"]
    return {b["billId"] for b in [*PAGE1, *PAGE2, *archive]}


def test_bill_type_constants_match_the_okx_docs() -> None:
    assert bill_types.FUNDING_FEE == "8" and bill_types.TRADE == "2" and bill_types.TRANSFER == "1"
    assert bill_types.FUNDING_TYPES == frozenset({"8"})
    assert (bill_types.FEE_FIELD, bill_types.PNL_FIELD) == ("fee", "pnl")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_two_pages_via_after_are_stored_once_and_a_second_run_adds_nothing(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    job = bills_job(ENV, verified, APP_KEY, now=clock(T0, T0 + timedelta(minutes=5)))

    first = await job()
    afters = [c.request.url.params.get("after") for c in routes["bills"].calls]
    assert afters == [None, PAGE1[-1]["billId"]]  # page 2 reached through the cursor, then stop
    assert routes["archive"].called  # first run also pages the 3-month archive
    assert first["archive"] is True and first["bills_new"] == len(all_bill_ids())
    assert bill_count(verified) == len(all_bill_ids())

    archive_calls = routes["archive"].call_count
    second = await job()
    assert second["archive"] is False and routes["archive"].call_count == archive_calls
    assert second["bills_new"] == 0
    assert bill_count(verified) == len(all_bill_ids())
    with verified.connect() as conn:
        ids = conn.scalars(select(Bill.bill_id).where(Bill.env == ENV)).all()
    assert sorted(ids) == sorted(all_bill_ids())


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_funding_and_trade_bills_keep_exact_numeric_values(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    mock_okx(respx_mock)
    await bills_job(ENV, verified, APP_KEY, now=clock(T0))()

    with verified.connect() as conn:
        rows = {
            r.bill_id: r
            for r in conn.execute(
                select(Bill).where(
                    Bill.env == ENV,
                    Bill.bill_id.in_([FUNDING_BILL["billId"], TRADE_BILL["billId"]]),
                )
            )
        }
    funding = rows[FUNDING_BILL["billId"]]
    trade = rows[TRADE_BILL["billId"]]
    assert funding.type == bill_types.FUNDING_FEE and funding.sub_type == "174"
    assert funding.bal_chg == Decimal("-0.01234567") and isinstance(funding.bal_chg, Decimal)
    assert funding.fee == Decimal("0") and funding.pnl is None
    assert funding.inst_id == "BTC-USDT-SWAP" and funding.ccy == "USDT"
    assert funding.ts == datetime.fromtimestamp(1793180000, UTC)
    assert trade.type == bill_types.TRADE
    assert trade.bal_chg == Decimal("1.48312345")
    assert trade.fee == Decimal("-0.01687655")
    assert trade.pnl == Decimal("1.5")
    # A transfer bill with OKX's blank fee/pnl stores NULL, not 0.
    with verified.connect() as conn:
        transfer = conn.execute(select(Bill).where(Bill.env == ENV, Bill.bill_id == PAGE2[2]["billId"])).one()
    assert transfer.fee is None and transfer.pnl is None and transfer.bal_chg == Decimal("5000")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_deposits_and_withdrawals_are_stored_by_kind_using_only_read_endpoints(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    mock_okx(respx_mock)  # anything outside these routes raises AllMockedAssertionError
    result = await bills_job(ENV, verified, APP_KEY, now=clock(T0))()
    assert result["deposits"] == 1 and result["withdrawals"] == 1

    used = {c.request.url.path for c in respx_mock.calls}
    assert used <= ALLOWED
    assert all(c.request.method == "GET" for c in respx_mock.calls)

    with verified.connect() as conn:
        found = conn.execute(select(DepositWithdrawal).where(DepositWithdrawal.env == ENV))
        rows = {r.kind: r for r in found}
    assert set(rows) == {"deposit", "withdrawal"}
    dep, wd = rows["deposit"], rows["withdrawal"]
    assert (dep.external_id, dep.ccy, dep.amount, dep.state) == ("88888888", "USDT", Decimal("1000"), "2")
    assert dep.ts == datetime.fromtimestamp(1793000000, UTC) and dep.tx_id is not None
    assert (wd.external_id, wd.ccy, wd.amount, wd.state) == ("99999999", "USDT", Decimal("250"), "2")

    # A rerun updates in place (e.g. a pending deposit turning successful) rather than duplicating.
    await bills_job(ENV, verified, APP_KEY, now=clock(T0 + timedelta(minutes=5)))()
    with verified.connect() as conn:
        n = conn.scalar(
            select(func.count()).select_from(DepositWithdrawal).where(DepositWithdrawal.env == ENV)
        )
    assert n == 2


async def _no_sleep(_: float) -> None:
    return None


def _fast_client(row: Connection, app_key: str) -> OkxClient:
    return client_for_connection(row, app_key, sleep=_no_sleep)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_sync_status_records_success_then_a_5xx_error(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    routes = mock_okx(respx_mock)
    later = T0 + timedelta(minutes=5)
    job = bills_job(ENV, verified, APP_KEY, client_factory=_fast_client, now=clock(T0, later))

    await job()
    st = status(verified)
    assert st.last_success_at == T0 and st.reconciled_at == T0 and st.last_error is None
    before = bill_count(verified)

    routes["bills"].mock(side_effect=None).respond(status_code=503, text="Service Unavailable")
    with pytest.raises(Exception, match="503"):
        await job()

    st = status(verified)
    assert st.last_error is not None and "503" in st.last_error
    assert st.last_success_at == T0  # previous good run kept
    assert bill_count(verified) == before


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_without_a_verified_connection_nothing_is_called_or_written(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    with verified.begin() as conn:
        conn.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
    _connection(verified, verified=False)
    routes = mock_okx(respx_mock)
    result = await bills_job(ENV, verified, APP_KEY, now=clock(T0))()
    assert result == {"env": ENV, "skipped": True, "reason": "no_verified_connection"}
    assert not any(r.called for r in routes.values())
    assert bill_count(verified) == 0 and status(verified) is None


PATH = "/internal/sync/bills"


def app_client(engine: Engine) -> TestClient:
    sched = Scheduler()
    sched.add(STREAM, 300, bills_job(ENV, engine, APP_KEY))
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        app_key=APP_KEY,
    )
    return TestClient(create_app(settings, scheduler=sched, engine=engine))


@respx.mock(base_url=BASE, assert_all_called=False)
def test_signed_endpoint_runs_the_sync(respx_mock: respx.MockRouter, verified: Engine) -> None:
    respx_mock.route(host="testserver").pass_through()
    mock_okx(respx_mock)
    client = app_client(verified)

    assert client.post(PATH).status_code == 401
    response = client.post(PATH, headers=signed("POST", PATH))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["env"] == ENV and body["skipped"] is False
    assert body["bills_new"] == len(all_bill_ids()) and body["deposits"] == 1


@respx.mock(base_url=BASE, assert_all_called=False)
def test_signed_endpoint_409_without_a_verified_connection(
    respx_mock: respx.MockRouter, verified: Engine
) -> None:
    respx_mock.route(host="testserver").pass_through()
    with verified.begin() as conn:
        conn.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
    client = app_client(verified)
    response = client.post(PATH, headers=signed("POST", PATH))
    assert response.status_code == 409
    assert response.json() == {"ok": False, "error": "no_verified_connection", "env": ENV}
