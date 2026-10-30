"""ATL-152, brief §19: integration tests against the real OKX demo (paper) account.

Not part of ``uv run pytest``: every test here is marked ``okx_demo`` and skipped unless
``ATLAS_OKX_DEMO_IT=1``. Run them explicitly from ``services/``::

    ATLAS_OKX_DEMO_IT=1 uv run pytest -m okx_demo trading_core/tests/integration -v

Where the key comes from (decision #234): the owner (or QA) saves the demo key in Settings ->
Connections and presses Test connection. That writes an encrypted ``connections`` row into the app
database (``DB_DATABASE``, default ``atlas``; ``ATLAS_OKX_DEMO_SOURCE_DB`` overrides). This suite only
*reads* that row, copies the ciphertexts into this checkout's test database and decrypts them with
``APP_KEY`` (the environment, else the macOS Keychain entry ``./atlas up`` uses). The key is never
read from .env, fixtures or test files and never printed.

Everything else happens in the per-checkout test database (decision #239) and the Redis test DB, so
the owner's settings, proposals and War Room are untouched. What the Risk Engine needs is loaded from
the real demo API first: the SWAP universe (instruments sync), the account (account sync: balances,
positions, an equity point), recent 1H candles (ATR) and a fresh ticker/mark in the market-data hash
(the WebSocket feed's format; the feed itself is not started). Each order is then a ``passed``
proposal that ``risk.engine.evaluate`` approves, and the real ``execution.executor.execute`` sends it.
Nothing assumes a demo balance (decision #240): the size is OKX's minimum for the instrument and the
Risk Engine's own margin check decides whether it fits.

Every case cleans up after itself, even on failure: it cancels the instrument's pending orders and
algo orders and closes its position. A case refuses to start (skip with the reason) when the
instrument already has a position or open orders on the demo account, so it never touches the
owner's own trades. The kill-switch case flattens the *whole* account, so it also skips when anything
else is open, unless ``ATLAS_OKX_DEMO_IT_FLATTEN_ALL=1``.

Stop trading_core (``./atlas down``) before running: its own reconciliation would otherwise act on the
same account and race case 4.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, cast

import pytest
import redis
from atlas_shared import bus, testing
from atlas_shared import market_data as md
from atlas_shared.db import (
    AlgoOrder,
    Candle,
    Connection,
    Instrument,
    Order,
    Proposal,
    TradingState,
    make_engine,
)
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from trading_core.execution.close_cancel import cancel_order, close_position
from trading_core.execution.executor import ExecutionResult, execute
from trading_core.execution.kill import kill
from trading_core.execution.reconcile import reconcile
from trading_core.marketdata.grid import candle_rows
from trading_core.okx import OkxClient
from trading_core.okx.connection import client_for_connection
from trading_core.okx.errors import OkxNetworkError
from trading_core.okx.models import Position
from trading_core.okx.trade import MgnMode, OkxTradeClient, PosSide
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail
from trading_core.risk.context import _atr_1h, _instrument_info
from trading_core.risk.engine import RiskDecision, evaluate
from trading_core.risk.rules import rules_config
from trading_core.risk.sizing import contract_value_usd, round_to_tick
from trading_core.sync.account import sync_account
from trading_core.sync.instruments import sync_instruments

ENV = "demo"
BTC = "BTC-USDT-SWAP"
XAU = "XAU-USDT-SWAP"
RUN_VAR = "ATLAS_OKX_DEMO_IT"
FLATTEN_ALL_VAR = "ATLAS_OKX_DEMO_IT_FLATTEN_ALL"
SOURCE_DB_VAR = "ATLAS_OKX_DEMO_SOURCE_DB"
ALGO_KINDS = ("conditional,oco", "trigger")
# Stop distance: comfortably above the Risk Engine's min_stop_atr_mult (0.8 x ATR 1H) so a minute of
# noise cannot trigger it while the case runs, and far below max_stop_pct (8%).
STOP_ATR_MULT = Decimal("1.5")
STOP_MIN_PCT = Decimal("0.006")
STOP_MAX_PCT = Decimal("0.05")
TP_R = Decimal(3)
LIMIT_DISCOUNT = Decimal("0.70")  # case 1: a limit buy 30% below the market never fills
CANDLES_1H = 100
POLL_SECONDS = 1.0
POLL_TRIES = 15

pytestmark = [
    pytest.mark.okx_demo,
    pytest.mark.skipif(
        os.environ.get(RUN_VAR) != "1",
        reason=f"OKX demo integration tests run only with {RUN_VAR}=1 (they trade on the demo account)",
    ),
]


# --- the world: test DB + the stored demo connection ----------------------------------------------


def _app_key() -> str:
    key = os.environ.get("APP_KEY", "")
    if key:
        return key
    if shutil.which("security"):
        # The same Keychain entry ./atlas up loads (brief §18). The value never leaves this process.
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "atlas", "-a", "APP_KEY", "-w"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    pytest.fail(
        "APP_KEY is not set and not in the Keychain (service atlas, account APP_KEY); export it first."
    )


def _source_engine() -> Engine:
    settings = testing.db_settings()
    settings["DB_DATABASE"] = os.environ.get(SOURCE_DB_VAR) or os.environ.get("DB_DATABASE") or "atlas"
    return make_engine(settings)


@dataclass
class Demo:
    engine: Engine
    rds: redis.Redis
    connection: Connection
    app_key: str

    def client(self) -> OkxClient:
        return client_for_connection(self.connection, self.app_key)


def _connection_values(row: Any) -> dict[str, Any]:
    return {c.name: getattr(row, c.key) for c in Connection.__table__.columns}


@pytest.fixture(scope="module")
def demo() -> Iterator[Demo]:
    app_key = _app_key()
    source = _source_engine()
    try:
        with Session(source) as s:
            stored = s.scalar(
                select(Connection)
                .where(
                    Connection.exchange == "okx",
                    Connection.env == ENV,
                    Connection.last_verified_at.is_not(None),
                )
                .order_by(Connection.last_verified_at.desc())
                .limit(1)
            )
            if stored is not None:
                s.expunge(stored)
    except OperationalError as exc:
        pytest.fail(f"the app database is not reachable: {exc}")
    finally:
        source.dispose()
    if stored is None:
        pytest.fail(
            "No verified OKX demo connection in the app database. Save the demo key in Settings -> "
            "Connections and press Test connection, then run this again."
        )

    engine = make_engine(testing.db_settings())
    rds = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        rds.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    with engine.begin() as c:
        old_conns = [
            dict(r._mapping)
            for r in c.execute(
                select(Connection.__table__).where(Connection.exchange == "okx", Connection.env == ENV)
            )
        ]
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_state = [
            dict(r._mapping) for r in c.execute(select(TradingState.__table__).where(TradingState.env == ENV))
        ]
        c.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
        c.execute(insert(Connection).values(**_connection_values(stored)))

    world = Demo(engine, rds, stored, app_key)

    async def setup() -> None:
        async with world.client() as okx:
            await sync_instruments(ENV, okx=okx, engine=engine, inst_types=["SWAP"])

    try:
        asyncio.run(setup())
    except OkxNetworkError as exc:
        pytest.fail(f"OKX's REST host is not reachable from this machine (check DNS / network): {exc}")
    try:
        yield world
    finally:
        with engine.begin() as c:
            c.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == ENV))
            for row in old_conns:
                c.execute(insert(Connection).values(**row))
            c.execute(text("DELETE FROM trading_state WHERE env = :e"), {"e": ENV})
            for row in old_state:
                c.execute(insert(TradingState).values(**row))
            if old_settings is None:
                c.execute(text("DELETE FROM settings WHERE env = :e"), {"e": ENV})
            else:
                c.execute(
                    text("UPDATE settings SET version = :v, data = CAST(:d AS jsonb) WHERE env = :e"),
                    {"v": old_settings[0], "d": json.dumps(old_settings[1]), "e": ENV},
                )
        rds.close()
        engine.dispose()


# --- OKX reads ------------------------------------------------------------------------------------


async def pending_orders(trade: OkxTradeClient, inst_id: str | None = None) -> list[OrderDetail]:
    return await trade.get_orders_pending(inst_id=inst_id)


async def pending_algos(trade: OkxTradeClient, inst_id: str | None = None) -> list[AlgoOrderDetail]:
    out: list[AlgoOrderDetail] = []
    for kind in ALGO_KINDS:
        out.extend(await trade.get_algo_orders_pending(kind, inst_id=inst_id))
    return out


async def open_positions(okx: OkxClient, inst_id: str | None = None) -> list[Position]:
    rows = await okx.get_positions(inst_type="SWAP", inst_id=inst_id)
    return [p for p in rows if p.pos is not None and p.pos != 0]


def stop_algos(algos: list[AlgoOrderDetail]) -> list[AlgoOrderDetail]:
    return [a for a in algos if a.sl_trigger_px is not None and a.state in ("live", "partially_effective")]


async def eventually(check: Any, what: str) -> Any:
    """Poll ``check()`` (async, returns a truthy value when done) — OKX's lists lag an order by a moment."""
    last: Any = None
    for _ in range(POLL_TRIES):
        last = await check()
        if last:
            return last
        await asyncio.sleep(POLL_SECONDS)
    pytest.fail(f"timed out waiting for {what}; last read: {last!r}")


