"""ATL-127: the Risk Engine runs the nine §10 checks in order and records each one.

The first half drives :func:`run_checks` with a hand-built :class:`RiskContext` (pure, no I/O). The
second half runs :func:`evaluate` end to end against this checkout's test DB (migrated by
`cd backend && php artisan test`) and Redis DB ``REDIS_TEST_DB`` (default 15): it seeds a settings
row, a verified connection, an instrument with 1H candles, equity and balances, and the ATL-125
market-data hash, then asserts the risk_checks row, the proposal status and the risk.decision
envelope.
"""

from __future__ import annotations

import ast
import json
import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
import redis
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
from atlas_shared.events import EventEnvelope
from atlas_shared.market_data import md_key, to_ms
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.risk.engine import (
    CHECK_NAMES,
    InstrumentInfo,
    ProposalInfo,
    RiskContext,
    evaluate,
    run_checks,
)
from trading_core.risk.inputs import InstrumentSpec
from trading_core.risk.rules import OpenPosition, ScheduledEvent, us_session_open

# Wednesday 15 July 2026, 14:00 UTC = 10:00 New York (EDT, UTC-4).
NOW = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)

XAU = InstrumentInfo(
    "XAU-USDT-SWAP",
    "SWAP",
    "commodity",
    "live",
    InstrumentSpec(
        inst_id="XAU-USDT-SWAP",
        ct_type="linear",
        settle_ccy="USDT",
        quote_ccy="USDT",
        ct_val=D("0.001"),
        ct_mult=D(1),
        lot_sz=D(1),
        min_sz=D(1),
        tick_sz=D("0.01"),
        max_lever=D(50),
    ),
)
TSLA = InstrumentInfo(
    "TSLA-USDT-SWAP",
    "SWAP",
    "stock",
    "live",
    InstrumentSpec(
        inst_id="TSLA-USDT-SWAP",
        ct_type="linear",
        settle_ccy="USDT",
        quote_ccy="USDT",
        ct_val=D("0.01"),
        ct_mult=D(1),
        lot_sz=D(1),
        min_sz=D(1),
        tick_sz=D("0.01"),
        max_lever=D(20),
    ),
)


def proposal(inst: InstrumentInfo = XAU, **kw: Any) -> ProposalInfo:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(),
        env="demo",
        inst_id=inst.inst_id,
        side="buy",
        order_type="limit",
        entry=D(4000),
        entry_zone=None,
        stop=D(3960),
        expected_r=D("2.5"),
        leverage_suggestion=3,
        size_suggestion_pct=D(5),
    )
    if inst is TSLA:
        base.update(entry=D(250), stop=D("247.5"))
    base.update(kw)
    return ProposalInfo(**base)


def ctx(inst: InstrumentInfo = XAU, settings: dict[str, Any] | None = None, **kw: Any) -> RiskContext:
    """A context in which every check passes (check 4 resizes to max_usdt_per_trade)."""
    base: dict[str, Any] = dict(
        now=NOW,
        proposal=proposal(inst),
        settings_version=7,
        settings_data={"risk_profile": "balanced", "autonomy_level": 2, **(settings or {})},
        connection_verified=True,
        ticker_at=NOW - timedelta(seconds=2),
        instrument=inst,
        atr_1h=D(20) if inst is XAU else D(2),
        mark=D(4000) if inst is XAU else D(250),
        equity=D(100000),
        available_usd=D(90000),
        fee_rate=D("0.0005"),
        day_base_usd=D(100000),
        week_base_usd=D(100000),
    )
    base.update(kw)
    return RiskContext(**base)


def statuses(checks: list[dict[str, Any]]) -> list[str]:
    return [c["status"] for c in checks]


def test_happy_path_runs_all_nine_checks_in_order() -> None:
    out = run_checks(ctx())
    assert [c["check"] for c in out.checks] == list(CHECK_NAMES)
    assert [c["n"] for c in out.checks] == list(range(1, 10))
    assert statuses(out.checks) == ["passed"] * 9
    assert out.result == "resized"  # max_usdt_per_trade (1000) binds below the 5 % suggestion
    assert out.sizing is not None and out.sizing.binding_cap == "max_usdt_per_trade"
    assert out.sizing.contracts == D(250)
    assert out.decision == "awaiting_owner"


