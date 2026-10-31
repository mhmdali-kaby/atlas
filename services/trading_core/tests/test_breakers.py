"""ATL-145: circuit breakers trip, pause new orders, announce themselves, and data_stale clears itself.

Runs against this checkout's test DB (migrated by `cd backend && php artisan test`) and Redis DB
``REDIS_TEST_DB`` (default 15). Every row is stamped in 2033, later than any other test's rows, so
"newest equity / balances tick / bill" are this module's own; the fixture removes them afterwards.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import (
    BalanceSnapshot,
    Bill,
    Candle,
    EquityPoint,
    Instrument,
    PositionSnapshot,
    Proposal,
    TradingState,
    make_engine,
)
from atlas_shared.events import EventEnvelope
from atlas_shared.market_data import md_key, to_ms
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import OperationalError
from trading_core.okx import OkxClient, OkxCredentials, trade_read
from trading_core.okx.errors import OkxNetworkError
from trading_core.okx.trade import OkxTradeClient, OrderRequest
from trading_core.okx.trade_read import OkxTradeError, add_error_hook, remove_error_hook
from trading_core.risk.breakers import (
    AGENT_BUDGET,
    CONSECUTIVE_LOSSES,
    DAILY_LOSS,
    DATA_STALE,
    NAMES,
    OKX_ERRORS,
    VOLATILITY_HALT,
    BreakerMonitor,
    Breakers,
    okx_errors_key,
)
from trading_core.risk.engine import CHECK_NAMES, evaluate

TEST_DB = testing.db_settings()
ENV = "live"
# Thursday 13 January 2033, 12:00 UTC = 15:00 Baghdad.
AT = datetime(2033, 1, 13, 12, 0, tzinfo=UTC)
STREAMS = ("atlas.system", "atlas.warroom", "atlas.risk")


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> datetime:
        self.at = self.at + timedelta(seconds=seconds)
        return self.at


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
    conn.delete(*STREAMS, okx_errors_key(ENV))
    yield conn
    conn.delete(*STREAMS, okx_errors_key(ENV))
    conn.close()


@pytest.fixture
def world(engine: Engine, rds: redis.Redis) -> Iterator[dict[str, Any]]:
    """A settings row (default profile: balanced), no trading_state, and two fresh instruments: HELD
    with an open position at the newest account tick, FLAT with none."""
    tag = uuid.uuid4().hex[:6].upper()
    held, flat = f"BH{tag}-USDT-SWAP", f"BF{tag}-USDT-SWAP"
    with engine.begin() as c:
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_state = c.execute(
            select(TradingState.paused, TradingState.breakers).where(TradingState.env == ENV)
        ).first()
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 11, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = 11, data = EXCLUDED.data"
            ),
            # No bot capital cap: the loss limits here are % of account equity (decision #250).
            {"id": uuid.uuid4(), "e": ENV, "d": json.dumps({"bot_capital_cap_usdt": None})},
        )
        c.execute(delete(TradingState).where(TradingState.env == ENV))
        for inst in (held, flat):
            c.execute(
                Instrument.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    inst_id=inst,
                    inst_type="SWAP",
                    base_ccy=inst[:8],
                    quote_ccy="USDT",
                    settle_ccy="USDT",
                    ct_val=D("0.01"),
                    ct_mult=D(1),
                    lot_sz=D(1),
                    min_sz=D(1),
                    tick_sz=D("0.1"),
                    max_lever=D(50),
                    state="live",
                    asset_class="crypto",
                    created_at=AT,
                    updated_at=AT,
                )
            )
        tick = AT - timedelta(minutes=1)
        c.execute(
            BalanceSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                ccy="USDT",
                account="trading",
                eq=D(100000),
                avail_bal=D(90000),
                eq_usd=D(100000),
                taken_at=tick,
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            PositionSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=held,
                pos_side="net",
                pos=D(5),
                avg_px=D(100),
                mark_px=D(100),
                mgn_mode="isolated",
                taken_at=tick,
                created_at=AT,
                updated_at=AT,
            )
        )
    for inst in (held, flat):
        fresh(rds, inst, AT)
    yield {"held": held, "flat": flat}
    for inst in (held, flat):
        rds.delete(md_key(ENV, inst))
    since = AT - timedelta(days=10)
    with engine.begin() as c:
        c.execute(delete(Proposal).where(Proposal.inst_id.in_([held, flat])))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id.in_([held, flat])))
        c.execute(delete(Candle).where(Candle.env == ENV, Candle.inst_id.in_([held, flat])))
        c.execute(
            delete(PositionSnapshot).where(PositionSnapshot.env == ENV, PositionSnapshot.taken_at >= since)
        )
        c.execute(
            delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at >= since)
        )
        c.execute(delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts >= since))
        c.execute(delete(Bill).where(Bill.env == ENV, Bill.ts >= since))
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
        if old_settings is None:
            c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
        else:
            c.execute(
                text("UPDATE settings SET version = :v, data = CAST(:d AS jsonb) WHERE env = :e"),
                {"v": old_settings[0], "d": json.dumps(old_settings[1]), "e": ENV},
            )


def fresh(rds: redis.Redis, inst: str, at: datetime) -> None:
    rds.hset(
        md_key(ENV, inst),
        mapping={
            "last_msg_ms": to_ms(at),
            "tickers_ms": to_ms(at),
            "ticker": json.dumps({"last": "100"}),
            "mark": json.dumps({"markPx": "100"}),
        },
    )


def events(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


def breaker_events(rds: redis.Redis) -> list[EventEnvelope]:
    return [e for e in events(rds, "atlas.system") if e.type == "system.circuit_breaker"]


def alerts(rds: redis.Redis) -> list[EventEnvelope]:
    return [e for e in events(rds, "atlas.warroom") if e.payload.get("kind") == "system_alert"]


def stored(engine: Engine) -> dict[str, Any]:
    with engine.connect() as c:
        raw = c.scalar(select(TradingState.breakers).where(TradingState.env == ENV))
    return raw if isinstance(raw, dict) else {}


def is_active(engine: Engine, name: str) -> bool:
    return bool(stored(engine).get(name, {}).get("active"))


def proposal(engine: Engine, inst_id: str) -> uuid.UUID:
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
                entry=D(100),
                stop=D(98),
                expected_r=D("2.5"),
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status="passed",
                created_at=AT,
                updated_at=AT,
            )
        )
    return pid


def bill(engine: Engine, pnl: str, ts: datetime) -> None:
    with engine.begin() as c:
        c.execute(
            Bill.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                bill_id=uuid.uuid4().hex[:20],
                inst_id=None,
                ccy="USDT",
                type="2",
                bal_chg=D(pnl),
                pnl=D(pnl),
                ts=ts,
                created_at=AT,
                updated_at=AT,
            )
        )


def equity(engine: Engine, value: int, ts: datetime) -> None:
    with engine.begin() as c:
        c.execute(
            EquityPoint.__table__.insert().values(
                id=uuid.uuid4(), env=ENV, ts=ts, total_eq_usd=D(value), created_at=AT, updated_at=AT
            )
        )


# ---- AC1: three OKX errors within 5 minutes -----------------------------------------------------


def test_three_okx_errors_within_five_minutes_trip_okx_errors_and_check_1_rejects(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    clock = Clock(AT)
    b = Breakers(engine, ENV, redis_client=rds, now=clock)
    err = OkxTradeError("51008", "Insufficient balance", path="trade/order")

    b.okx_error_hook(ENV, err)
    clock.advance(120)
    b.okx_error_hook(ENV, OkxNetworkError("ConnectTimeout", path="trade/order"))
    assert not is_active(engine, OKX_ERRORS)
    clock.advance(170)  # 290 s after the first: still inside the 5-minute window
    b.okx_error_hook(ENV, err)

    entry = stored(engine)[OKX_ERRORS]
    assert entry["active"] is True and entry["detail"]["count"] == 3
    assert "3 OKX errors" in entry["reason"]

    decision = evaluate(proposal(engine, world["held"]), engine=engine, redis_client=rds, now=clock)
    assert decision.result == "rejected" and decision.failed_check == CHECK_NAMES[0]
    assert decision.checks[0]["code"] == "circuit_breaker_active"
    assert decision.checks[0]["values"]["active_breakers"] == [OKX_ERRORS]


def test_okx_errors_spread_over_more_than_5_minutes_or_benign_codes_do_not_trip(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    clock = Clock(AT)
    b = Breakers(engine, ENV, redis_client=rds, now=clock)
    err = OkxTradeError("50001", "Service temporarily unavailable", path="trade/order")
    for _ in range(3):
        b.okx_error_hook(ENV, err)
        clock.advance(151)  # the first has left the window by the third
    b.okx_error_hook(ENV, OkxTradeError("51016", "Duplicated clOrdId", path="trade/order"))
    b.okx_error_hook(ENV, OkxTradeError("51400", "Cancel failed", path="trade/cancel-order"))
    b.okx_error_hook("demo", err)  # another env's client
    assert not is_active(engine, OKX_ERRORS)


@respx.mock(base_url="https://www.okx.com", assert_all_called=False)
async def test_the_trade_client_feeds_refusals_to_the_error_hook(
    respx_mock: respx.MockRouter, engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    fixtures = Path(__file__).parent / "fixtures" / "okx_trade"
    respx_mock.get("/api/v5/public/time").mock(
        return_value=httpx.Response(200, json={"code": "0", "msg": "", "data": [{"ts": "1793207736680"}]})
    )
    respx_mock.post("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=json.loads((fixtures / "order_51008.json").read_text()))
    )

    async def no_sleep(_: float) -> None:
        return None

    client = OkxTradeClient(
        OkxClient(
            ENV,
            OkxCredentials(api_key="k", secret="s", passphrase="p"),  # noqa: S106 - test only
            http=httpx.AsyncClient(base_url="https://www.okx.com"),
            sleep=no_sleep,
            wall_clock_ms=lambda: 1_793_207_736_680,
        )
    )
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    seen: list[str] = []
    add_error_hook(b.okx_error_hook)
    add_error_hook(lambda env, e: seen.append(getattr(e, "code", "")))
    try:
        order = OrderRequest(
            inst_id=world["held"],
            td_mode="isolated",
            side="buy",
            ord_type="limit",
            sz=D(1),
            px=D(100),
            cl_ord_id="atl145test1",
        )
        for _ in range(3):
            with pytest.raises(OkxTradeError):
                await client.place_order(order)
    finally:
        trade_read._ERROR_HOOKS.clear()
    assert seen == ["51008"] * 3
    assert is_active(engine, OKX_ERRORS)


# ---- AC2: data_stale clears itself; daily_loss does not -------------------------------------------


def test_stale_data_trips_and_60s_of_fresh_data_clears_it_but_daily_loss_stays(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    clock = Clock(AT)
    b = Breakers(engine, ENV, redis_client=rds, now=clock)
    # Today (Baghdad) started at 100,000 and equity is now 95,000: a 5% loss, limit 4% (balanced).
    equity(engine, 100000, AT - timedelta(hours=10))
    equity(engine, 95000, AT - timedelta(minutes=1))
    fresh(rds, world["held"], AT - timedelta(seconds=45))  # held instrument, 45 s > 30 s

    tripped = b.evaluate()
    assert set(tripped) == {DATA_STALE, DAILY_LOSS}
    assert stored(engine)[DATA_STALE]["detail"]["instruments"] == {world["held"]: 45.0}
    assert "5.00%" in stored(engine)[DAILY_LOSS]["reason"]

    # The flat instrument (no position, not on the watchlist) going stale does not matter.
    rds.delete(md_key(ENV, world["flat"]))
    for step in (15, 15, 15, 15):
        fresh(rds, world["held"], clock.advance(step))
        assert b.evaluate() == []
        assert is_active(engine, DATA_STALE), "cleared before 60 s of fresh data"
    # Data went stale again: the fresh period starts over.
    clock.advance(40)
    b.evaluate()
    assert stored(engine)[DATA_STALE]["fresh_since"] is None
    for _ in range(4):
        fresh(rds, world["held"], clock.advance(15))
        b.evaluate()
    assert is_active(engine, DATA_STALE)
    fresh(rds, world["held"], clock.advance(15))
    b.evaluate()

    entry = stored(engine)[DATA_STALE]
    assert entry["active"] is False and entry["cleared_by"] == "system"
    assert is_active(engine, DAILY_LOSS), "daily_loss must wait for the owner"
    for _ in range(10):
        fresh(rds, world["held"], clock.advance(15))
        b.evaluate()
    assert is_active(engine, DAILY_LOSS)

    cleared = [e.payload for e in breaker_events(rds) if e.payload["state"] == "cleared"]
    assert [p["name"] for p in cleared] == [DATA_STALE]

    # The owner resumes: everything clears, and the same day's loss does not re-trip it.
    assert b.clear_all(by="owner") == [DAILY_LOSS]
    assert b.evaluate() == []
    assert not is_active(engine, DAILY_LOSS)


def test_a_watchlist_instrument_with_no_data_trips_data_stale(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    rds.delete(md_key(ENV, world["flat"]))
    assert DATA_STALE not in b.check_stale({world["held"]}, b._settings()[0])
    assert b.check_stale({world["held"], world["flat"]}, b._settings()[0]) == [DATA_STALE]
    assert stored(engine)[DATA_STALE]["detail"]["instruments"] == {world["flat"]: None}


# ---- AC3: a 1-minute move above 6 sigma on a held instrument --------------------------------------


def _one_minute_series(engine: Engine, inst: str, last_return: float) -> None:
    """24 h of 1m bars whose log returns alternate +/-0.05% (sigma ~0.05%), then one bar of
    ``last_return`` closing at AT."""
    import math

    closes = [100.0]
    for k in range(1440):
        closes.append(closes[-1] * math.exp(0.0005 if k % 2 == 0 else -0.0005))
    closes.append(closes[-1] * math.exp(last_return))
    first = AT - timedelta(minutes=len(closes))
    with engine.begin() as c:
        c.execute(
            Candle.__table__.insert(),
            [
                dict(
                    env=ENV,
                    inst_id=inst,
                    bar="1m",
                    ts=first + timedelta(minutes=i),
                    o=D(repr(px)),
                    h=D(repr(px)),
                    l=D(repr(px)),
                    c=D(repr(px)),
                    vol=D(1),
                    confirmed=True,
                )
                for i, px in enumerate(closes)
            ],
        )


def test_a_move_above_6_sigma_on_a_held_instrument_trips_volatility_halt(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    _one_minute_series(engine, world["held"], 0.0040)  # ~8 sigma
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    move = b.one_minute_move(world["held"])
    assert move is not None and 7.5 < move["z"] < 8.5

    assert b.evaluate() == [VOLATILITY_HALT]
    detail = stored(engine)[VOLATILITY_HALT]["detail"]
    assert detail["inst_id"] == world["held"] and detail["limit_sigma"] == "6"


def test_the_same_move_on_an_instrument_with_no_position_does_not_trip(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    _one_minute_series(engine, world["flat"], 0.0040)
    _one_minute_series(engine, world["held"], 0.0020)  # ~4 sigma: under the limit
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    assert b.one_minute_move(world["flat"]) is not None
    assert b.evaluate() == []
    assert not is_active(engine, VOLATILITY_HALT)

    closed = EventEnvelope(
        type="candle.closed",
        env=ENV,
        source="service:trading_core.market_ws",
        payload={"inst_id": world["flat"], "bar": "1m"},
    )
    assert b.on_event("atlas.market", closed) == []


# ---- AC4: one system.circuit_breaker envelope and one War Room system_alert per trip -------------


def test_each_trip_publishes_one_envelope_and_one_war_room_alert_naming_the_breaker(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    for name in NAMES:
        assert b.trip(name, f"Reason for {name}.", {"n": D("1.5")})
        assert not b.trip(name, "Again.")  # already active: no second announcement

    envs = breaker_events(rds)
    assert [e.payload["name"] for e in envs] == list(NAMES)
    assert all(e.payload["state"] == "tripped" and e.env == ENV for e in envs)
    assert all(e.source == "service:trading_core.breakers" for e in envs)
    assert envs[0].payload["detail"] == {"n": "1.5"}

    msgs = alerts(rds)
    assert len(msgs) == len(NAMES)
    for name, msg in zip(NAMES, msgs, strict=True):
        assert msg.source == "system:circuit_breaker" and msg.payload["author"] == "circuit_breaker"
        assert msg.payload["author_type"] == "system" and msg.payload["thread"] == {
            "kind": "main",
            "key": "main",
        }
        assert f"`{name}`" in msg.payload["content_md"] and f"Reason for {name}." in msg.payload["content_md"]
        assert msg.payload["card"]["code"] == name and msg.payload["card"]["type"] == "circuit_breaker"

    with pytest.raises(ValueError):
        b.trip("made_up", "no")


def test_an_over_budget_agent_trips_agent_budget_once_per_agent_and_day(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    state = EventEnvelope(
        type="system.agent_state",
        env=ENV,
        source="agent:historian",
        payload={
            "agent": "historian",
            "state": "degraded",
            "reason": "over_budget",
            "last_error": "over_budget",
        },
    )
    other = EventEnvelope(
        type="system.agent_state",
        env=ENV,
        source="agent:historian",
        payload={"agent": "historian", "reason": "ok"},
    )
    assert b.on_event("atlas.system", other) == []
    assert b.on_event("atlas.system", state) == [AGENT_BUDGET]
    assert b.clear(AGENT_BUDGET)
    assert b.on_event("atlas.system", state) == [], "the owner already acknowledged today's overrun"


# ---- AC5: max_consecutive_losses with the default settings ----------------------------------------


def test_hitting_max_consecutive_losses_with_default_settings_trips_consecutive_losses(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    clock = Clock(AT)
    b = Breakers(engine, ENV, redis_client=rds, now=clock)
    bill(engine, "25", AT - timedelta(hours=5))  # a win before the streak
    bill(engine, "-10", AT - timedelta(hours=4))
    bill(engine, "-12", AT - timedelta(hours=3))
    assert b.evaluate() == []  # 2 losses; the balanced default allows 3

    bill(engine, "-8", AT - timedelta(hours=2))
    assert b.evaluate() == [CONSECUTIVE_LOSSES]
    entry = stored(engine)[CONSECUTIVE_LOSSES]
    assert entry["detail"] == {
        "consecutive_losses": 3,
        "limit": 3,
        "newest_loss_at": "2033-01-13T10:00:00Z",
    }

    # Resume: the acknowledged streak does not re-trip; a fourth loss does.
    assert b.clear(CONSECUTIVE_LOSSES)
    assert b.evaluate() == []
    bill(engine, "-3", AT - timedelta(hours=1))
    assert b.evaluate() == [CONSECUTIVE_LOSSES]


# ---- the monitor ---------------------------------------------------------------------------------


async def test_the_monitor_registers_the_error_hook_only_while_running(
    engine: Engine, rds: redis.Redis, world: dict[str, Any]
) -> None:
    b = Breakers(engine, ENV, redis_client=rds, now=lambda: AT)
    monitor = BreakerMonitor(b, interval_seconds=3600)
    await monitor.start()
    try:
        assert b.okx_error_hook in trade_read._ERROR_HOOKS and monitor.running
    finally:
        await monitor.stop()
    assert b.okx_error_hook not in trade_read._ERROR_HOOKS and not monitor.running
    remove_error_hook(b.okx_error_hook)  # idempotent