async def clean_instrument(demo: Demo, inst_id: str) -> list[str]:
    """Cancel the instrument's pending orders and algos and close its position. Returns what failed."""
    problems: list[str] = []
    async with demo.client() as okx:
        trade = OkxTradeClient(okx)
        try:
            for o in await pending_orders(trade, inst_id):
                try:
                    await trade.cancel_order(inst_id, ord_id=o.ord_id)
                except Exception as exc:
                    problems.append(f"cancel order {o.ord_id}: {exc}")
            algos = await pending_algos(trade, inst_id)
            if algos:
                try:
                    await trade.cancel_algos([(inst_id, a.algo_id) for a in algos])
                except Exception as exc:
                    problems.append(f"cancel algos: {exc}")
            for p in await open_positions(okx, inst_id):
                try:
                    await trade.close_position(
                        inst_id, cast(MgnMode, p.mgn_mode), pos_side=cast(PosSide, p.pos_side), auto_cxl=True
                    )
                except Exception as exc:
                    problems.append(f"close {inst_id} {p.pos_side}: {exc}")
        except Exception as exc:
            problems.append(f"read {inst_id}: {exc}")
    return problems


async def require_untouched(demo: Demo, inst_id: str) -> None:
    async with demo.client() as okx:
        trade = OkxTradeClient(okx)
        pos = await open_positions(okx, inst_id)
        orders = await pending_orders(trade, inst_id)
        algos = await pending_algos(trade, inst_id)
    if pos or orders or algos:
        pytest.skip(
            f"{inst_id} already has {len(pos)} position(s), {len(orders)} order(s) and {len(algos)} algo "
            "order(s) on the demo account; this suite does not touch trades it did not open."
        )


