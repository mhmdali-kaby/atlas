"""ATL-237 (decision #255 (4)): trading_core follows system_state.active_env.

On ``system.env_changed`` it stops the old env's jobs and sockets, opens the new env's (OKX URLs from
``okx.environments.<env>``), and refuses ``/execute`` with ``reconciling`` until the new env's first
reconciliation has finished. With no system_state row, ATLAS_ENV decides.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.active_env import ENV_CHANGED, read_active_env, stored_env
from atlas_shared.db import (
    BalanceSnapshot,
    Connection,
    EquityPoint,
    PositionSnapshot,
    SyncStatus,
    make_engine,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, text
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.execution.reconcile import JOB as RECONCILE_JOB
from trading_core.execution.reconcile import ReconcileGate
from trading_core.okx import OkxCredentials
from trading_core.runtime import EnvRuntime
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers
from trading_core.sync.account import STREAM as ACCOUNT_JOB
from trading_core.ws import MarketFeed
from trading_core.ws.feed import ws_urls
from trading_core.ws.private import PrivateFeed, private_url

SECRET = "atlas-test-only-internal-secret"  # noqa: S105
APP_KEY = "base64:" + base64.b64encode(b"atlas-test-only-app-key-32-bytes").decode()
FIXTURES = Path(__file__).parent / "fixtures" / "okx"
TEST_DB = testing.db_settings()
LIVE_REST = str(DEFAULTS["okx"]["environments"]["live"]["rest_base_url"])
CREDS = OkxCredentials(api_key="test-key-0000", secret="test-only-secret", passphrase="p")  # noqa: S106


# --- fixtures ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


def set_active_env(engine: Engine, env: str | None) -> None:
    """Write (or, with None, delete) the single system_state row, as Laravel's switch does."""
    with engine.begin() as c:
        c.execute(text("DELETE FROM system_state"))
        if env is not None:
            c.execute(
                text(
                    "INSERT INTO system_state"
                    " (id, active_env, preflight_attestations, created_at, updated_at)"
                    " VALUES (1, :env, '{}'::jsonb, now(), now())"
                ),
                {"env": env},
            )


@pytest.fixture
def system_state(engine: Engine) -> Iterator[Engine]:
    set_active_env(engine, None)
    yield engine
    set_active_env(engine, None)


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client(testing.redis_settings())
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    yield conn
    conn.close()


def _settings(env: str = "demo", *, sockets: bool = False) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8100,
        env=env,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        run_market_ws=sockets,
        run_private_ws=sockets,
        app_key=APP_KEY,
    )


def env_changed(frm: str, to: str) -> EventEnvelope:
    return EventEnvelope(
        type=ENV_CHANGED,
        env=to,
        source="api:environment",
        payload={"from": frm, "to": to, "at": datetime.now(UTC).isoformat()},
    )


# --- a fake WebSocket server: records which URLs were opened and closed -----------------------------


class FakeSockets:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.closed: list[str] = []

    @asynccontextmanager
    async def _socket(self, url: str) -> AsyncIterator[Any]:
        self.opened.append(url)
        try:
            yield _FakeWs()
        finally:
            self.closed.append(url)

    def connect(self, url: str) -> Any:
        return self._socket(url)

    @property
    def open(self) -> list[str]:
        still = list(self.opened)
        for url in self.closed:
            still.remove(url)
        return still


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    def __aiter__(self) -> _FakeWs:
        return self

    async def __anext__(self) -> str:
        await asyncio.Event().wait()  # never receives anything; only a cancel ends it
        raise StopAsyncIteration


async def _until(cond: Any, within: float = 3.0) -> None:
    deadline = time.monotonic() + within
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# --- AC1: after the event, the live REST base URL without the demo header, old sockets closed ------


def laravel_encrypt(plaintext: str) -> str:
    key = parse_app_key(APP_KEY)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    value = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    iv_b64 = base64.b64encode(iv).decode()
    mac = hmac.new(key, (iv_b64 + value).encode(), hashlib.sha256).hexdigest()
    return base64.b64encode(
        json.dumps({"iv": iv_b64, "value": value, "mac": mac, "tag": ""}).encode()
    ).decode()


def _wipe_live(engine: Engine) -> None:
    with engine.begin() as c:
        for table in (BalanceSnapshot, PositionSnapshot, EquityPoint, SyncStatus):
            c.execute(delete(table).where(table.env == "live"))
        c.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == "live"))


def _live_connection(engine: Engine) -> None:
    now = datetime.now(UTC)
    with engine.begin() as c:
        c.execute(
            insert(Connection).values(
                id=uuid.uuid4(),
                exchange="okx",
                env="live",
                label="OKX Live",
                api_key=laravel_encrypt("test-api-key-0000"),
                api_secret=laravel_encrypt("test-secret"),
                passphrase=laravel_encrypt("test-passphrase"),
                last_verified_at=now,
                permissions=["read_only", "trade"],
                created_at=now,
                updated_at=now,
            )
        )