def test_suggestion_that_binds_is_approved_not_resized() -> None:
    out = run_checks(ctx(proposal=proposal(size_suggestion_pct=D("0.5"))))
    assert out.result == "approved"
    assert out.sizing is not None and out.sizing.binding_cap == "strategist_suggestion"


# --- AC1: check 1 ------------------------------------------------------------------------------


def _rejected_at(out: Any, n: int, code: str) -> None:
    assert out.result == "rejected" and out.decision is None
    assert out.failed_check == CHECK_NAMES[n - 1]
    assert out.checks[n - 1]["status"] == "failed" and out.checks[n - 1]["code"] == code
    assert statuses(out.checks[: n - 1]) == ["passed"] * (n - 1)
    assert statuses(out.checks[n:]) == ["not_run"] * (9 - n)
    assert len(out.checks) == 9


def test_paused_trading_is_rejected_at_check_1_and_the_rest_not_run() -> None:
    _rejected_at(run_checks(ctx(paused=True, pause_reason="kill switch")), 1, "trading_paused")


def test_market_data_older_than_30s_is_rejected_at_check_1() -> None:
    _rejected_at(run_checks(ctx(ticker_at=NOW - timedelta(seconds=31))), 1, "market_data_stale")
    _rejected_at(run_checks(ctx(ticker_at=None)), 1, "market_data_stale")
    assert run_checks(ctx(ticker_at=NOW - timedelta(seconds=30))).result != "rejected"


def test_breaker_and_unverified_connection_reject_at_check_1() -> None:
    _rejected_at(run_checks(ctx(active_breakers=("daily_loss",))), 1, "circuit_breaker_active")
    _rejected_at(run_checks(ctx(connection_verified=False)), 1, "connection_not_verified")


# --- AC2: check 2 ------------------------------------------------------------------------------


def test_blocklisted_instrument_is_rejected_at_check_2() -> None:
    _rejected_at(run_checks(ctx(settings={"blocklist": ["xau-usdt-swap"]})), 2, "blocklisted")


def test_disabled_market_and_unknown_instrument_reject_at_check_2() -> None:
    _rejected_at(run_checks(ctx(settings={"enabled_markets": ["crypto_perp"]})), 2, "market_disabled")
    _rejected_at(run_checks(ctx(instrument=None)), 2, "instrument_unknown")