# --- the inputs the Risk Engine reads --------------------------------------------------------------


def _settings(cap_usd: Decimal) -> dict[str, Any]:
    """Settings for one case: the trade's notional is capped at OKX's minimum size; the account-level
    limits (open positions, daily loss, trades per day) are opened up so the owner's own positions or
    earlier runs never decide the outcome."""
    return {
        "risk_profile": "balanced",
        "autonomy_level": 2,
        "max_leverage": 3,
        "max_usdt_per_trade": str(cap_usd),
        "bot_capital_cap_usdt": str(DEFAULTS["money_limits"]["bot_capital_cap_usdt"]),
        "max_new_exposure_per_day_usdt": "1000000",
        "max_trades_per_day": 100000,
        "max_open_positions": 1000,
        "max_daily_loss_pct": "100",
        "max_weekly_loss_pct": "100",
        "max_consecutive_losses": 100000,
        "max_correlated_exposure_pct": "100",
        "max_share_per_instrument_pct": "100",
        "block_new_trades_minutes_before_high_impact_event": 0,
    }


@dataclass
class Prepared:
    inst_id: str
    proposal_id: uuid.UUID
    decision: RiskDecision
    mark: Decimal
    entry: Decimal
    stop: Decimal


async def _load_market(demo: Demo, okx: OkxClient, inst_id: str) -> Decimal:
    candles = await okx.get_candles(inst_id, bar="1H", limit=CANDLES_1H)
    rows = candle_rows(ENV, inst_id, "1H", candles)
    if rows:
        with demo.engine.begin() as c:
            c.execute(pg_insert(Candle).values(rows).on_conflict_do_nothing())
    tickers = [t for t in await okx.get_tickers("SWAP") if t.inst_id == inst_id]
    marks = await okx.get_mark_price("SWAP", inst_id=inst_id)
    if not tickers or tickers[0].last is None:
        pytest.fail(f"OKX demo returned no ticker for {inst_id}")
    last = tickers[0].last
    mark = marks[0].mark_px if marks and marks[0].mark_px is not None else last
    now_ms = md.to_ms(datetime.now(UTC))
    demo.rds.hset(
        md.md_key(ENV, inst_id),
        mapping={
            md.LAST_MSG_MS: now_ms,
            "tickers_ms": now_ms,
            "mark-price_ms": now_ms,
            md.TICKER: json.dumps({"instId": inst_id, "last": str(last)}),
            md.MARK: json.dumps({"instId": inst_id, "markPx": str(mark)}),
        },
    )
    return mark