def _fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return body


async def test_env_changed_moves_rest_to_live_and_closes_the_demo_sockets(
    system_state: Engine, rds: redis.Redis
) -> None:
    engine = system_state
    sockets = FakeSockets()
    feed = MarketFeed(
        "demo", engine, rds, connect=sockets.connect, instruments=set, publish=lambda s, e: None
    )
    pfeed = PrivateFeed(
        "demo", engine, rds, credentials=lambda: CREDS, connect=sockets.connect, publish=lambda s, e: None
    )
    demo_urls = {*ws_urls("demo").values(), private_url("demo")}
    application = create_app(
        _settings("demo", sockets=True),
        scheduler=Scheduler(),
        engine=engine,
        market_feed=feed,
        private_feed=pfeed,
    )
    state = application.state
    await state.runtime.start()
    try:
        await _until(lambda: set(sockets.open) == demo_urls)

        set_active_env(engine, "live")
        assert await state.env_switcher.handle_envelope(env_changed("demo", "live")) is True

        # The demo sockets are closed and nothing is left running for demo.
        assert sockets.open == []
        assert sorted(sockets.closed) == sorted(demo_urls)
        assert not feed.running and not pfeed.running
        # Everything now in app.state belongs to live, with live URLs from config.
        assert state.settings.env == "live"
        assert state.runtime.env == "live"
        assert isinstance(state.market_feed, MarketFeed) and state.market_feed.env == "live"
        assert {k: link.url for k, link in state.market_feed.links.items()} == ws_urls("live")
        assert isinstance(state.private_feed, PrivateFeed) and state.private_feed.env == "live"
        assert state.private_feed.socket.url == private_url("live")
        assert private_url("live") != private_url("demo")
        assert RECONCILE_JOB in state.scheduler.jobs
        assert state.reconcile_gate.ready is False and state.reconcile_gate.reason == "reconciling"
    finally:
        await state.runtime.stop()

    # The live jobs talk to OKX with the live connection: live base URL, no simulated-trading header.
    _wipe_live(engine)
    _live_connection(engine)
    try:
        with respx.mock(base_url=LIVE_REST, assert_all_called=False) as router:
            router.get("/api/v5/public/time").respond(
                json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
            )
            router.get("/api/v5/account/balance").respond(json=_fixture("account_balance"))
            router.get("/api/v5/asset/balances").respond(json=_fixture("asset_balances"))
            router.get("/api/v5/account/positions").respond(json=_fixture("account_positions"))
            router.get("/api/v5/market/tickers").respond(json={"code": "0", "msg": "", "data": []})
            result = await state.scheduler.run_now(ACCOUNT_JOB)
            calls = list(router.calls)
        assert result["env"] == "live"
        assert calls, "the live account job made no OKX call"
        for call in calls:
            assert str(call.request.url).startswith(LIVE_REST)
            assert "x-simulated-trading" not in call.request.headers
    finally:
        _wipe_live(engine)


@respx.mock(base_url=LIVE_REST, assert_all_called=False)
def test_internal_time_uses_the_live_client_after_the_switch(
    respx_mock: respx.MockRouter, system_state: Engine
) -> None:
    respx_mock.route(host="testserver").pass_through()
    route = respx_mock.get("/api/v5/public/time").respond(
        json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
    )
    application = create_app(_settings("demo"), scheduler=Scheduler(), engine=system_state)
    path = "/internal/time"
    with TestClient(application) as http:
        before = http.get(path, headers=signed_headers(SECRET, "GET", path))
        assert before.json()["env"] == "demo"
        assert route.calls.last.request.headers["x-simulated-trading"] == "1"

        set_active_env(system_state, "live")
        asyncio.run(application.state.env_switcher.handle_envelope(env_changed("demo", "live")))

        after = http.get(path, headers=signed_headers(SECRET, "GET", path))
        health = http.get("/health")
    assert after.json()["env"] == "live"
    assert "x-simulated-trading" not in route.calls.last.request.headers
    assert health.json()["env"] == "live"


# --- AC2: /execute is refused with "reconciling" until the live reconciliation has finished --------


