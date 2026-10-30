"""ATL-125: the OKX WebSocket market feed, against a fake WebSocket server (never OKX).

Redis is the local test Redis on DB ``REDIS_TEST_DB`` (15, like Pest); candles go to this
checkout's test DB (atlas_shared.testing), migrated by `cd backend && php artisan test`. Time is a
simulated clock: pings, staleness and reconnect backoff advance it by hand.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import redis
from atlas_shared import bus, testing
from atlas_shared import market_data as md
from atlas_shared.db import (
    BalanceSnapshot,
    Candle,
    PositionSnapshot,
    Watchlist,
    WatchlistItem,
    make_engine,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import OperationalError
from trading_core.ws import MarketFeed, backoff_seconds, desired_instruments, ws_urls
from trading_core.ws import feed as feed_mod
from websockets.asyncio.server import ServerConnection, serve

ENV = "demo"
T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
TEST_DB = testing.db_settings()


# -- simulated clock ---------------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._waiters: list[tuple[datetime, asyncio.Future[None]]] = []
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self._now + timedelta(seconds=seconds), fut))
        self.sleeps.append(seconds)
        await fut

    async def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        due = [(d, f) for d, f in self._waiters if d <= self._now]
        self._waiters = [(d, f) for d, f in self._waiters if d > self._now]
        for _, fut in due:
            if not fut.done():
                fut.set_result(None)
        await _settle()


async def _settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0.005)


async def _until(cond: Any, within: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# -- fake OKX WebSocket server -----------------------------------------------------------------------


class FakeOkx:
    """One fake endpoint: records every frame clients send and pushes frames to the live ones."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.conns: list[ServerConnection] = []
        self.connections = 0
        self.url = ""

    async def handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        self.conns.append(ws)
        try:
            async for msg in ws:
                text = msg if isinstance(msg, str) else msg.decode()
                self.received.append(text)
                if text == "ping":
                    await ws.send("pong")
        finally:
            self.conns.remove(ws)

    async def push(self, payload: dict[str, Any]) -> None:
        for ws in list(self.conns):
            await ws.send(json.dumps(payload))

    async def drop(self) -> None:
        for ws in list(self.conns):
            await ws.close()

    def subscribed(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for text in self.received:
            if text == "ping":
                continue
            msg = json.loads(text)
            for arg in msg.get("args", []):
                pair = (arg["channel"], arg["instId"])
                if msg["op"] == "subscribe":
                    pairs.add(pair)
                else:
                    pairs.discard(pair)
        return pairs

    def pings(self) -> int:
        return sum(1 for t in self.received if t == "ping")


@pytest.fixture
async def servers() -> AsyncIterator[dict[str, FakeOkx]]:
    fakes = {"public": FakeOkx(), "business": FakeOkx()}
    started = []
    for fake in fakes.values():
        server = await serve(fake.handler, "127.0.0.1", 0)
        port = next(iter(server.sockets)).getsockname()[1]
        fake.url = f"ws://127.0.0.1:{port}"
        started.append(server)
    try:
        yield fakes
    finally:
        for server in started:
            server.close()
            await server.wait_closed()


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete("atlas.market")
    yield conn
    conn.delete("atlas.market")
    conn.close()


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


def _inst() -> str:
    return f"WS{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"


def _events(rds: redis.Redis, kind: str | None = None) -> list[EventEnvelope]:
    out = []
    for _, fields in rds.xrange("atlas.market"):
        env = EventEnvelope.model_validate_json(fields[bus.FIELD])
        if kind is None or env.type == kind:
            out.append(env)
    return out


def _feed(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx], clock: FakeClock, insts: set[str]
) -> MarketFeed:
    return MarketFeed(
        ENV,
        engine,
        rds,
        clock=clock,
        instruments=lambda: set(insts),
        urls={"public": servers["public"].url, "business": servers["business"].url},
        rand=lambda: 0.0,
    )


async def _started(feed: MarketFeed, servers: dict[str, FakeOkx], insts: set[str]) -> None:
    await feed.start()
    want_pub = {(ch, i) for ch in ("tickers", "mark-price", "books5") for i in insts}
    want_biz = {(ch, i) for ch in ("candle1m", "candle5m", "candle1H") for i in insts}
    await _until(lambda: servers["public"].subscribed() == want_pub)
    await _until(lambda: servers["business"].subscribed() == want_biz)


def _ticker(inst: str, last: str = "100.5") -> dict[str, Any]:
    return {
        "arg": {"channel": "tickers", "instId": inst},
        "data": [
            {
                "instType": "SWAP",
                "instId": inst,
                "last": last,
                "lastSz": "1",
                "askPx": "100.6",
                "askSz": "3",
                "bidPx": "100.4",
                "bidSz": "2",
                "open24h": "99",
                "high24h": "101",
                "low24h": "98",
                "volCcy24h": "1000",
                "vol24h": "10000",
                "sodUtc0": "99.5",
                "sodUtc8": "99.2",
                "ts": str(md.to_ms(T0)),
            }
        ],
    }