def _atr(demo: Demo, inst_id: str) -> Decimal | None:
    with Session(demo.engine) as s:
        return _atr_1h(s, ENV, inst_id, datetime.now(UTC), rules_config())


async def prepare(demo: Demo, inst_id: str, *, order_type: str) -> Prepared:
    """Sync the account and market data, write a ``passed`` buy proposal at OKX's minimum size and run
    the real Risk Engine on it. Fails unless the check approves it."""
    with Session(demo.engine) as s:
        inst = s.scalar(select(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
    if inst is None or inst.state != "live":
        pytest.skip(f"{inst_id} is not a live instrument in the demo universe")
    assert inst.tick_sz and inst.lot_sz and inst.min_sz and inst.ct_val
    async with demo.client() as okx:
        await sync_account(ENV, okx=okx, engine=demo.engine)
        mark = await _load_market(demo, okx, inst_id)
    atr = _atr(demo, inst_id) or mark * STOP_MIN_PCT
    entry = round_to_tick(mark * LIMIT_DISCOUNT if order_type == "limit" else mark, inst.tick_sz)
    distance = min(max(atr * STOP_ATR_MULT, entry * STOP_MIN_PCT), entry * STOP_MAX_PCT)
    stop = round_to_tick(entry - distance, inst.tick_sz, ROUND_FLOOR)
    tp = round_to_tick(entry + distance * TP_R, inst.tick_sz, ROUND_CEILING)

    # Cap the notional so the Risk Engine's round-down to lotSz lands on exactly minSz.
    spec = _instrument_info(inst).spec
    cv = contract_value_usd(spec, entry, None)
    cap = ((inst.min_sz + inst.lot_sz / 2) * cv).quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)

    pid = uuid.uuid4()
    now = datetime.now(UTC)
    with demo.engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level,"
                " max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at)"
                " VALUES (:id, :e, 1, CAST(:d AS jsonb), 'balanced', 2, 1, 5, 3, now(), now())"
                " ON CONFLICT (env) DO UPDATE SET version = settings.version + 1, data = EXCLUDED.data,"
                " updated_at = now()"
            ),
            {"id": uuid.uuid4(), "e": ENV, "d": json.dumps(_settings(cap))},
        )
        c.execute(text("DELETE FROM trading_state WHERE env = :e"), {"e": ENV})
        c.execute(
            insert(Proposal).values(
                id=pid,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst_id,
                side="buy",
                order_type=order_type,
                entry=entry,
                stop=stop,
                expected_r=TP_R,
                probability=Decimal("0.5"),
                take_profits=[{"price": str(tp), "pct": 100}],
                leverage_suggestion=3,
                size_suggestion_pct=Decimal(100),
                rationale_md="ATL-152 OKX demo integration test",
                status="passed",
                created_at=now,
                updated_at=now,
            )
        )
    decision = evaluate(pid, engine=demo.engine, redis_client=demo.rds)
    if decision.result not in ("approved", "resized"):
        pytest.fail(f"the Risk Engine refused the test proposal: {decision.reason} ({decision.failed_check})")
    assert decision.final_size == inst.min_sz, (decision.final_size, inst.min_sz)
    return Prepared(inst_id, pid, decision, mark, entry, stop)


