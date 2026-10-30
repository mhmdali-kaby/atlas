"""ATL-144: protection follows the filled size, limit entries expire, proposals carry the fill status.

Fill events go through the real private-feed path (``PrivateFeed.on_push`` → ``order_store`` →
``EntryLifecycle.on_order``). OKX is a stateful respx mock: pending algo orders live in a dict that
amend/cancel/place change. The database and Redis are the real test instances.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
import redis
import respx
from atlas_shared import bus, testing
from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Fill,
    Instrument,
    Order,
    Proposal,
    RiskCheck,
    TradingState,
    make_engine,
)
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import OperationalError
from trading_core.execution.config import derived_id
from trading_core.execution.lifecycle import EntryLifecycle
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.ws.private import PrivateFeed

ENV = "demo"
AT = datetime(2031, 3, 12, 15, 0, tzinfo=UTC)
BASE = "https://www.okx.com"
CREDS = OkxCredentials(api_key="test-key-0000", secret="test-only-secret", passphrase="p")  # noqa: S106
NOW_MS = 1_793_207_736_680
STREAMS = ("atlas.orders", "atlas.system", "atlas.warroom", "atlas.proposals", "atlas.account")
TEST_DB = testing.db_settings()


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


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete(*STREAMS)
    yield conn
    conn.delete(*STREAMS)
    conn.close()


@pytest.fixture
def world(engine: Engine) -> Iterator[dict[str, Any]]:
    inst_id = f"LC{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    with engine.begin() as c:
        old_settings = c.execute(
            text("SELECT version, data FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        old_conn = c.execute(
            select(Connection.id, Connection.last_verified_at).where(
                Connection.exchange == "okx", Connection.env == ENV
            )
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
                "d": json.dumps({"autonomy_level": 2, "entry_timeout_minutes": 30}),
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
                    api_secret="x",  # noqa: S106 - never decrypted; the test factory supplies keys
                    passphrase="x",  # noqa: S106
                    last_verified_at=AT,
                    permissions=["read_only", "trade"],
                    position_mode="net_mode",
                    created_at=AT,
                    updated_at=AT,
                )
            )
        else:
            c.execute(update(Connection).where(Connection.id == old_conn.id).values(last_verified_at=AT))
        c.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                base_ccy="LC",
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=D("0.001"),
                ct_mult=D(1),
                lot_sz=D("0.1"),
                min_sz=D("0.1"),
                tick_sz=D("0.01"),
                max_lever=D(50),
                state="live",
                asset_class="crypto",
                created_at=AT,
                updated_at=AT,
            )
        )
    yield {"inst_id": inst_id}
    with engine.begin() as c:
        c.execute(delete(Fill).where(Fill.inst_id == inst_id))
        c.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst_id))
        c.execute(delete(Order).where(Order.inst_id == inst_id))
        c.execute(delete(Proposal).where(Proposal.inst_id == inst_id))
        c.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == inst_id))
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


def sent_entry(engine: Engine, inst_id: str, *, sz: str = "1", ord_type: str = "limit") -> dict[str, Any]:
    """An entry as the Executor leaves it once OKX accepted it: proposal sent, order live, the
    attached stop recorded as algo ``s…`` for the whole size."""
    pid, rid, oid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cl, s_id = derived_id("e", rid), derived_id("s", rid)
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
                order_type=ord_type,
                entry=D(4000),
                stop=D(3960),
                expected_r=D("2.5"),
                take_profits=[{"price": 4100, "pct": 50}],
                leverage_suggestion=3,
                size_suggestion_pct=D(5),
                status="sent",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            RiskCheck.__table__.insert().values(
                id=rid,
                env=ENV,
                proposal_id=pid,
                settings_version=7,
                result="approved",
                checks=[],
                final_size=D(sz),
                final_notional_usd=D(4),
                leverage=3,
                reason="test",
                decision="send_now",
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            Order.__table__.insert().values(
                id=oid,
                env=ENV,
                proposal_id=pid,
                risk_check_id=rid,
                cl_ord_id=cl,
                ord_id="900001",
                inst_id=inst_id,
                side="buy",
                pos_side="net",
                ord_type=ord_type,
                td_mode="isolated",
                px=D(4000),
                sz=D(sz),
                lever=3,
                state="live",
                reduce_only=False,
                origin="atlas",
                purpose="entry",
                raw={},
                created_at=AT,
                updated_at=AT,
            )
        )
        c.execute(
            AlgoOrder.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                order_id=oid,
                algo_cl_ord_id=s_id,
                algo_id="77001",
                inst_id=inst_id,
                kind="oco",
                side="sell",
                pos_side="net",
                sl_trigger_px=D(3960),
                tp_trigger_px=D(4100),
                sz=D(sz),
                state="live",
                reduce_only=True,
                origin="atlas",
                raw={"placed_as": "attached"},
                created_at=AT,
                updated_at=AT,
            )
        )
    return {"proposal_id": pid, "risk_check_id": rid, "order_id": oid, "cl": cl, "s_id": s_id, "sz": sz}


# --- a stateful OKX ---------------------------------------------------------------------------------


def ok(rows: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})


class FakeOkx:
    """Pending algo orders and one entry order, changed by the requests the lifecycle sends."""

    def __init__(self, router: respx.MockRouter, inst_id: str, entry: dict[str, Any]) -> None:
        self.router = router
        self.inst_id = inst_id
        self.entry = entry
        self.algos: dict[str, dict[str, Any]] = {}
        self.order_state = "live"
        self.acc_fill = "0"
        self.next_algo = 88000
        router.get("/api/v5/public/time").mock(
            return_value=httpx.Response(200, json={"code": "0", "msg": "", "data": [{"ts": str(NOW_MS)}]})
        )
        router.get("/api/v5/trade/orders-algo-pending").mock(side_effect=self._pending)
        router.post("/api/v5/trade/amend-algos").mock(side_effect=self._amend)
        router.post("/api/v5/trade/cancel-algos").mock(side_effect=self._cancel_algos)
        router.post("/api/v5/trade/order-algo").mock(side_effect=self._place_algo)
        router.post("/api/v5/trade/cancel-order").mock(side_effect=self._cancel_order)
        router.get("/api/v5/trade/order").mock(side_effect=self._get_order)

    def add_algo(self, algo_cl: str, sz: str, algo_id: str = "77001") -> None:
        self.algos[algo_id] = {
            "algoId": algo_id,
            "algoClOrdId": algo_cl,
            "instType": "SWAP",
            "instId": self.inst_id,
            "ordType": "oco",
            "side": "sell",
            "posSide": "net",
            "tdMode": "isolated",
            "sz": sz,
            "state": "live",
            "slTriggerPx": "3960",
            "slOrdPx": "-1",
            "tpTriggerPx": "4100",
            "tpOrdPx": "-1",
            "ordId": "",
            "cTime": str(NOW_MS),
            "uTime": str(NOW_MS),
        }

    def live_stops(self) -> list[dict[str, Any]]:
        return [a for a in self.algos.values() if a["state"] == "live" and a["slTriggerPx"]]

    def _pending(self, request: httpx.Request) -> httpx.Response:
        return ok(self.live_stops())

    def _amend(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        algo = self.algos[body["algoId"]]
        algo["sz"] = body["newSz"]
        return ok([{"algoId": algo["algoId"], "algoClOrdId": algo["algoClOrdId"], "sCode": "0", "sMsg": ""}])

    def _cancel_algos(self, request: httpx.Request) -> httpx.Response:
        rows = []
        for item in json.loads(request.content):
            self.algos[item["algoId"]]["state"] = "canceled"
            rows.append({"algoId": item["algoId"], "algoClOrdId": "", "sCode": "0", "sMsg": ""})
        return ok(rows)

    def _place_algo(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.next_algo += 1
        self.add_algo(body["algoClOrdId"], body["sz"], str(self.next_algo))
        return ok(
            [{"algoId": str(self.next_algo), "algoClOrdId": body["algoClOrdId"], "sCode": "0", "sMsg": ""}]
        )

    def _cancel_order(self, request: httpx.Request) -> httpx.Response:
        self.order_state = "canceled"
        return ok([{"clOrdId": self.entry["cl"], "ordId": "900001", "sCode": "0", "sMsg": ""}])

    def _get_order(self, request: httpx.Request) -> httpx.Response:
        return ok([self.order_row()])

    def order_row(self, **extra: Any) -> dict[str, Any]:
        return {
            "instType": "SWAP",
            "instId": self.inst_id,
            "ordId": "900001",
            "clOrdId": self.entry["cl"],
            "px": "4000",
            "sz": self.entry["sz"],
            "ordType": "limit",
            "side": "buy",
            "posSide": "net",
            "tdMode": "isolated",
            "accFillSz": self.acc_fill,
            "avgPx": "4000" if self.acc_fill != "0" else "",
            "state": self.order_state,
            "lever": "3",
            "fee": "-0.01",
            "feeCcy": "USDT",
            "attachAlgoOrds": [],
            "cTime": str(NOW_MS),
            "uTime": str(NOW_MS + 1),
            **extra,
        }

    def calls(self, method: str, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(c.request.content) if method == "POST" else dict(c.request.url.params)
            for c in self.router.calls
            if c.request.method == method and c.request.url.path == f"/api/v5/{path}"
        ]


def push(okx: FakeOkx, *, acc: str, fill: str, trade_id: str, state: str, u_ms: int) -> dict[str, Any]:
    """One OKX ``orders`` push carrying a fill (fillSz/tradeId), as the private socket delivers it."""
    okx.acc_fill = acc
    okx.order_state = state
    return okx.order_row(
        fillSz=fill,
        fillPx="4000",
        tradeId=trade_id,
        fillTime=str(u_ms),
        fillFee="-0.002",
        fillFeeCcy="USDT",
        execType="M",
        uTime=str(u_ms),
    )


class Clock:
    def __init__(self) -> None:
        self.t = AT + timedelta(minutes=1)

    def __call__(self) -> datetime:
        return self.t


def factory(creds: Any, **kw: Any) -> OkxClient:
    return OkxClient(ENV, CREDS, wall_clock_ms=lambda: NOW_MS, **kw)


@pytest.fixture
def harness(engine: Engine, rds: redis.Redis, world: dict[str, Any]) -> Iterator[dict[str, Any]]:
    entry = sent_entry(engine, world["inst_id"])
    clock = Clock()
    lifecycle = EntryLifecycle(engine, ENV, redis_client=rds, client_factory=factory, now=clock)
    feed = PrivateFeed(ENV, engine, rds, credentials=lambda: None)
    feed.on_order(lifecycle.on_order)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        okx = FakeOkx(router, world["inst_id"], entry)
        okx.add_algo(entry["s_id"], entry["sz"])
        yield {"entry": entry, "lifecycle": lifecycle, "feed": feed, "okx": okx, "clock": clock, **world}


def envelopes(rds: redis.Redis, stream: str) -> list[EventEnvelope]:
    return [EventEnvelope.model_validate_json(f[bus.FIELD]) for _id, f in rds.xrange(stream)]


def proposal_status(engine: Engine, pid: uuid.UUID) -> str:
    with engine.connect() as c:
        return str(c.scalar(select(Proposal.status).where(Proposal.id == pid)))


def stored_algo(engine: Engine, algo_cl: str) -> AlgoOrder:
    with engine.connect() as c:
        row = c.execute(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == algo_cl)).one()
        return row  # type: ignore[no-any-return]


async def deliver(h: dict[str, Any], row: dict[str, Any]) -> None:
    await h["feed"].on_push("orders", [row], h["clock"]())


# --- AC1: 0.4 then 0.6 → one SL, 0.4 then 1.0 ---------------------------------------------------------


async def test_partial_fills_resize_the_one_stop_to_the_filled_size(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx, entry = harness["okx"], harness["entry"]

    await deliver(
        harness, push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    )
    assert [b["newSz"] for b in okx.calls("POST", "trade/amend-algos")] == ["0.4"]
    assert [a["sz"] for a in okx.live_stops()] == ["0.4"]
    assert D(stored_algo(engine, entry["s_id"]).sz) == D("0.4")
    assert proposal_status(engine, entry["proposal_id"]) == "partially_filled"

    await deliver(harness, push(okx, acc="1", fill="0.6", trade_id="t2", state="filled", u_ms=NOW_MS + 20))
    amends = okx.calls("POST", "trade/amend-algos")
    assert [b["newSz"] for b in amends] == ["0.4", "1"]
    # Only the size moves: no stop or take-profit price is ever sent with the amend.
    assert all(set(b) == {"instId", "algoId", "newSz"} for b in amends)
    stops = okx.live_stops()
    assert len(stops) == 1 and D(stops[0]["sz"]) == D(1) and stops[0]["slTriggerPx"] == "3960"
    assert okx.calls("POST", "trade/order-algo") == []
    assert D(stored_algo(engine, entry["s_id"]).sz) == D(1)
    assert proposal_status(engine, entry["proposal_id"]) == "filled"

    resized = [e.payload for e in envelopes(rds, "atlas.orders") if e.type == "algo_order.resized"]
    assert [(p["from_sz"], p["to_sz"]) for p in resized] == [("1", "0.4"), ("0.4", "1")]
    statuses = [
        (e.payload["from_status"], e.payload["to_status"])
        for e in envelopes(rds, "atlas.proposals")
        if e.type == "proposal.status_changed"
    ]
    assert statuses == [("sent", "partially_filled"), ("partially_filled", "filled")]


# --- AC2: a replayed fill sends nothing to OKX ---------------------------------------------------------


async def test_replaying_a_fill_sends_no_amend_and_posts_nothing(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx = harness["okx"]
    first = push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    await deliver(harness, first)
    assert len(okx.calls("POST", "trade/amend-algos")) == 1
    fills_before = [e for e in envelopes(rds, "atlas.warroom") if e.payload["kind"] == "fill"]

    await deliver(harness, dict(first))

    assert len(okx.calls("POST", "trade/amend-algos")) == 1
    assert okx.calls("POST", "trade/order-algo") == []
    assert okx.calls("POST", "trade/cancel-algos") == []
    assert [a["sz"] for a in okx.live_stops()] == ["0.4"]
    fills_after = [e for e in envelopes(rds, "atlas.warroom") if e.payload["kind"] == "fill"]
    assert len(fills_after) == len(fills_before) == 1


async def test_a_restart_mid_resize_finishes_it_without_a_second_stop(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    """The fill was stored but the process died before the amend: the sweep finds the stored stop out
    of step, amends it once, and a second sweep does nothing."""
    from trading_core.execution import order_store

    okx, entry, lifecycle = harness["okx"], harness["entry"], harness["lifecycle"]
    row = push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    order_store.upsert_order(engine, ENV, row, harness["clock"]())  # stored, never settled

    out = await lifecycle.sweep()
    assert out["settled"] == [str(entry["order_id"])]
    assert [b["newSz"] for b in okx.calls("POST", "trade/amend-algos")] == ["0.4"]

    assert await lifecycle.sweep() == {"expired": [], "settled": []}
    assert len(okx.calls("POST", "trade/amend-algos")) == 1
    assert len(okx.live_stops()) == 1 and okx.calls("POST", "trade/order-algo") == []


async def test_no_stop_on_okx_places_one_and_a_duplicate_is_cancelled(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx, entry = harness["okx"], harness["entry"]
    okx.algos.clear()  # OKX shows no protective algo for the fill yet

    await deliver(
        harness, push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    )
    p_id = derived_id("p", entry["risk_check_id"])
    [placed] = okx.calls("POST", "trade/order-algo")
    assert placed["algoClOrdId"] == p_id and placed["sz"] == "0.4"
    assert placed["slTriggerPx"] == "3960" and placed["tpTriggerPx"] == "4100" and placed["reduceOnly"]

    # Then OKX materialises the attached stop as well: keep ``s``, size it, cancel ``p``.
    okx.add_algo(entry["s_id"], entry["sz"])
    await deliver(harness, push(okx, acc="1", fill="0.6", trade_id="t2", state="filled", u_ms=NOW_MS + 20))
    stops = okx.live_stops()
    assert [(s["algoClOrdId"], s["sz"]) for s in stops] == [(entry["s_id"], "1")]
    assert stored_algo(engine, p_id).state == "canceled"


# --- AC3/AC4: the entry timeout ----------------------------------------------------------------------


async def test_a_partly_filled_limit_entry_is_cancelled_at_the_timeout_and_keeps_its_stop(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx, entry, lifecycle, clock = harness["okx"], harness["entry"], harness["lifecycle"], harness["clock"]
    await deliver(
        harness, push(okx, acc="0.3", fill="0.3", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    )
    assert [a["sz"] for a in okx.live_stops()] == ["0.3"]

    clock.t = AT + timedelta(minutes=29, seconds=59)
    assert (await lifecycle.sweep())["expired"] == []
    assert okx.calls("POST", "trade/cancel-order") == []

    clock.t = AT + timedelta(minutes=30)
    out = await lifecycle.sweep()

    assert out["expired"] == [str(entry["order_id"])]
    assert okx.calls("POST", "trade/cancel-order") == [{"instId": harness["inst_id"], "clOrdId": entry["cl"]}]
    stops = okx.live_stops()
    assert len(stops) == 1 and stops[0]["sz"] == "0.3"
    assert len(okx.calls("POST", "trade/amend-algos")) == 1  # only the fill's, none at the cancel
    assert D(stored_algo(engine, entry["s_id"]).sz) == D("0.3")
    assert proposal_status(engine, entry["proposal_id"]) == "partially_filled"
    with engine.connect() as c:
        state, filled = c.execute(
            select(Order.state, Order.filled_sz).where(Order.id == entry["order_id"])
        ).one()
    assert (state, D(filled)) == ("canceled", D("0.3"))
    [expired] = [e.payload for e in envelopes(rds, "atlas.orders") if e.type == "order.expired"]
    assert expired["filled_sz"] == "0.3" and expired["timeout_minutes"] == 30


async def test_an_unfilled_limit_entry_expires_at_the_timeout(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx, entry, lifecycle, clock = harness["okx"], harness["entry"], harness["lifecycle"], harness["clock"]
    okx.algos.clear()  # attached TP/SL only exists on the order until something fills
    clock.t = AT + timedelta(minutes=31)

    out = await lifecycle.sweep()

    assert out["expired"] == [str(entry["order_id"])]
    assert len(okx.calls("POST", "trade/cancel-order")) == 1
    assert okx.calls("POST", "trade/order-algo") == [] and okx.calls("POST", "trade/amend-algos") == []
    assert proposal_status(engine, entry["proposal_id"]) == "expired"
    assert stored_algo(engine, entry["s_id"]).state == "canceled"
    changes = [
        (e.payload["from_state"], e.payload["to_state"])
        for e in envelopes(rds, "atlas.orders")
        if e.type == "order.state_changed"
    ]
    assert ("live", "canceled") in changes
    # A second sweep finds nothing left to do.
    assert await lifecycle.sweep() == {"expired": [], "settled": []}


async def test_market_entries_never_time_out(engine: Engine, rds: redis.Redis, world: dict[str, Any]) -> None:
    entry = sent_entry(engine, world["inst_id"], ord_type="market")
    lifecycle = EntryLifecycle(
        engine, ENV, redis_client=rds, client_factory=factory, now=lambda: AT + timedelta(days=1)
    )
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        assert await lifecycle.sweep() == {"expired": [], "settled": []}
        assert router.calls.call_count == 0
    assert proposal_status(engine, entry["proposal_id"]) == "sent"


# --- AC5: one War Room fill message per fill -----------------------------------------------------------


async def test_each_fill_posts_one_fill_message(
    engine: Engine, rds: redis.Redis, harness: dict[str, Any]
) -> None:
    okx, entry = harness["okx"], harness["entry"]
    await deliver(
        harness, push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10)
    )
    await deliver(harness, push(okx, acc="1", fill="0.6", trade_id="t2", state="filled", u_ms=NOW_MS + 20))

    fills = [e for e in envelopes(rds, "atlas.warroom") if e.payload["kind"] == "fill"]
    assert len(fills) == 2
    cards = [f.payload["card"] for f in fills]
    assert [(c["inst_id"], c["side"], c["size"], c["price"], c["fee"]) for c in cards] == [
        (harness["inst_id"], "buy", "0.4", "4000", "-0.002"),
        (harness["inst_id"], "buy", "0.6", "4000", "-0.002"),
    ]
    assert all(c["order_id"] == str(entry["order_id"]) for c in cards)
    assert all(c["proposal_id"] == str(entry["proposal_id"]) for c in cards)
    assert all(f.payload["author"] == "executor" and f.payload["author_type"] == "system" for f in fills)
    assert "0.4" in fills[0].payload["content_md"] and harness["inst_id"] in fills[0].payload["content_md"]


async def test_trading_state_is_untouched(engine: Engine, harness: dict[str, Any]) -> None:
    """Fill handling protects positions even while trading is paused; it never opens anything."""
    with engine.begin() as c:
        c.execute(delete(TradingState).where(TradingState.env == ENV))
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
    try:
        okx = harness["okx"]
        await deliver(
            harness,
            push(okx, acc="0.4", fill="0.4", trade_id="t1", state="partially_filled", u_ms=NOW_MS + 10),
        )
        assert [a["sz"] for a in okx.live_stops()] == ["0.4"]
        assert okx.calls("POST", "trade/order") == []
    finally:
        with engine.begin() as c:
            c.execute(delete(TradingState).where(TradingState.env == ENV))