# -- AC1: ticker -> Redis snapshot + market.tick -----------------------------------------------------


async def test_ticker_lands_in_redis_and_publishes_one_throttled_tick(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    inst = _inst()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, {inst})
    try:
        await _started(feed, servers, {inst})
        await servers["public"].push(_ticker(inst))
        await _until(lambda: md.read_snapshot(rds, ENV, inst).ticker is not None)

        snap = md.read_snapshot(rds, ENV, inst)
        assert rds.exists(f"atlas:md:demo:{inst}")
        assert snap.ticker is not None and snap.ticker["last"] == "100.5"
        assert snap.last_msg_at is not None
        assert abs((snap.last_msg_at - clock.now()).total_seconds()) < 1
        assert not snap.is_stale(clock.now())

        ticks = _events(rds, "market.tick")
        assert len(ticks) == 1
        tick = ticks[0]
        assert tick.env == ENV and tick.source.startswith("service:trading_core")
        assert tick.payload["inst_id"] == inst
        assert Decimal(tick.payload["last"]) == Decimal("100.5")
        assert Decimal(tick.payload["bid_px"]) == Decimal("100.4")

        # A second push inside the same second updates Redis but is not published again.
        await servers["public"].push(_ticker(inst, "100.7"))
        await _until(lambda: (md.read_snapshot(rds, ENV, inst).ticker or {}).get("last") == "100.7")
        assert len(_events(rds, "market.tick")) == 1
        await clock.advance(1)
        await servers["public"].push(_ticker(inst, "100.9"))
        await _until(lambda: len(_events(rds, "market.tick")) == 2)
    finally:
        await feed.stop()
        rds.delete(md.md_key(ENV, inst))


# -- AC2: a confirmed candle is upserted once and published once -------------------------------------