# 03:00 and 10:00 New York in a DST week (EDT, UTC-4) and a standard-time week (EST, UTC-5).
DST_0300, DST_1000 = datetime(2026, 7, 15, 7, 0, tzinfo=UTC), datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
STD_0300, STD_1000 = datetime(2026, 1, 14, 8, 0, tzinfo=UTC), datetime(2026, 1, 14, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize("at", [DST_0300, STD_0300], ids=["dst", "standard"])
def test_stock_perp_at_0300_new_york_is_rejected_at_check_2(at: datetime) -> None:
    out = run_checks(ctx(TSLA, now=at, ticker_at=at))
    _rejected_at(out, 2, "outside_us_hours")
    assert "03:00" in out.checks[1]["detail"]


@pytest.mark.parametrize("at", [DST_1000, STD_1000], ids=["dst", "standard"])
def test_stock_perp_at_1000_new_york_passes_check_2(at: datetime) -> None:
    out = run_checks(ctx(TSLA, now=at, ticker_at=at))
    assert out.checks[1]["status"] == "passed"
    assert out.result != "rejected"


def test_us_hours_follow_dst_not_a_fixed_utc_hour() -> None:
    # 14:00 UTC is 10:00 in July (EDT) but 09:00 in January (EST), before the 09:30 open.
    assert us_session_open(datetime(2026, 7, 15, 14, 0, tzinfo=UTC))
    assert not us_session_open(datetime(2026, 1, 14, 14, 0, tzinfo=UTC))
    assert not us_session_open(datetime(2026, 7, 18, 14, 0, tzinfo=UTC))  # Saturday


def test_stock_perp_outside_hours_passes_when_the_setting_is_off() -> None:
    out = run_checks(
        ctx(TSLA, now=DST_0300, ticker_at=DST_0300, settings={"avoid_stock_perps_outside_us_hours": False})
    )
    assert out.checks[1]["status"] == "passed"


# --- checks 3-5 --------------------------------------------------------------------------------


def test_invalid_stop_is_rejected_at_check_3() -> None:
    _rejected_at(run_checks(ctx(proposal=proposal(stop=D(4010)))), 3, "stop_wrong_side")
    _rejected_at(run_checks(ctx(atr_1h=None)), 3, "atr_unavailable")


def test_no_capacity_is_rejected_at_check_4() -> None:
    _rejected_at(run_checks(ctx(used_exposure_today_usd=D(5000))), 4, "no_capacity")
    _rejected_at(run_checks(ctx(equity=None)), 4, "equity_unknown")


def test_portfolio_limits_reject_at_check_5() -> None:
    held = tuple(OpenPosition(f"C{i}-USDT-SWAP", 1, D(100), "crypto") for i in range(6))
    _rejected_at(run_checks(ctx(positions=held)), 5, "max_open_positions")
    _rejected_at(run_checks(ctx(trades_today=10)), 5, "max_trades_per_day")
    # 35 % of 100k = 35k cap; XAG long 34.5k with corr 0.9 + 1k new = 35.5k.
    xag = (OpenPosition("XAG-USDT-SWAP", 1, D(34500), "commodity"),)
    _rejected_at(
        run_checks(ctx(positions=xag, correlations={"XAG-USDT-SWAP": 0.9})), 5, "correlated_exposure"
    )
    # Same pair at corr 0.5 is not correlated, but the commodity class net exposure still is.
    _rejected_at(
        run_checks(ctx(positions=xag, correlations={"XAG-USDT-SWAP": 0.5})), 5, "asset_class_exposure"
    )
    # A short in the class offsets the net exposure, and corr 0.5 is not counted.
    hedged = (*xag, OpenPosition("CL-USDT-SWAP", -1, D(30000), "commodity"))
    assert (
        run_checks(ctx(positions=hedged, correlations={"XAG-USDT-SWAP": 0.5, "CL-USDT-SWAP": None})).result
        != "rejected"
    )


# --- AC3: checks 6 and 8 -----------------------------------------------------------------------


def test_daily_loss_at_the_limit_is_rejected_at_check_6() -> None:
    # Balanced: max_daily_loss_pct 4 -> 4,000 USD lost on a 100,000 USD day start.
    _rejected_at(run_checks(ctx(day_loss_usd=D(4000))), 6, "daily_loss_limit")
    assert run_checks(ctx(day_loss_usd=D("3999.99"))).checks[5]["status"] == "passed"


def test_weekly_loss_and_consecutive_losses_reject_at_check_6() -> None:
    _rejected_at(run_checks(ctx(week_loss_usd=D(10000))), 6, "weekly_loss_limit")
    _rejected_at(run_checks(ctx(consecutive_losses=3)), 6, "consecutive_losses")


def test_liquidation_too_close_is_rejected_at_check_7() -> None:
    # 50x long at 4000: liq ~ 4000 x (1 - 0.02 + 0.005) = 3940, only 20 below a stop 40 away.
    out = run_checks(
        ctx(
            proposal=proposal(leverage_suggestion=50), settings={"risk_profile": "custom", "max_leverage": 50}
        )
    )
    _rejected_at(out, 7, "liquidation_too_close")


def test_importance_5_event_in_10_minutes_is_rejected_at_check_8() -> None:
    cpi = ScheduledEvent("CPI y/y", "US", 5, NOW + timedelta(minutes=10))
    out = run_checks(ctx(events=(cpi,)))
    _rejected_at(out, 8, "event_blackout")
    assert "CPI" in out.checks[7]["detail"]


def test_event_outside_the_window_or_below_5_passes_check_8() -> None:
    later = ScheduledEvent("FOMC", "US", 5, NOW + timedelta(minutes=16))
    minor = ScheduledEvent("Claims", "US", 4, NOW + timedelta(minutes=5))
    assert run_checks(ctx(events=(later, minor))).checks[7]["status"] == "passed"
    off = run_checks(
        ctx(
            events=(ScheduledEvent("CPI", "US", 5, NOW + timedelta(minutes=5)),),
            settings={"block_new_trades_minutes_before_high_impact_event": 0},
        )
    )
    assert off.checks[7]["status"] == "passed"


# --- AC5: check 9 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "decision"),
    [(1, "advise_only"), (2, "awaiting_owner"), (3, "awaiting_owner"), (4, "awaiting_owner")],
)
def test_autonomy_level_sets_the_decision(level: int, decision: str) -> None:
    out = run_checks(ctx(settings={"autonomy_level": level}))
    assert out.result != "rejected"
    assert out.decision == decision
    assert out.checks[8]["status"] == "passed"