async def send(demo: Demo, prepared: Prepared) -> ExecutionResult:
    result = await execute(
        prepared.decision.risk_check_id,
        "owner",
        engine=demo.engine,
        redis_client=demo.rds,
        app_key=demo.app_key,
        active_env=ENV,
    )
    assert result.status == "sent", result.payload()
    assert result.stop_verified, result.payload()
    return result


async def open_min_position(demo: Demo, inst_id: str) -> tuple[Prepared, ExecutionResult, Position]:
    prepared = await prepare(demo, inst_id, order_type="market")
    result = await send(demo, prepared)

    async def position() -> Position | None:
        async with demo.client() as okx:
            rows = await open_positions(okx, inst_id)
        return rows[0] if rows else None

    pos = await eventually(position, f"the {inst_id} position")
    assert abs(pos.pos) == prepared.decision.final_size
    return prepared, result, pos


async def stops_on(demo: Demo, inst_id: str) -> list[AlgoOrderDetail]:
    async with demo.client() as okx:
        return stop_algos(await pending_algos(OkxTradeClient(okx), inst_id))


def forget_rows(demo: Demo, inst_id: str) -> None:
    """Drop this instrument's order rows so the next case starts from a DB that matches OKX."""
    with demo.engine.begin() as c:
        c.execute(delete(AlgoOrder).where(AlgoOrder.env == ENV, AlgoOrder.inst_id == inst_id))
        c.execute(delete(Order).where(Order.env == ENV, Order.inst_id == inst_id))
    demo.rds.delete(md.md_key(ENV, inst_id))


@pytest.fixture
def cleanup(demo: Demo) -> Iterator[list[str]]:
    """Each test appends the instruments it trades; they are flattened afterwards, pass or fail."""
    touched: list[str] = []
    yield touched
    problems: list[str] = []
    for inst_id in touched:
        problems += asyncio.run(clean_instrument(demo, inst_id))
        forget_rows(demo, inst_id)
    with demo.engine.begin() as c:
        c.execute(text("DELETE FROM trading_state WHERE env = :e"), {"e": ENV})
    assert not problems, f"cleanup left something on the demo account: {problems}"


# --- the cases --------------------------------------------------------------------------------------


async def test_limit_order_place_and_cancel(demo: Demo, cleanup: list[str]) -> None:
    """(1) A limit buy 30% below the market is live on OKX, the cancel path removes it, nothing is left."""
    await require_untouched(demo, BTC)
    cleanup.append(BTC)
    prepared = await prepare(demo, BTC, order_type="limit")
    result = await send(demo, prepared)
    assert result.state == "live", result.payload()

    async def listed() -> bool:
        async with demo.client() as okx:
            return any(o.ord_id == result.ord_id for o in await pending_orders(OkxTradeClient(okx), BTC))

    await eventually(listed, "the limit order in orders-pending")

    report = await cancel_order(
        ENV, str(result.order_id), engine=demo.engine, redis_client=demo.rds, app_key=demo.app_key
    )
    assert report["status"] == "cancelled", report

    async def gone() -> bool:
        async with demo.client() as okx:
            trade = OkxTradeClient(okx)
            return not await pending_orders(trade, BTC) and not await pending_algos(trade, BTC)

    await eventually(gone, "no BTC order or algo left")
    async with demo.client() as okx:
        assert not await open_positions(okx, BTC)


