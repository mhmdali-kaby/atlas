"""ATL-128: signed ``POST /internal/risk/check`` runs a hand-written proposal through the Risk Engine,
with nothing sent to OKX.

Runs against this checkout's test DB (migrated by `cd backend && php artisan test`) and Redis DB
``REDIS_TEST_DB`` (default 15). The fixture seeds the same world as test_risk_engine.py's (settings,
verified connection, instrument with 1H candles, equity, balance, fresh market-data hash) in env
``live`` at a fixed 2031 clock, and restores what it changed.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import (
    BalanceSnapshot,
    Candle,
    Connection,
    EquityPoint,
    Instrument,
    PositionSnapshot,
    Proposal,
    RiskCheck,
    TradingState,
    make_engine,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.risk.engine import CHECK_NAMES
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

SECRET = "atlas-test-only-internal-secret"  # noqa: S105
PATH = "/internal/risk/check"
TEST_DB = testing.db_settings()
ENV = "live"
# Thursday 13 March 2031, 15:00 UTC = 11:00 New York: later than any other test's rows.
AT = datetime(2031, 3, 13, 15, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete("atlas.risk")
    yield conn
    conn.delete("atlas.risk")
    conn.close()


@pytest.fixture
def world(engine: Engine, rds: redis.Redis) -> Iterator[str]:
    inst_id = f"RC{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    seen = AT - timedelta(minutes=1)
    with engine.begin() as c:
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_conn = c.execute(
            select(Connection.id, Connection.last_verified_at).where(
                Connection.exchange == "okx", Connection.env == ENV
            )
        ).first()
        old_state = c.execute(
            select(TradingState.paused, TradingState.breakers).where(TradingState.env == ENV)
        ).first()
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 7, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 7, data = EXCLUDED.data"
            ),
            {
                "id": uuid.uuid4(),
                "e": ENV,
                "d": json.dumps({"risk_profile": "balanced", "autonomy_level": 2}),
            },
        )
        if old_conn is None:
            c.execute(
                insert(Connection).values(
                    id=uuid.uuid4(),
                    exchange="okx",
                    env=ENV,
                    label="t",
                    api_key="x",
                    api_secret="x",  # noqa: S106 - not a key; nothing decrypts it
                    passphrase="x",  # noqa: S106
                    last_verified_at=AT,
                    created_at=AT,
                    updated_at=AT,
                )
            )
        else:
            c.execute(update(Connection).where(Connection.id == old_conn.id).values(last_verified_at=AT))
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        c.execute(
            insert(Instrument).values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                base_ccy=inst_id[:8],
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=D("0.001"),
                ct_mult=D(1),
                lot_sz=D(1),
                min_sz=D(1),
                tick_sz=D("0.01"),
                max_lever=D(50),
                state="live",
                asset_class="crypto",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            insert(Candle),
            [
                dict(
                    env=ENV,
                    inst_id=inst_id,
                    bar="1H",
                    ts=AT - timedelta(hours=h),
                    o=D(4000),
                    h=D(4010),
                    l=D(3990),
                    c=D(4000),
                    vol=D(1),
                    confirmed=True,
                )
                for h in range(1, 41)
            ],
        )
        c.execute(
            insert(EquityPoint).values(
                id=uuid.uuid4(), env=ENV, ts=seen, total_eq_usd=D(100000), created_at=AT, updated_at=AT
            )
        )
        c.execute(
            insert(BalanceSnapshot).values(
                id=uuid.uuid4(),
                env=ENV,
                ccy="USDT",
                account="trading",
                eq=D(90000),
                avail_bal=D(90000),
                eq_usd=D(90000),
                taken_at=seen,
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            insert(PositionSnapshot).values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                pos_side="net",
                pos=D(0),
                mgn_mode="isolated",
                taken_at=seen,
                created_at=AT,
                updated_at=AT,
            )
        )
    rds.hset(
        f"atlas:md:{ENV}:{inst_id}",
        mapping={
            "last_msg_ms": int((AT - timedelta(seconds=2)).timestamp() * 1000),
            "tickers_ms": int((AT - timedelta(seconds=2)).timestamp() * 1000),
            "ticker": json.dumps({"last": "4000"}),
            "mark": json.dumps({"markPx": "4000"}),
        },
    )
    yield inst_id
    rds.delete(f"atlas:md:{ENV}:{inst_id}")
    with engine.begin() as c:
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
        c.execute(delete(Candle).where(Candle.env == ENV, Candle.inst_id == inst_id))
        c.execute(delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts == seen))
        c.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at == seen))
        c.execute(
            delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.inst_id == inst_id)
        )
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        if old_state is not None:
            c.execute(
                insert(TradingState).values(
                    id=uuid.uuid4(),
                    env=ENV,
                    paused=old_state.paused,
                    breakers=old_state.breakers,
                    created_at=AT,
                    updated_at=AT,
                )
            )
        if old_conn is None:
            c.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
        else:
            c.execute(
                update(Connection)
                .where(Connection.id == old_conn.id)
                .values(last_verified_at=old_conn.last_verified_at)
            )
        if old_settings is None:
            c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
        else:
            c.execute(
                text("UPDATE settings SET version = :v, data = CAST(:d AS jsonb) WHERE env = :e"),
                {"v": old_settings[0], "d": json.dumps(old_settings[1]), "e": ENV},
            )


def client(engine: Engine, rds: redis.Redis) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env=ENV,
        internal_shared_secret=SECRET,
        run_scheduler=False,
        run_market_ws=False,
        run_private_ws=False,
    )
    app = create_app(settings, scheduler=Scheduler(), engine=engine)
    app.state.risk_redis = rds
    app.state.clock = lambda: AT
    return TestClient(app)


def proposal_body(inst_id: str, **kw: Any) -> bytes:
    body: dict[str, Any] = {
        "inst_id": inst_id,
        "side": "buy",
        "order_type": "limit",
        "entry": "4000",
        "stop": "3960",
        "take_profits": [{"price": "4100", "pct": "100"}],
        "size_suggestion_pct_equity": "5",
        "leverage_suggestion": 3,
        "expected_r_multiple": "2.5",
        "probability_estimate": "0.5",
        "rationale_md": "Hand-written check.",
        "invalidation": "1H close below 3960.",
        **kw,
    }
    return json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def post(tc: TestClient, body: bytes, headers: dict[str, str] | None = None) -> Any:
    hdrs = headers if headers is not None else signed_headers(SECRET, "POST", PATH, body)
    return tc.post(PATH, content=body, headers={**hdrs, "Content-Type": "application/json"})


def _rows(engine: Engine, inst_id: str) -> tuple[list[Any], list[Any]]:
    with Session(engine) as s:
        props = list(s.scalars(select(Proposal).where(Proposal.inst_id == inst_id)))
        checks = list(
            s.scalars(
                select(RiskCheck)
                .join(Proposal, Proposal.id == RiskCheck.proposal_id)
                .where(Proposal.inst_id == inst_id)
            )
        )
    return props, checks


# --- AC1: unsigned or replayed → 401 ------------------------------------------------------------


def test_unsigned_call_is_401_and_writes_nothing(engine: Engine, rds: redis.Redis, world: str) -> None:
    resp = post(client(engine, rds), proposal_body(world), headers={})
    assert resp.status_code == 401
    assert _rows(engine, world) == ([], [])


def test_replayed_call_is_401(engine: Engine, rds: redis.Redis, world: str) -> None:
    tc = client(engine, rds)
    body = proposal_body(world)
    headers = signed_headers(SECRET, "POST", PATH, body)
    assert post(tc, body, headers).status_code == 200
    assert post(tc, body, headers).status_code == 401
    props, checks = _rows(engine, world)
    assert len(props) == 1 and len(checks) == 1


def test_signature_over_another_body_is_401(engine: Engine, rds: redis.Redis, world: str) -> None:
    headers = signed_headers(SECRET, "POST", PATH, proposal_body(world))
    resp = post(client(engine, rds), proposal_body(world, stop="3900"), headers)
    assert resp.status_code == 401


# --- AC2: signed valid proposal → decision, one proposals row, one risk_checks row ---------------


def test_signed_proposal_returns_the_decision_and_stores_one_row_each(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    resp = post(client(engine, rds), proposal_body(world))
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["ok"] is True and out["env"] == ENV and out["settings_version"] == 7
    assert out["result"] == "resized" and out["decision"] == "awaiting_owner"
    assert out["final_contracts"] == "250" and out["final_notional_usd"] == "1000"
    assert out["leverage"] == 3 and out["binding_cap"] == out["checks"][3]["values"]["binding_cap"]
    assert out["binding_cap"] and out["binding_cap"] != "strategist_suggestion"
    assert [c["check"] for c in out["checks"]] == list(CHECK_NAMES)
    assert len(out["checks"]) == 9 and all(c["detail"] for c in out["checks"])

    props, checks = _rows(engine, world)
    assert len(props) == 1 and len(checks) == 1
    prop, check = props[0], checks[0]
    assert prop.author == "owner:manual" and prop.kind == "entry" and prop.env == ENV
    assert prop.stop == D(3960) and prop.cycle_id is None
    assert prop.status == "expired"  # draft → risk_checked by evaluate → closed: a what-if only
    assert str(prop.id) == out["proposal_id"] and str(check.id) == out["risk_check_id"]
    assert check.final_size == D(250)
    assert rds.xlen("atlas.risk") == 1


# --- QA round 1: a manual what-if check must not leave a pending proposal behind -------------------


def test_a_manual_check_leaves_no_pending_or_executable_proposal(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    from atlas_shared.defaults import DEFAULTS
    from trading_core.execution.config import EXECUTABLE_PROPOSAL_STATUSES
    from trading_core.risk.api import FINAL_STATUS

    assert post(client(engine, rds), proposal_body(world)).status_code == 200
    props, _ = _rows(engine, world)
    pending = set(DEFAULTS["agents"]["strategist"]["checklist"]["pending_statuses"])
    evaluable = set(DEFAULTS["risk_rules"]["evaluable_statuses"])
    assert props[0].status == FINAL_STATUS
    assert FINAL_STATUS not in pending | evaluable | set(EXECUTABLE_PROPOSAL_STATUSES)


def test_a_manual_check_does_not_block_an_agent_proposal_on_the_same_side(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    proposals = pytest.importorskip("atlas_agents.proposals")
    framework = pytest.importorskip("atlas_agents.tools.framework")
    from atlas_shared.proposals import TradeProposal

    body = proposal_body(world)
    assert post(client(engine, rds), body).status_code == 200
    ctx = framework.ToolContext(
        env=ENV, session_factory=lambda: Session(engine), agent="strategist", clock=lambda: AT
    )
    with Session(engine) as s:
        item = proposals._check_duplicate(
            s, ctx, TradeProposal.model_validate_json(body), proposals.checklist_config(), None
        )
    assert item.passed, item.detail


def test_a_failing_check_returns_rejected_with_the_reason(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    with engine.begin() as c:
        c.execute(
            insert(TradingState).values(
                id=uuid.uuid4(), env=ENV, paused=True, breakers={}, created_at=AT, updated_at=AT
            )
        )
    out = post(client(engine, rds), proposal_body(world)).json()
    assert out["result"] == "rejected" and out["failed_check"] == "env_and_kill_switch"
    assert out["final_contracts"] is None and out["binding_cap"] is None
    assert out["checks"][0]["code"] == "trading_paused"
    assert [c["status"] for c in out["checks"][1:]] == ["not_run"] * 8


@pytest.mark.parametrize(
    "change",
    [
        {"stop": None},  # no stop: rule 3
        {"stop": "4050"},  # buy with the stop above the entry
        {"side": "hold"},
        {"surprise": 1},  # unknown field
    ],
)
def test_an_invalid_proposal_is_422_and_writes_nothing(
    engine: Engine, rds: redis.Redis, world: str, change: dict[str, Any]
) -> None:
    resp = post(client(engine, rds), proposal_body(world, **change))
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid" and resp.json()["errors"]
    assert _rows(engine, world) == ([], [])


def test_no_settings_row_is_409_and_leaves_no_draft(engine: Engine, rds: redis.Redis, world: str) -> None:
    with engine.begin() as c:
        c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
    resp = post(client(engine, rds), proposal_body(world))
    assert resp.status_code == 409 and resp.json()["error"] == "risk_engine_error"
    assert _rows(engine, world) == ([], [])


# --- AC4: nothing goes to OKX ------------------------------------------------------------------


def test_the_check_makes_zero_requests_to_okx(engine: Engine, rds: redis.Redis, world: str) -> None:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        okx = mock.route(host__regex=r"(^|\.)okx\.com$").respond(500)
        anything = mock.route().respond(500)
        resp = post(client(engine, rds), proposal_body(world))
    assert resp.status_code == 200, resp.text
    assert okx.call_count == 0
    assert anything.call_count == 0
    assert len(mock.calls) == 0
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(Proposal).where(Proposal.inst_id == world)) == 1


# --- ATL-160: POST /internal/risk/evaluate/{id} checks an existing proposal (the Orchestrator's call) ---


def _agent_proposal(engine: Engine, inst_id: str, status: str) -> uuid.UUID:
    pid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            insert(Proposal).values(
                id=pid,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst_id,
                side="buy",
                order_type="limit",
                entry=D(4000),
                stop=D(3960),
                take_profits=[{"price": "4100", "pct": "100"}],
                size_suggestion_pct=D(5),
                leverage_suggestion=3,
                expected_r=D("2.5"),
                probability=D("0.5"),
                status=status,
                created_at=AT,
                updated_at=AT,
            )
        )
    return pid


def _evaluate(tc: TestClient, pid: str, headers: dict[str, str] | None = None) -> Any:
    path = f"/internal/risk/evaluate/{pid}"
    hdrs = headers if headers is not None else signed_headers(SECRET, "POST", path, b"")
    return tc.post(path, content=b"", headers=hdrs)


def test_evaluate_checks_a_passed_proposal_once_and_writes_no_new_proposal(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    pid = _agent_proposal(engine, world, "passed")
    resp = _evaluate(client(engine, rds), str(pid))
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["proposal_id"] == str(pid) and out["decision"] == "awaiting_owner"
    props, checks = _rows(engine, world)
    assert [p.id for p in props] == [pid] and props[0].status == "risk_checked"
    assert len(checks) == 1 and str(checks[0].id) == out["risk_check_id"]


def test_evaluate_refuses_unsigned_and_not_passed(engine: Engine, rds: redis.Redis, world: str) -> None:
    tc = client(engine, rds)
    pid = _agent_proposal(engine, world, "under_review")
    assert _evaluate(tc, str(pid), headers={}).status_code == 401
    resp = _evaluate(tc, str(pid))
    assert resp.status_code == 409 and resp.json()["error"] == "risk_engine_error"
    assert _evaluate(tc, "not-a-uuid").status_code == 404
    assert _rows(engine, world)[1] == []


# --- ATL-162: POST /internal/proposals/{id}/execute re-checks, then (only if approved) executes ----


def _awaiting(engine: Engine, inst_id: str, *, stop: D = D(3960), expires: datetime | None = None) -> uuid.UUID:
    pid = _agent_proposal(engine, inst_id, "awaiting_owner")
    with engine.begin() as c:
        c.execute(
            update(Proposal)
            .where(Proposal.id == pid)
            .values(stop=stop, approval_expires_at=expires or AT + timedelta(minutes=10))
        )
    return pid


class _ExecutorSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def __call__(self, risk_check_id: Any, approved_by: str | None = None, **_: Any) -> Any:
        from trading_core.execution.executor import ExecutionResult

        self.calls.append((str(risk_check_id), approved_by))
        return ExecutionResult(
            status="sent", reason="sent", detail="Order sent; stop confirmed on OKX.",
            risk_check_id=str(risk_check_id), state="live", stop_verified=True,
        )


def _approve(
    engine: Engine, rds: redis.Redis, pid: str, spy: _ExecutorSpy, headers: dict[str, str] | None = None
) -> Any:
    tc = client(engine, rds)
    tc.app.state.approval_execute = spy  # type: ignore[attr-defined]
    path = f"/internal/proposals/{pid}/execute"
    body = json.dumps({"approved_by": "owner"}).encode()
    hdrs = headers if headers is not None else signed_headers(SECRET, "POST", path, body)
    return tc.post(path, content=body, headers={**hdrs, "Content-Type": "application/json"})


def test_execute_reruns_the_risk_check_and_executes_the_fresh_check(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    pid = _awaiting(engine, world)
    spy = _ExecutorSpy()
    resp = _approve(engine, rds, str(pid), spy)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    _, checks = _rows(engine, world)
    assert len(checks) == 1  # the re-check ran here
    assert spy.calls == [(str(checks[0].id), "owner")]
    assert out["status"] == "sent" and out["proposal_id"] == str(pid)
    assert out["risk_check"]["risk_check_id"] == str(checks[0].id)
    assert out["risk_check"]["result"] in ("approved", "resized")
    with Session(engine) as s:
        row = s.get(Proposal, pid)
        assert row is not None and row.decided_by == "owner" and row.decided_at is not None


def test_a_rejected_recheck_never_calls_the_executor(engine: Engine, rds: redis.Redis, world: str) -> None:
    # A stop 0.10 below a 4000 entry is far inside 0.8 × ATR(1H) (ATR 20): check 3 rejects it.
    pid = _awaiting(engine, world, stop=D("3999.9"))
    spy = _ExecutorSpy()
    resp = _approve(engine, rds, str(pid), spy)
    assert resp.status_code == 409, resp.text
    assert resp.json()["reason"] == "risk_rejected" and resp.json()["risk_check"]["result"] == "rejected"
    assert spy.calls == []
    props, checks = _rows(engine, world)
    assert len(checks) == 1 and checks[0].result == "rejected"
    assert props[0].status == "rejected" and props[0].status_reason == "risk_rejected"


def test_execute_refuses_expired_repeated_and_unsigned_approvals(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    spy = _ExecutorSpy()
    expired = _awaiting(engine, world, expires=AT - timedelta(seconds=1))
    resp = _approve(engine, rds, str(expired), spy)
    assert resp.status_code == 409 and resp.json()["reason"] == "approval_expired"
    assert _rows(engine, world)[1] == []

    assert _approve(engine, rds, str(uuid.uuid4()), spy).status_code == 404
    pid = _awaiting(engine, world)
    assert _approve(engine, rds, str(pid), spy, headers={}).status_code == 401
    assert spy.calls == []

    assert _approve(engine, rds, str(pid), spy).status_code == 200
    again = _approve(engine, rds, str(pid), spy)
    assert again.status_code == 409 and again.json()["reason"] == "not_awaiting_owner"
    assert len(spy.calls) == 1