TC_SRC = Path(__file__).resolve().parents[1] / "src" / "trading_core"
ALLOWED = ("risk", "execution", "okx")  # okx/ is the trade client itself
WRITE_MODULE = "trading_core.okx.trade"  # trade_read and trade_models are read-only and allowed
WRITE_NAMES = {"OkxTradeClient", "trade"}
WRITE_PATH = re.compile(
    r"^(/api/v5/)?(trade/(order|batch-orders|amend-order|cancel-order|close-position|order-algo"
    r"|cancel-algos)|account/set-leverage)$"
)


def _trade_imports(path: Path) -> list[str]:
    """Every import of the OKX write client and every OKX write-endpoint path in one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name == WRITE_MODULE]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {a.name for a in node.names}
            if node.level:
                if mod.split(".")[-1:] == ["trade"] or names & WRITE_NAMES:
                    hits.append("." * node.level + mod)
            elif mod == WRITE_MODULE or (mod == "trading_core.okx" and names & WRITE_NAMES):
                hits.append(mod)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and WRITE_PATH.match(node.value):
            hits.append(node.value)
    return hits


def test_only_risk_and_execution_import_the_okx_trade_endpoints() -> None:
    offenders = {}
    for path in TC_SRC.rglob("*.py"):
        rel = path.relative_to(TC_SRC)
        if rel.parts[0] in ALLOWED:
            continue
        if hits := _trade_imports(path):
            offenders[str(rel)] = hits
    assert offenders == {}


def test_the_import_guard_catches_a_trade_import(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from trading_core.okx import OkxTradeClient\nimport trading_core.okx.trade\n"
        "from trading_core.okx.trade import OrderRequest\nfrom .trade import OkxTradeClient\n"
        "PATH = '/api/v5/trade/order'\nCANCEL = 'trade/cancel-algos'\nLEV = 'account/set-leverage'\n",
        encoding="utf-8",
    )
    assert len(_trade_imports(bad)) == 7


def test_the_import_guard_allows_read_only_trade_calls(tmp_path: Path) -> None:
    ok = tmp_path / "ok.py"
    ok.write_text(
        "from trading_core.okx.trade_read import OkxTradeReadClient\n"
        "from trading_core.okx.trade_models import FillDetail\n"
        "PATHS = ['trade/fills-history', '/api/v5/trade/orders-pending', 'account/max-size']\n",
        encoding="utf-8",
    )
    assert _trade_imports(ok) == []
    assert _trade_imports(TC_SRC / "sync" / "history.py") == []


def test_the_import_guard_catches_a_write_import_from_sync(tmp_path: Path) -> None:
    src = (TC_SRC / "sync" / "history.py").read_text(encoding="utf-8")
    bad = tmp_path / "history.py"
    bad.write_text(src + "\nfrom trading_core.okx.trade import OkxTradeClient\n", encoding="utf-8")
    assert _trade_imports(bad) == [WRITE_MODULE]


# --- evaluate() against Postgres + Redis (AC1, AC4) --------------------------------------------

TEST_DB = testing.db_settings()
ENV = "live"
# Wednesday 12 March 2031, 15:00 UTC = 11:00 New York (EDT): later than any other test's rows.
AT = datetime(2031, 3, 12, 15, 0, tzinfo=UTC)


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
def world(engine: Engine, rds: redis.Redis) -> Iterator[dict[str, Any]]:
    inst_id = f"RK{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
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
        data = json.dumps({"risk_profile": "balanced", "autonomy_level": 2})
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 7, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 7, data = EXCLUDED.data"
            ),
            {"id": uuid.uuid4(), "e": ENV, "d": data},
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
            Candle.__table__.insert(),
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
            EquityPoint.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                ts=AT - timedelta(minutes=1),
                total_eq_usd=D(100000),
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            BalanceSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                ccy="USDT",
                account="trading",
                eq=D(90000),
                avail_bal=D(90000),
                eq_usd=D(90000),
                taken_at=AT - timedelta(minutes=1),
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
                pos=D(0),
                mgn_mode="isolated",
                taken_at=AT - timedelta(minutes=1),
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
    yield {"inst_id": inst_id}
    rds.delete(md_key(ENV, inst_id))
    with engine.begin() as c:
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
        c.execute(delete(Candle).where(Candle.env == ENV, Candle.inst_id == inst_id))
        c.execute(
            delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts == AT - timedelta(minutes=1))
        )
        c.execute(
            delete(BalanceSnapshot).where(
                BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at == AT - timedelta(minutes=1)
            )
        )
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


def _proposal_row(engine: Engine, inst_id: str) -> uuid.UUID:
    pid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            Proposal.__table__.insert().values(
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
                expected_r=D("2.5"),
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status="passed",
                created_at=AT,
                updated_at=AT,
            )
        )
    return pid


def _envelopes(rds: redis.Redis) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange("atlas.risk")]


def test_evaluate_writes_one_row_with_nine_checks_and_publishes_one_decision(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    pid = _proposal_row(engine, world["inst_id"])
    decision = evaluate(pid, engine=engine, redis_client=rds, now=lambda: AT)

    with engine.connect() as c:
        rows = c.execute(select(RiskCheck).where(RiskCheck.proposal_id == pid)).all()
        status = c.scalar(select(Proposal.status).where(Proposal.id == pid))
        state = c.scalar(select(func.count()).select_from(TradingState).where(TradingState.env == ENV))
    assert len(rows) == 1
    row = rows[0]
    assert row.settings_version == 7
    assert [ch["check"] for ch in row.checks] == list(CHECK_NAMES)
    assert all(ch["status"] == "passed" for ch in row.checks), row.checks
    assert row.result == "resized" and row.decision == "awaiting_owner"
    assert row.final_size == D(250) and row.final_notional_usd == D(1000) and row.leverage == 3
    assert status == "risk_checked"
    assert state == 1  # trading_state was absent and is created by the engine
    assert row.checks[2]["values"]["atr_1h"] == "20.0"

    events = _envelopes(rds)
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "risk.decision" and ev.env == ENV and ev.source == "trading_core:risk"
    assert ev.payload["proposal_id"] == str(pid) and ev.payload["risk_check_id"] == str(row.id)
    assert ev.payload["result"] == "resized" and ev.payload["decision"] == "awaiting_owner"
    assert ev.payload["settings_version"] == 7
    assert decision.risk_check_id == row.id


def test_evaluate_with_trading_paused_rejects_at_check_1(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
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
    pid = _proposal_row(engine, world["inst_id"])
    decision = evaluate(pid, engine=engine, redis_client=rds, now=lambda: AT)
    assert (
        decision.result == "rejected"
        and decision.decision is None
        and decision.failed_check == CHECK_NAMES[0]
    )
    with engine.connect() as c:
        row = c.execute(select(RiskCheck).where(RiskCheck.proposal_id == pid)).one()
    assert row.checks[0]["code"] == "trading_paused"
    assert [ch["status"] for ch in row.checks[1:]] == ["not_run"] * 8
    assert row.final_size is None and row.decision is None
    assert len(_envelopes(rds)) == 1


def test_evaluate_with_stale_redis_data_rejects_at_check_1(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    rds.hset(md_key(ENV, world["inst_id"]), "tickers_ms", to_ms(AT - timedelta(seconds=31)))
    pid = _proposal_row(engine, world["inst_id"])
    decision = evaluate(pid, engine=engine, redis_client=rds, now=lambda: AT)
    assert decision.result == "rejected" and decision.checks[0]["code"] == "market_data_stale"
    assert [ch["status"] for ch in decision.checks[1:]] == ["not_run"] * 8


def test_evaluate_autonomy_1_stores_advise_only(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    with engine.begin() as c:
        c.execute(
            text("UPDATE settings SET data = data || '{\"autonomy_level\": 1}'::jsonb WHERE env = :e"),
            {"e": ENV},
        )
    pid = _proposal_row(engine, world["inst_id"])
    assert evaluate(pid, engine=engine, redis_client=rds, now=lambda: AT).decision == "advise_only"
    with engine.connect() as c:
        assert c.scalar(select(RiskCheck.decision).where(RiskCheck.proposal_id == pid)) == "advise_only"


def test_context_dataclass_replace_keeps_it_pure() -> None:
    # run_checks reads only the context: the same context gives the same checks.
    c = ctx()
    assert run_checks(c).checks == run_checks(replace(c)).checks