@pytest.mark.parametrize("inst_id", [BTC, XAU])
async def test_min_size_market_order_has_server_side_stop(
    demo: Demo, cleanup: list[str], inst_id: str
) -> None:
    """(2) A minimum-size market buy with attached TP/SL: OKX lists the SL algo while the position is
    open; closing it leaves the instrument flat with no algo behind."""
    with Session(demo.engine) as s:
        known = s.scalar(select(Instrument.state).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
    if known is None:
        pytest.skip(f"{inst_id} is not in the OKX demo universe right now (instruments sync did not list it)")
    await require_untouched(demo, inst_id)
    cleanup.append(inst_id)
    prepared, _, pos = await open_min_position(demo, inst_id)

    stops = await eventually(lambda: stops_on(demo, inst_id), f"an SL algo for {inst_id} on OKX")
    assert any(a.sl_trigger_px is not None and a.sl_trigger_px <= prepared.mark for a in stops), stops

    report = await close_position(
        ENV, inst_id, pos.pos_side, engine=demo.engine, redis_client=demo.rds, app_key=demo.app_key
    )
    assert report["status"] == "closed", report

    async def flat() -> bool:
        async with demo.client() as okx:
            trade = OkxTradeClient(okx)
            return not await open_positions(okx, inst_id) and not await pending_algos(trade, inst_id)

    await eventually(flat, f"{inst_id} flat with no algo left")


async def test_kill_switch_flatten(demo: Demo, cleanup: list[str]) -> None:
    """(3) Kill with flatten: afterwards orders-pending, orders-algo-pending and positions are empty."""
    await require_untouched(demo, BTC)
    if os.environ.get(FLATTEN_ALL_VAR) != "1":
        async with demo.client() as okx:
            trade = OkxTradeClient(okx)
            others = (
                len(await open_positions(okx)),
                len(await pending_orders(trade)),
                len(await pending_algos(trade)),
            )
        if any(others):
            pytest.skip(
                f"the demo account has {others[0]} position(s), {others[1]} order(s) and {others[2]} algo "
                f"order(s) open; the kill switch would close or cancel them. "
                f"Set {FLATTEN_ALL_VAR}=1 to allow it."
            )
    cleanup.append(BTC)
    await open_min_position(demo, BTC)
    await eventually(lambda: stops_on(demo, BTC), "the BTC SL algo before the kill")

    report = await kill(ENV, flatten=True, engine=demo.engine, redis_client=demo.rds, app_key=demo.app_key)
    assert report["ok"], report
    assert report["closed"] >= 1, report

    async def empty() -> tuple[int, int, int] | None:
        async with demo.client() as okx:
            trade = OkxTradeClient(okx)
            counts = (
                len(await pending_orders(trade, BTC)),
                len(await pending_algos(trade, BTC)),
                len(await open_positions(okx, BTC)),
            )
        return None if any(counts) else counts

    await eventually(empty, "no BTC order, algo or position after the kill")
    with demo.engine.connect() as c:
        paused = c.execute(text("SELECT paused FROM trading_state WHERE env = :e"), {"e": ENV}).scalar()
    assert paused is True


async def test_reconciliation_replaces_a_removed_stop(demo: Demo, cleanup: list[str]) -> None:
    """(4) Remove the position's stop directly on OKX; one reconciliation run places a new SL."""
    await require_untouched(demo, BTC)
    cleanup.append(BTC)
    prepared, _, pos = await open_min_position(demo, BTC)
    stops = await eventually(lambda: stops_on(demo, BTC), "the BTC SL algo")

    async with demo.client() as okx:
        await OkxTradeClient(okx).cancel_algos([(BTC, a.algo_id) for a in stops])

    async def unprotected() -> bool:
        return not await stops_on(demo, BTC)

    await eventually(unprotected, "the BTC stop to be gone on OKX")
    # Reconciliation leaves a position alone while the Executor may still be protecting it
    # (reconciliation.protect_grace_seconds after the entry); wait that out like a real 5-minute run would.
    await asyncio.sleep(float(DEFAULTS["reconciliation"]["protect_grace_seconds"]) + 2)

    async with demo.client() as okx:
        result = await reconcile(ENV, okx=okx, engine=demo.engine, redis_client=demo.rds)
    assert result.positions >= 1, result.as_dict()
    placed = [p for p in result.protected if p.get("inst_id") == BTC and p.get("placed")]
    assert len(placed) == 1, result.as_dict()

    new_stops = await eventually(lambda: stops_on(demo, BTC), "a new BTC SL algo after reconciliation")
    assert {a.algo_id for a in new_stops}.isdisjoint({a.algo_id for a in stops})
    assert all(a.sl_trigger_px is not None and a.sl_trigger_px < prepared.mark for a in new_stops), new_stops
    assert any(a.algo_cl_ord_id and a.algo_cl_ord_id.startswith("r") for a in new_stops), new_stops
    assert pos.inst_id == BTC
