"""ATL-184: the Risk Engine checks Portfolio Manager position actions (amend_stop, amend_targets,
partial_close, close) and the signed /internal/risk/evaluate/{id} accepts them.

The first half drives :func:`run_position_checks` with hand-built contexts (pure): that is where
close_pct 0 and 150 are tested, because the proposals table CHECKs close_pct to (0, 100) and cannot
hold either. The second half runs the signed evaluate route end to end against this checkout's test
DB and Redis DB ``REDIS_TEST_DB`` (default 15): a verified connection, an open long in the newest
positions snapshot, a live exchange stop in algo_orders and a fresh mark in the market-data hash.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
import redis
from atlas_shared import bus, testing
from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Instrument,
    PositionSnapshot,
    Proposal,
    RiskCheck,
    TradingState,
    make_engine,
)
from atlas_shared.market_data import md_key, to_ms
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.risk.position_actions import (
    POSITION_CHECK_NAMES,
    ActionContext,
    ActionInfo,
    CloseSpec,
    HeldPosition,
    LiveStop,
    run_position_checks,
)
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

NOW = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
INST = "XAU-USDT-SWAP"
LONG = HeldPosition(INST, "net", 1, D(10), D(3950), D(4000), NOW - timedelta(seconds=30))
SHORT = HeldPosition(INST, "net", -1, D(10), D(4050), D(4000), NOW - timedelta(seconds=30))
LIVE_SL = LiveStop("a1", "s1abc", "sl", D(3900), "net", "sell")


def action(kind: str, **kw: Any) -> ActionInfo:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "env": "demo",
        "kind": kind,
        "inst_id": INST,
        "side": None,
        "stop": None,
        "targets": (),
        "close_pct": None,
    }
    base.update(kw)
    return ActionInfo(**base)


def actx(a: ActionInfo, **kw: Any) -> ActionContext:
    base: dict[str, Any] = {
        "now": NOW,
        "action": a,
        "settings_version": 3,
        "settings_data": {"risk_profile": "balanced", "autonomy_level": 2},
        "connection_verified": True,
        "positions": (LONG,),
        "positions_taken_at": LONG.taken_at,
        "live_stops": (LIVE_SL,),
        "mark": D(4000),
        "mark_source": "market_data",
        "spec": CloseSpec("linear", D("0.01"), D(1), D(1), D(1)),
    }
    base.update(kw)
    return ActionContext(**base)


def by_name(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["check"]: c for c in checks}


# --- Pure checks -----------------------------------------------------------------------------


def test_amend_stop_that_tightens_a_long_below_mark_is_approved_with_every_check() -> None:
    out = run_position_checks(actx(action("amend_stop", stop=D(3950))))
    assert out.result == "approved" and out.decision == "awaiting_owner" and out.failed_check is None
    assert [c["check"] for c in out.checks] == list(POSITION_CHECK_NAMES)
    assert [c["status"] for c in out.checks] == [
        "passed",
        "passed",
        "passed",
        "passed",
        "not_applicable",
        "not_applicable",
        "passed",
    ]
    tight = by_name(out.checks)["stop_tightens"]["values"]
    assert tight["current_stop"] == "3900" and tight["new_stop"] == "3950"
    assert out.current_stop == D(3900)


@pytest.mark.parametrize(
    ("stop", "check", "code"),
    [
        (D(3850), "stop_tightens", "stop_widens"),
        (D(3900), "stop_tightens", "stop_unchanged"),
        (D(4000), "stop_vs_mark", "stop_beyond_mark"),
        (D(4010), "stop_vs_mark", "stop_beyond_mark"),
    ],
)
def test_amend_stop_that_widens_or_crosses_mark_is_rejected_with_a_named_reason(
    stop: D, check: str, code: str
) -> None:
    out = run_position_checks(actx(action("amend_stop", stop=stop)))
    assert out.result == "rejected" and out.decision is None and out.failed_check == check
    assert by_name(out.checks)[check]["code"] == code
    assert out.checks[-1]["status"] == "not_run"


def test_short_stop_tightens_downwards_and_must_stay_above_mark() -> None:
    stops = (LiveStop("a2", "s2", "oco", D(4100), "net", "buy"),)
    ok = run_position_checks(actx(action("amend_stop", stop=D(4050)), positions=(SHORT,), live_stops=stops))
    assert ok.result == "approved"
    wide = run_position_checks(actx(action("amend_stop", stop=D(4150)), positions=(SHORT,), live_stops=stops))
    assert by_name(wide.checks)["stop_tightens"]["code"] == "stop_widens"
    cross = run_position_checks(
        actx(action("amend_stop", stop=D(3990)), positions=(SHORT,), live_stops=stops)
    )
    assert by_name(cross.checks)["stop_vs_mark"]["code"] == "stop_beyond_mark"


def test_the_tightest_of_several_live_stops_is_the_one_to_beat() -> None:
    stops = (LIVE_SL, LiveStop("a3", "o3", "oco", D(3940), "net", "sell"))
    out = run_position_checks(actx(action("amend_stop", stop=D(3920)), live_stops=stops))
    assert by_name(out.checks)["stop_tightens"]["code"] == "stop_widens"


def test_a_stop_for_the_other_side_does_not_count_as_the_live_stop() -> None:
    # A buy-side stop cannot protect a long; with no matching stop any safe-side stop passes.
    other = (LiveStop("a4", "s4", "sl", D(3990), "net", "buy"),)
    out = run_position_checks(actx(action("amend_stop", stop=D(3920)), live_stops=other))
    assert out.result == "approved"
    assert by_name(out.checks)["stop_tightens"]["values"]["note"] == "no_live_stop"


@pytest.mark.parametrize("kind", ["amend_stop", "amend_targets", "partial_close", "close"])
def test_any_action_with_no_open_position_is_rejected(kind: str) -> None:
    a = action(kind, stop=D(3950), targets=(D(4100),), close_pct=D(50))
    out = run_position_checks(actx(a, positions=()))
    assert out.result == "rejected" and out.failed_check == "position_open"
    assert by_name(out.checks)["position_open"]["code"] == "no_open_position"


def test_a_stale_positions_snapshot_cannot_prove_a_position_is_open() -> None:
    out = run_position_checks(actx(action("close"), positions_taken_at=NOW - timedelta(seconds=301)))
    assert by_name(out.checks)["position_open"]["code"] == "positions_stale"


def test_side_picks_the_position_in_long_short_mode_and_two_without_side_is_ambiguous() -> None:
    long_ = replace(LONG, pos_side="long")
    short = replace(SHORT, pos_side="short")
    both = (long_, short)
    amb = run_position_checks(actx(action("close"), positions=both))
    assert by_name(amb.checks)["position_open"]["code"] == "position_ambiguous"
    out = run_position_checks(actx(action("close", side="sell"), positions=both))
    assert out.result == "approved" and out.position == short


@pytest.mark.parametrize("pct", [D(0), D(150), D(-5)])
def test_partial_close_pct_outside_0_100_is_rejected(pct: D) -> None:
    out = run_position_checks(actx(action("partial_close", close_pct=pct)))
    assert out.result == "rejected" and out.failed_check == "close_size"
    assert by_name(out.checks)["close_size"]["code"] == "close_pct_out_of_range"


def test_partial_close_sizes_down_to_lot_and_refuses_below_min() -> None:
    out = run_position_checks(actx(action("partial_close", close_pct=D(35))))
    assert out.result == "approved" and out.close_contracts == D(3)  # 3.5 → 3
    assert out.close_notional_usd == D(3) * D("0.01") * D(4000)
    tiny = run_position_checks(actx(action("partial_close", close_pct=D(5))))  # 0.5 → 0 < minSz 1
    assert by_name(tiny.checks)["close_size"]["code"] == "close_below_min_size"


def test_close_is_100_percent_and_passes_while_paused_or_a_breaker_is_tripped() -> None:
    paused = run_position_checks(actx(action("close"), paused=True, pause_reason="kill"))
    assert paused.result == "approved" and paused.close_contracts == D(10)
    assert by_name(paused.checks)["close_size"]["values"]["close_pct"] == "100"
    assert by_name(paused.checks)["env_state"]["values"]["note"] == "risk_reducing_allowed_while_halted"
    tripped = run_position_checks(actx(action("close"), active_breakers=("daily_loss",)))
    assert tripped.result == "approved"
    tighten = run_position_checks(actx(action("amend_stop", stop=D(3950)), paused=True))
    assert tighten.result == "approved"


def test_widening_is_refused_while_paused_too() -> None:
    out = run_position_checks(actx(action("amend_stop", stop=D(3850)), paused=True))
    assert by_name(out.checks)["stop_tightens"]["code"] == "stop_widens"


def test_amend_targets_needs_every_target_on_the_profit_side_and_is_refused_while_paused() -> None:
    ok = run_position_checks(actx(action("amend_targets", targets=(D(4100), D(4200)))))
    assert ok.result == "approved"
    bad = run_position_checks(actx(action("amend_targets", targets=(D(4100), D(3990)))))
    assert by_name(bad.checks)["targets_vs_mark"]["code"] == "target_beyond_mark"
    halted = run_position_checks(actx(action("amend_targets", targets=(D(4100),)), paused=True))
    assert by_name(halted.checks)["env_state"]["code"] == "trading_paused"


def test_no_mark_and_no_verified_connection_are_refused() -> None:
    no_mark = run_position_checks(actx(action("amend_stop", stop=D(3950)), mark=None))
    assert by_name(no_mark.checks)["stop_vs_mark"]["code"] == "mark_unknown"
    unverified = run_position_checks(actx(action("close"), connection_verified=False))
    assert by_name(unverified.checks)["env_state"]["code"] == "connection_unverified"


def test_autonomy_1_is_advise_only() -> None:
    out = run_position_checks(
        actx(action("close"), settings_data={"risk_profile": "balanced", "autonomy_level": 1})
    )
    assert out.result == "approved" and out.decision == "advise_only"


# --- Signed POST /internal/risk/evaluate/{id} against Postgres + Redis -----------------------

SECRET = "atlas-test-only-internal-secret"  # noqa: S105
TEST_DB = testing.db_settings()
ENV = "live"
# Friday 14 March 2031, 15:00 UTC: later than the other risk tests' rows.
AT = datetime(2031, 3, 14, 15, 0, tzinfo=UTC)


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
    inst_id = f"PA{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    cl_id = f"s{uuid.uuid4().hex[:20]}"
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
                " VALUES (:id, :e, 9, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 9, data = EXCLUDED.data"
            ),
            {
                "id": uuid.uuid4(),
                "e": ENV,
                "d": json.dumps({"risk_profile": "balanced", "autonomy_level": 2}),
            },
        )
        if old_conn is None:
            c.execute(
                Connection.__table__.insert().values(
                    id=uuid.uuid4(),
                    exchange="okx",
                    env=ENV,
                    label="t",
                    api_key="x",
                    api_secret="x",  # noqa: S106 - not a key; the engine never decrypts it
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
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                base_ccy=inst_id[:8],
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=D("0.01"),
                ct_mult=D(1),
                lot_sz=D(1),
                min_sz=D(1),
                tick_sz=D("0.01"),
                max_lever=D(50),
                state="live",
                asset_class="commodity",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            PositionSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                pos_side="net",
                pos=D(10),
                avg_px=D(3950),
                mark_px=D(4000),
                mgn_mode="isolated",
                taken_at=AT - timedelta(seconds=30),
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            AlgoOrder.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                algo_id="900001",
                algo_cl_ord_id=cl_id,
                inst_id=inst_id,
                kind="sl",
                side="sell",
                pos_side="net",
                trigger_px=D(3900),
                state="live",
                origin="atlas",
                created_at=AT,
                updated_at=AT,
            )
        )
    rds.hset(
        md_key(ENV, inst_id),
        mapping={
            "last_msg_ms": to_ms(AT - timedelta(seconds=2)),
            "tickers_ms": to_ms(AT - timedelta(seconds=2)),
            "ticker": json.dumps({"last": "4000"}),
            "mark": json.dumps({"markPx": "4000"}),
        },
    )
    yield inst_id
    rds.delete(md_key(ENV, inst_id))
    with engine.begin() as c:
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(AlgoOrder).where(AlgoOrder.env == ENV, AlgoOrder.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
        c.execute(
            delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.inst_id == inst_id)
        )
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        if old_state is not None:
            c.execute(
                TradingState.__table__.insert().values(
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


def _pm_proposal(engine: Engine, inst_id: str, kind: str, **kw: Any) -> uuid.UUID:
    pid = uuid.uuid4()
    values: dict[str, Any] = {
        "id": pid,
        "env": ENV,
        "version": 1,
        "kind": kind,
        "author": "portfolio_manager",
        "inst_id": inst_id,
        "rationale_md": "rule fired",
        "invalidation": "breakeven_at_1r",
        "status": "passed",
        "created_at": AT,
        "updated_at": AT,
    }
    values.update(kw)
    with engine.begin() as c:
        c.execute(insert(Proposal).values(**values))
    return pid


def _evaluate(tc: TestClient, pid: uuid.UUID) -> Any:
    path = f"/internal/risk/evaluate/{pid}"
    return tc.post(path, content=b"", headers=signed_headers(SECRET, "POST", path, b""))


def _stored(engine: Engine, pid: uuid.UUID) -> tuple[Any, str]:
    with engine.connect() as c:
        rc = c.execute(select(RiskCheck).where(RiskCheck.proposal_id == pid)).one()
        status = c.scalar(select(Proposal.status).where(Proposal.id == pid))
    assert status is not None
    return rc, status


def test_evaluate_approves_a_tightening_amend_stop_and_stores_each_check(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    pid = _pm_proposal(engine, world, "amend_stop", stop=D(3950))
    resp = _evaluate(client(engine, rds), pid)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["result"] == "approved" and out["decision"] == "awaiting_owner"
    assert out["failed_check"] is None and out["leverage"] is None and out["final_contracts"] is None
    assert [c["check"] for c in out["checks"]] == list(POSITION_CHECK_NAMES)
    rc, status = _stored(engine, pid)
    assert rc.result == "approved" and rc.decision == "awaiting_owner" and rc.settings_version == 9
    assert [c["check"] for c in rc.checks] == list(POSITION_CHECK_NAMES)
    assert by_name(rc.checks)["stop_tightens"]["values"]["current_stop"] == "3900"
    # Same contract as entries: evaluate leaves risk_checked; the caller (cycle / PM) moves it on.
    assert status == "risk_checked"
    published = rds.xrange("atlas.risk")
    assert len(published) == 1


@pytest.mark.parametrize(("stop", "code"), [(D(3850), "stop_widens"), (D(4005), "stop_beyond_mark")])
def test_evaluate_rejects_a_widening_or_beyond_mark_stop(
    engine: Engine, rds: redis.Redis, world: str, stop: D, code: str
) -> None:
    pid = _pm_proposal(engine, world, "amend_stop", stop=stop)
    out = _evaluate(client(engine, rds), pid).json()
    assert out["result"] == "rejected" and out["decision"] is None
    rc, _ = _stored(engine, pid)
    assert rc.result == "rejected" and code in [c["code"] for c in rc.checks]


def test_evaluate_rejects_an_action_with_no_open_position(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    with engine.begin() as c:
        c.execute(
            update(PositionSnapshot)
            .where(PositionSnapshot.env == ENV, PositionSnapshot.inst_id == world)
            .values(pos=D(0))
        )
    pid = _pm_proposal(engine, world, "close")
    out = _evaluate(client(engine, rds), pid).json()
    assert out["result"] == "rejected" and out["failed_check"] == "position_open"
    assert out["checks"][1]["code"] == "no_open_position"


def test_evaluate_approves_a_close_while_trading_is_paused(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    with engine.begin() as c:
        c.execute(
            TradingState.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                paused=True,
                reason="kill",
                breakers={},
                created_at=AT,
                updated_at=AT,
            )
        )
    pid = _pm_proposal(engine, world, "close")
    out = _evaluate(client(engine, rds), pid).json()
    assert out["result"] == "approved" and out["decision"] == "awaiting_owner"
    assert out["final_contracts"] == "10" and out["final_notional_usd"] == "400"


def test_evaluate_partial_close_reports_the_contracts_to_close(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    pid = _pm_proposal(engine, world, "partial_close", close_pct=D(50))
    out = _evaluate(client(engine, rds), pid).json()
    assert out["result"] == "approved" and out["final_contracts"] == "5"


def test_evaluate_still_refuses_a_position_action_that_is_not_evaluable(
    engine: Engine, rds: redis.Redis, world: str
) -> None:
    pid = _pm_proposal(engine, world, "close", status="under_review")
    resp = _evaluate(client(engine, rds), pid)
    assert resp.status_code == 409 and resp.json()["error"] == "risk_engine_error"