class _StubFactory:
    """Builds a live runtime whose reconcile job waits for the test to release it."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.reasons: list[str] = []

    def __call__(self, settings: Settings, reason: str) -> EnvRuntime:
        self.reasons.append(reason)
        gate = ReconcileGate(reason=reason)
        scheduler = Scheduler()

        async def reconcile() -> dict[str, Any]:
            await self.release.wait()
            gate.open()
            return {"env": settings.env}

        scheduler.add(RECONCILE_JOB, 300, reconcile)
        return EnvRuntime(env=settings.env, scheduler=scheduler, gate=gate)


async def test_execute_is_refused_with_reconciling_until_the_live_reconciliation_finishes(
    system_state: Engine,
) -> None:
    factory = _StubFactory()
    application = create_app(
        _settings("demo"),
        scheduler=Scheduler(),
        engine=system_state,
        reconcile_gate=ReconcileGate(ready=True),
        runtime_factory=factory,
    )
    state = application.state
    sent: list[Any] = []

    async def approval_spy(*args: Any, **kwargs: Any) -> Any:
        sent.append(args)
        raise AssertionError("the Executor must not be called while reconciling")

    state.approval_execute = approval_spy
    rid = uuid.uuid4()
    execute_path = f"/internal/execute/{rid}"
    proposal_path = f"/internal/proposals/{uuid.uuid4()}/execute"
    body = json.dumps({"approved_by": "owner"}).encode()

    with TestClient(application) as http:
        set_active_env(system_state, "live")
        assert await state.env_switcher.handle_envelope(env_changed("demo", "live")) is True
        assert factory.reasons == ["reconciling"]

        refused = http.post(execute_path, headers=signed_headers(SECRET, "POST", execute_path))
        assert refused.status_code == 409
        assert refused.json()["reason"] == "reconciling"
        assert refused.json()["risk_check_id"] == str(rid)
        owner = http.post(
            proposal_path, content=body, headers=signed_headers(SECRET, "POST", proposal_path, body)
        )
        assert owner.status_code == 409 and owner.json()["reason"] == "reconciling"
        assert sent == []

        # The live reconciliation finishes: the same call is now handed to the Executor.
        factory.release.set()
        await state.scheduler.run_now(RECONCILE_JOB)
        assert state.reconcile_gate.ready is True
        accepted = http.post(execute_path, headers=signed_headers(SECRET, "POST", execute_path))
    assert accepted.json()["reason"] == "risk_check_not_found"  # past the gate, into the Executor


async def test_an_event_for_the_running_env_or_without_a_row_changes_nothing(system_state: Engine) -> None:
    factory = _StubFactory()
    application = create_app(
        _settings("demo"), scheduler=Scheduler(), engine=system_state, runtime_factory=factory
    )
    switcher = application.state.env_switcher
    assert await switcher.handle_envelope(env_changed("demo", "live")) is False  # no row: stay
    set_active_env(system_state, "demo")
    assert await switcher.handle_envelope(env_changed("live", "demo")) is False
    assert factory.reasons == [] and application.state.settings.env == "demo"


# --- AC4: with no system_state row, ATLAS_ENV decides; with a row, the row wins at startup ----------


def test_without_a_row_trading_core_starts_in_atlas_env(
    system_state: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ENV", "live")
    monkeypatch.setenv("TRADING_CORE_SCHEDULER", "1")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", SECRET)
    assert stored_env(system_state) is None
    assert read_active_env(system_state) == "live"
    factory = _StubFactory()
    application = create_app(
        scheduler=Scheduler(), engine=system_state, runtime_factory=factory, follow_env=True
    )
    with TestClient(application) as http:
        assert http.get("/health").json()["env"] == "live"
    assert factory.reasons == []


def test_with_a_row_trading_core_starts_in_the_stored_env(
    system_state: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ENV", "demo")
    monkeypatch.setenv("TRADING_CORE_SCHEDULER", "1")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", SECRET)
    set_active_env(system_state, "live")
    factory = _StubFactory()
    application = create_app(
        scheduler=Scheduler(), engine=system_state, runtime_factory=factory, follow_env=True
    )
    with TestClient(application) as http:
        assert http.get("/health").json()["env"] == "live"
    # At startup nothing has run yet, so the gate keeps the restart reason.
    assert factory.reasons == ["not_reconciled"]


async def test_the_watcher_follows_an_event_published_on_the_bus(
    system_state: Engine, rds: redis.Redis
) -> None:
    factory = _StubFactory()
    application = create_app(
        _settings("demo"), scheduler=Scheduler(), engine=system_state, runtime_factory=factory
    )
    switcher = application.state.env_switcher
    switcher.block_ms = 100
    switcher.start(rds)
    try:
        await asyncio.sleep(0.2)  # let the watcher pin the stream position
        set_active_env(system_state, "live")
        bus.publish("atlas.system", env_changed("demo", "live"), client=rds)
        await _until(lambda: application.state.settings.env == "live")
    finally:
        await switcher.stop()
    assert factory.reasons == ["reconciling"]