async def test_confirmed_candle_upserts_once_and_publishes_once_even_when_pushed_twice(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    inst = _inst()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, {inst})
    bar_ts = T0 - timedelta(hours=1)
    forming = [str(md.to_ms(T0)), "1", "2", "0.5", "1.5", "10", "5", "50", "0"]
    closed = [str(md.to_ms(bar_ts)), "100", "110", "95", "105", "1000", "10", "1050", "1"]
    try:
        await _started(feed, servers, {inst})
        await servers["business"].push({"arg": {"channel": "candle1H", "instId": inst}, "data": [forming]})
        await servers["business"].push({"arg": {"channel": "candle1H", "instId": inst}, "data": [closed]})
        await servers["business"].push({"arg": {"channel": "candle1H", "instId": inst}, "data": [closed]})
        await _until(lambda: len(_events(rds, "candle.closed")) >= 1)
        await _settle()

        with engine.connect() as conn:
            rows = conn.execute(select(Candle).where(Candle.env == ENV, Candle.inst_id == inst)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.bar == "1H" and row.ts == bar_ts and row.confirmed is True
        assert row.c == Decimal("105") and row.vol == Decimal("1000")

        events = _events(rds, "candle.closed")
        assert len(events) == 1
        assert events[0].payload == {
            "inst_id": inst,
            "bar": "1H",
            "ts": bar_ts.isoformat(),
            "o": "100",
            "h": "110",
            "l": "95",
            "c": "105",
            "vol": "1000",
            "vol_ccy": "10",
            "vol_ccy_quote": "1050",
        }

        # A restarted feed remembers the bar it already published (closed:<bar> in Redis).
        await feed.stop()
        again = _feed(engine, rds, servers, clock, {inst})
        try:
            await again.on_message(
                json.dumps({"arg": {"channel": "candle1H", "instId": inst}, "data": [closed]})
            )
        finally:
            await again.stop()
        assert len(_events(rds, "candle.closed")) == 1
    finally:
        await feed.stop()
        rds.delete(md.md_key(ENV, inst))
        with engine.begin() as conn:
            conn.execute(delete(Candle).where(Candle.env == ENV, Candle.inst_id == inst))


async def test_candle_upsert_uses_the_poller_path(
    engine: Engine, rds: redis.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same ON CONFLICT upsert as the poller: a bar the poller already stored is updated, not doubled."""
    calls: list[int] = []
    real = feed_mod.upsert_rows

    def spy(conn: Any, model: Any, rows: Any) -> int:
        calls.append(len(rows))
        return real(conn, model, rows)

    monkeypatch.setattr(feed_mod, "upsert_rows", spy)
    inst = _inst()
    bar_ts = T0 - timedelta(minutes=5)
    from trading_core.backfill.runner import upsert_rows

    with engine.begin() as conn:
        upsert_rows(
            conn,
            Candle,
            [
                {
                    "env": ENV,
                    "inst_id": inst,
                    "bar": "5m",
                    "ts": bar_ts,
                    "o": 1,
                    "h": 1,
                    "l": 1,
                    "c": 1,
                    "vol": 1,
                    "vol_ccy": None,
                    "vol_ccy_quote": None,
                    "confirmed": True,
                }
            ],
        )
    feed = MarketFeed(
        ENV, engine, rds, clock=FakeClock(T0), instruments=set, urls={"public": "", "business": ""}
    )
    try:
        await feed.on_message(
            json.dumps(
                {
                    "arg": {"channel": "candle5m", "instId": inst},
                    "data": [[str(md.to_ms(bar_ts)), "2", "3", "1", "2.5", "7", "1", "2", "1"]],
                }
            )
        )
        with engine.connect() as conn:
            n = conn.execute(select(func.count()).select_from(Candle).where(Candle.inst_id == inst)).scalar()
            c = conn.execute(select(Candle.c).where(Candle.inst_id == inst)).scalar()
        assert calls == [1] and n == 1 and c == Decimal("2.5")
    finally:
        rds.delete(md.md_key(ENV, inst))
        with engine.begin() as conn:
            conn.execute(delete(Candle).where(Candle.inst_id == inst))


# -- AC3: staleness and reconnect --------------------------------------------------------------------


async def test_silent_instrument_goes_stale_after_max_age(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    inst = _inst()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, {inst})
    max_age = float(DEFAULTS["market_ws"]["market_data_max_age_seconds"])
    assert max_age == 30
    try:
        await _started(feed, servers, {inst})
        await servers["public"].push(_ticker(inst))
        await _until(lambda: md.read_snapshot(rds, ENV, inst).ticker is not None)

        await clock.advance(29)
        assert not feed.is_stale(inst)
        assert not md.read_snapshot(rds, ENV, inst).is_stale(clock.now())

        await clock.advance(2)
        assert feed.is_stale(inst)
        assert md.read_snapshot(rds, ENV, inst).is_stale(clock.now())
        await feed.check_stale()
        stale = _events(rds, "market.stale")
        assert [e.payload["inst_id"] for e in stale] == [inst]
        await feed.check_stale()
        assert len(_events(rds, "market.stale")) == 1  # published once per transition

        await servers["public"].push(_ticker(inst))
        await _until(lambda: len(_events(rds, "market.fresh")) == 1)
        assert not feed.is_stale(inst)
    finally:
        await feed.stop()
        rds.delete(md.md_key(ENV, inst))


async def test_dropped_connection_reconnects_with_backoff_and_resubscribes_everything(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    insts = {_inst(), _inst()}
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, insts)
    try:
        await _started(feed, servers, insts)
        assert servers["public"].connections == 1 and servers["business"].connections == 1
        first = len(servers["public"].received)

        await servers["public"].drop()
        await _until(lambda: feed.links["public"].ws is None)
        # rand() = 0 means no jitter cut: the first retry waits the base delay, from config.
        base = float(DEFAULTS["market_ws"]["reconnect_base_seconds"])
        assert feed.links["public"].backoffs == [base]
        assert servers["public"].connections == 1  # still waiting out the backoff
        await clock.advance(base)
        await _until(lambda: servers["public"].connections == 2)

        resent = [json.loads(t) for t in servers["public"].received[first:] if t != "ping"]
        pairs = {(a["channel"], a["instId"]) for m in resent if m["op"] == "subscribe" for a in m["args"]}
        assert pairs == {(ch, i) for ch in ("tickers", "mark-price", "books5") for i in insts}
        assert servers["business"].connections == 1  # the other socket was untouched
    finally:
        await feed.stop()


def test_backoff_is_exponential_capped_and_jittered() -> None:
    cfg = DEFAULTS["market_ws"]
    base, cap, jitter = (
        float(cfg["reconnect_base_seconds"]),
        float(cfg["reconnect_max_seconds"]),
        float(cfg["reconnect_jitter"]),
    )
    assert [backoff_seconds(n, lambda: 0.0) for n in range(4)] == [base, base * 2, base * 4, base * 8]
    assert backoff_seconds(50, lambda: 0.0) == cap
    assert backoff_seconds(3, lambda: 1.0) == pytest.approx(base * 8 * (1 - jitter))


async def test_refresh_subscribes_new_and_unsubscribes_gone_instruments(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    a, b = _inst(), _inst()
    insts = {a}
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, insts)
    try:
        await _started(feed, servers, {a})
        insts.clear()
        insts.add(b)
        await clock.advance(float(DEFAULTS["market_ws"]["subscriptions_refresh_seconds"]))
        await _until(
            lambda: servers["public"].subscribed() == {(ch, b) for ch in ("tickers", "mark-price", "books5")}
        )
        await _until(lambda: {i for _, i in servers["business"].subscribed()} == {b})
    finally:
        await feed.stop()


# -- AC4: pings and config URLs ----------------------------------------------------------------------


async def test_client_pings_every_25_seconds(
    engine: Engine, rds: redis.Redis, servers: dict[str, FakeOkx]
) -> None:
    inst = _inst()
    clock = FakeClock(T0)
    feed = _feed(engine, rds, servers, clock, {inst})
    assert DEFAULTS["market_ws"]["ping_interval_seconds"] == 25
    try:
        await _started(feed, servers, {inst})
        await clock.advance(24)
        assert servers["public"].pings() == 0 and servers["business"].pings() == 0
        await clock.advance(1)
        await _until(lambda: servers["public"].pings() == 1 and servers["business"].pings() == 1)
        await clock.advance(25)
        await _until(lambda: servers["public"].pings() == 2 and servers["business"].pings() == 2)
    finally:
        await feed.stop()


def test_ws_urls_come_from_config_and_no_ws_url_literal_is_in_code() -> None:
    envs = DEFAULTS["okx"]["environments"]
    for env in ("demo", "live"):
        assert ws_urls(env) == {
            "public": envs[env]["ws_public_url"],
            "business": envs[env]["ws_business_url"],
        }
    assert MarketFeed(ENV, make_engine(TEST_DB), None).links["public"].url == envs["demo"]["ws_public_url"]
    src = Path(feed_mod.__file__).resolve().parents[1]
    literal = re.compile(r"wss?://")
    offenders = [str(p) for p in src.rglob("*.py") if literal.search(p.read_text())]
    assert offenders == []


# -- subscription set from the DB --------------------------------------------------------------------


def test_desired_instruments_are_btc_plus_watchlist_plus_open_positions(engine: Engine) -> None:
    env = "live"  # keep clear of the demo rows other tests write
    pick, removed, held, flat = _inst(), _inst(), _inst(), _inst()
    now = datetime(2026, 10, 30, 22, 30, tzinfo=UTC)  # 01:30 on 31 Oct in Baghdad
    wl_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            delete(WatchlistItem).where(
                WatchlistItem.watchlist_id.in_(select(Watchlist.id).where(Watchlist.env == env))
            )
        )
        conn.execute(delete(Watchlist).where(Watchlist.env == env))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.env == env))
        conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == env))
    with engine.begin() as conn:
        stamp = {"created_at": now, "updated_at": now}
        conn.execute(
            Watchlist.__table__.insert().values(
                id=wl_id,
                env=env,
                date=date(2026, 10, 31),
                version=1,
                created_by="market_scout",
                status="published",
                data={},
                **stamp,
            )
        )
        for rank, (inst, gone) in enumerate([(pick, None), (removed, now)], start=1):
            conn.execute(
                WatchlistItem.__table__.insert().values(
                    id=uuid.uuid4(),
                    watchlist_id=wl_id,
                    inst_id=inst,
                    rank=rank,
                    asset_class="crypto",
                    bias="long",
                    conviction=Decimal("0.6"),
                    thesis="t",
                    catalysts=[],
                    key_levels={},
                    invalidation="i",
                    removed_by_owner_at=gone,
                    data_as_of=now,
                    **stamp,
                )
            )
        conn.execute(
            BalanceSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=env,
                account="trading",
                ccy="USDT",
                eq=Decimal("1"),
                taken_at=now,
                **stamp,
            )
        )
        for inst, pos in ((held, Decimal("2")), (flat, Decimal("0"))):
            conn.execute(
                PositionSnapshot.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=env,
                    inst_id=inst,
                    pos_side="net",
                    pos=pos,
                    mgn_mode="isolated",
                    taken_at=now,
                    **stamp,
                )
            )
    try:
        assert desired_instruments(engine, env, now) == {"BTC-USDT-SWAP", pick, held}
        # The day before (Baghdad) has no watchlist.
        assert desired_instruments(engine, env, now - timedelta(days=1)) == {"BTC-USDT-SWAP", held}
    finally:
        with engine.begin() as conn:
            conn.execute(delete(WatchlistItem).where(WatchlistItem.watchlist_id == wl_id))
            conn.execute(delete(Watchlist).where(Watchlist.id == wl_id))
            conn.execute(delete(PositionSnapshot).where(PositionSnapshot.env == env))
            conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == env))
