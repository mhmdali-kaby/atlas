"""Reconciliation (brief §4.4, §1 rules 3 and 8): make the database match OKX, protect every position.

Runs at startup (the Executor refuses ``/execute`` until one run has succeeded, see :class:`ReconcileGate`),
every ``reconciliation.interval_seconds`` and after every private WebSocket re-login
(``ws.private.reconnected``). One run, in this order:

1. **Balances and positions**: the account sync's own reads and writer (``sync.account``), so the
   Portfolio snapshot is the one OKX reports now.
2. **Pending orders** (``trade/orders-pending``) and **pending algo orders**
   (``trade/orders-algo-pending``, conditional/oco and trigger) go through ``execution.order_store`` —
   the same upserts the private WebSocket uses. An order the DB does not know is imported with
   origin ``external``.
3. **DB orders OKX no longer lists as pending** (live / partially_filled, and new / sending that have an
   ordId or clOrdId) are re-read one by one with ``GET trade/order`` and take OKX's final state. An
   Atlas entry left in ``sending`` by a crash is matched by its clOrdId, so the Executor's next call
   sees it as already executed and sends nothing (rule 8). Algo rows the same with
   ``GET trade/order-algo``.
4. **Fills** of the last ``fills_lookback_days`` (``trade/fills-history`` per instType, paged): missing
   ones are inserted with the history sync's writer; a fill whose order the DB does not know first
   imports that order (``external``).
5. **Positions**: each open position's size is compared with the DB fills since the position opened
   (``cTime``). A difference that remains after steps 2-4 trips the ``reconciliation_mismatch`` circuit
   breaker. Each position with no live server-side stop (a pending conditional/oco algo with an
   ``slTriggerPx`` on the closing side) gets one: at the stop of the Atlas proposal whose entry opened
   it, else ``emergency_stop_atr_mult`` x ATR(1H) from the mark, never further than ``max_stop_pct``.
   A ``system.alert`` (code ``position_unprotected``) and a War Room ``system_alert`` follow.
6. ``sync_status`` (stream ``reconcile``) gets ``reconciled_at`` — the Portfolio's "Reconciled with
   OKX" indicator reads the newest one.

Nothing here withdraws or transfers (rule 5); the only OKX write is the protective stop.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import AlgoOrder, Connection, Fill, Instrument, Order, Proposal, SyncStatus
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trading_core.execution import order_store
from trading_core.execution.config import ORDER_NOT_FOUND, ORDERS_STREAM, SYSTEM_STREAM, WARROOM_STREAM
from trading_core.execution.config import round_stop as _round_stop
from trading_core.okx import OkxClient, client_for_connection
from trading_core.okx.errors import OkxClientError
from trading_core.okx.models import Position
from trading_core.okx.trade import AlgoOrderRequest, OkxTradeClient, OkxTradeError
from trading_core.okx.trade_models import AlgoOrderDetail, FillDetail
from trading_core.risk.breakers import Breakers
from trading_core.risk.context import _atr_1h
from trading_core.risk.rules import rules_config
from trading_core.sync import account as account_sync
from trading_core.sync import history as history_sync

log = logging.getLogger("trading_core.execution.reconcile")

JOB = "reconcile"
STREAM = "reconcile"  # sync_status.stream
SOURCE = "service:trading_core.reconcile"
WARROOM_SOURCE = "system:reconcile"
WARROOM_AUTHOR = "reconcile"
BREAKER = "reconciliation_mismatch"
ALERT_CODE = "position_unprotected"
NO_CONNECTION = "no_verified_connection"

OPEN_ORDER_STATES = ("live", "partially_filled")
UNSENT_ORDER_STATES = ("new", "sending")
OPEN_ALGO_STATES = ("live", "partially_effective", "pause")
PROTECTING_ALGO_STATES = frozenset({"live", "partially_effective"})
ZERO = Decimal(0)

_CFG: Mapping[str, Any] = DEFAULTS["reconciliation"]

ClientFactory = Callable[[Connection, str], OkxClient]


# -- the startup gate ---------------------------------------------------------------------------------


class ReconcileGate:
    """Whether the Executor may accept ``/execute`` (rule 8: a restart reconciles before any action).

    Closed at startup when the app runs the reconcile job; opened by the first run that succeeded (or
    found no verified connection, where the Executor refuses anyway). It never closes again: later runs
    that fail are reported in ``sync_status`` and retried on the next tick.
    """

    def __init__(self, ready: bool = False) -> None:
        self._ready = ready
        self.opened_at: datetime | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    def open(self, at: datetime | None = None) -> None:
        if not self._ready:
            self._ready = True
            self.opened_at = at or datetime.now(UTC)


# -- result ----------------------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    env: str
    at: datetime
    orders_seen: int = 0
    orders_imported: list[str] = field(default_factory=list)  # ordIds
    orders_closed: list[str] = field(default_factory=list)  # ordIds whose final state came from OKX
    algos_seen: int = 0
    algos_imported: list[str] = field(default_factory=list)
    fills_new: int = 0
    positions: int = 0
    protected: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    breaker_tripped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "at": order_store.iso(self.at),
            "orders_seen": self.orders_seen,
            "orders_imported": list(self.orders_imported),
            "orders_closed": list(self.orders_closed),
            "algos_seen": self.algos_seen,
            "algos_imported": list(self.algos_imported),
            "fills_new": self.fills_new,
            "positions": self.positions,
            "protected": list(self.protected),
            "mismatches": list(self.mismatches),
            "breaker_tripped": self.breaker_tripped,
        }


# -- events ----------------------------------------------------------------------------------------


class _Bus:
    def __init__(self, env: str, rds: redis.Redis | None) -> None:
        self.env = env
        self.rds = rds

    def publish(self, stream: str, type_: str, payload: dict[str, Any], source: str = SOURCE) -> None:
        if self.rds is None:
            return
        envelope = EventEnvelope.model_validate(
            {"type": type_, "env": self.env, "source": source, "correlation_id": None, "payload": payload}
        )
        try:
            bus.publish(stream, envelope, client=self.rds)
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": type_})

    def order(self, u: order_store.OrderUpsert) -> None:
        payload = order_store.order_payload(u)
        if u.state_changed:
            self.publish(
                ORDERS_STREAM,
                "order.state_changed",
                {**payload, "from_state": u.prev_state, "to_state": u.state},
            )
        fill = order_store.fill_payload(u)
        if fill is not None:
            self.publish("atlas.account", "account.fill", fill)

    def algo(self, u: order_store.AlgoUpsert) -> None:
        if u.state_changed:
            self.publish(
                ORDERS_STREAM,
                "algo_order.state_changed",
                {**order_store.algo_payload(u), "from_state": u.prev_state, "to_state": u.state},
            )

    def alert(self, detail: str, card: dict[str, Any]) -> None:
        self.publish(SYSTEM_STREAM, "system.alert", {"code": ALERT_CODE, "detail": detail, **card})
        self.publish(
            WARROOM_STREAM,
            "warroom.message",
            {
                "thread": {"kind": "main", "key": "main"},
                "author_type": "system",
                "author": WARROOM_AUTHOR,
                "kind": "system_alert",
                "content_md": detail,
                "card": {"code": ALERT_CODE, **card},
                "reply_to": None,
                "mentions": [],
                "reasoning_md": None,
                "tool_calls": None,
                "tokens_in": None,
                "tokens_out": None,
                "cost_usd": None,
            },
            source=WARROOM_SOURCE,
        )


# -- OKX reads -------------------------------------------------------------------------------------


async def _paged(
    trade: OkxTradeClient, path: str, params: Mapping[str, str | None], cursor: str
) -> list[dict[str, Any]]:
    """Newest-first paging with the ``after`` cursor until a short page or ``max_pages``."""
    limit = int(DEFAULTS["okx_rest"]["page_limits"][path])
    rows: list[dict[str, Any]] = []
    after: str | None = None
    for _ in range(int(_CFG["max_pages"])):
        page = await trade.get_rows(path, {**params, "after": after, "limit": str(limit)})
        rows.extend(page)
        if len(page) < limit:
            return rows
        nxt = order_store.blank(page[-1].get(cursor))
        if nxt is None or nxt == after:
            return rows
        after = nxt
    log.warning("paging stopped at max_pages", extra={"path": path})
    return rows


async def _one(trade: OkxTradeClient, path: str, params: Mapping[str, str | None]) -> dict[str, Any] | None:
    """One row from a by-id GET, or None when OKX does not know it."""
    try:
        rows = await trade.get_rows(path, params)
    except OkxTradeError as err:
        if err.s_code == ORDER_NOT_FOUND:
            return None
        raise
    return rows[0] if rows else None


# -- steps -----------------------------------------------------------------------------------------


async def _orders(
    trade: OkxTradeClient, engine: Engine, env: str, now: datetime, out: ReconcileResult, ev: _Bus
) -> None:
    pending = await _paged(trade, "trade/orders-pending", {}, "ordId")
    seen_ord: set[str] = set()
    seen_cl: set[str] = set()
    for row in pending:
        u = await asyncio.to_thread(order_store.upsert_order, engine, env, row, now)
        if u is None:
            continue
        out.orders_seen += 1
        seen_ord.add(u.detail.ord_id)
        if u.detail.cl_ord_id:
            seen_cl.add(u.detail.cl_ord_id)
        if u.prev_state is None and u.origin == order_store.EXTERNAL:
            out.orders_imported.append(u.detail.ord_id)
        ev.order(u)

    def stale() -> list[tuple[str, str | None, str | None, str]]:
        with Session(engine) as s:
            rows = s.execute(
                select(Order.inst_id, Order.ord_id, Order.cl_ord_id, Order.state).where(
                    Order.env == env, Order.state.in_((*OPEN_ORDER_STATES, *UNSENT_ORDER_STATES))
                )
            ).all()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    for inst_id, ord_id, cl, state in await asyncio.to_thread(stale):
        if (ord_id and ord_id in seen_ord) or (cl and cl in seen_cl):
            continue
        if not ord_id and not cl:
            continue
        found = await _one(
            trade, "trade/order", {"instId": inst_id, **({"ordId": ord_id} if ord_id else {"clOrdId": cl})}
        )
        if found is None:
            # Never reached OKX (an unsent entry stays resumable by the Executor), or OKX no longer has it.
            if state in OPEN_ORDER_STATES:
                log.warning("open order unknown to OKX", extra={"ord_id": ord_id, "inst_id": inst_id})
            continue
        u = await asyncio.to_thread(order_store.upsert_order, engine, env, found, now)
        if u is None:
            continue
        if u.state not in OPEN_ORDER_STATES:
            out.orders_closed.append(u.detail.ord_id)
        ev.order(u)


async def _algos(
    trade: OkxTradeClient, engine: Engine, env: str, now: datetime, out: ReconcileResult, ev: _Bus
) -> list[AlgoOrderDetail]:
    pending_rows: list[dict[str, Any]] = []
    for ord_type in ("conditional,oco", "trigger"):
        pending_rows += await _paged(trade, "trade/orders-algo-pending", {"ordType": ord_type}, "algoId")
    pending: list[AlgoOrderDetail] = []
    seen_ids: set[str] = set()
    seen_cl: set[str] = set()
    for row in pending_rows:
        u = await asyncio.to_thread(order_store.upsert_algo, engine, env, row, now)
        pending.append(u.detail)
        out.algos_seen += 1
        seen_ids.add(u.detail.algo_id)
        if u.detail.algo_cl_ord_id:
            seen_cl.add(u.detail.algo_cl_ord_id)
        if u.algo_order_id is not None and u.prev_state is None and u.origin == order_store.EXTERNAL:
            out.algos_imported.append(u.detail.algo_id)
        ev.algo(u)

    def stale() -> list[tuple[str | None, str | None]]:
        with Session(engine) as s:
            rows = s.execute(
                select(AlgoOrder.algo_id, AlgoOrder.algo_cl_ord_id).where(
                    AlgoOrder.env == env, AlgoOrder.state.in_(OPEN_ALGO_STATES)
                )
            ).all()
        return [(r[0], r[1]) for r in rows]

    for algo_id, cl in await asyncio.to_thread(stale):
        if (algo_id and algo_id in seen_ids) or (cl and cl in seen_cl) or not (algo_id or cl):
            continue
        found = await _one(trade, "trade/order-algo", {"algoId": algo_id} if algo_id else {"algoClOrdId": cl})
        if found is None:
            log.warning("open algo order unknown to OKX", extra={"algo_id": algo_id})
            continue
        ev.algo(await asyncio.to_thread(order_store.upsert_algo, engine, env, found, now))
    return pending


async def _fills(
    trade: OkxTradeClient, engine: Engine, env: str, now: datetime, out: ReconcileResult, ev: _Bus
) -> None:
    since = now - timedelta(days=int(_CFG["fills_lookback_days"]))
    limit = history_sync.PAGE_LIMIT
    fills: list[FillDetail] = []
    for inst_type in DEFAULTS["sync"]["history_fill_inst_types"]:

        async def page(after: str | None, it: str = str(inst_type)) -> list[FillDetail]:
            return await trade.get_fills_history(it, after=after, limit=limit, begin=since)

        fills += await history_sync.paginate_until(
            page, lambda f: f.bill_id or "", lambda f: f.ts, since, limit
        )
    unique = {(f.inst_id, f.trade_id): f for f in fills if f.ts is None or f.ts >= since}
    if not unique:
        return

    def known() -> dict[str, uuid.UUID]:
        with engine.connect() as conn:
            return history_sync._order_ids(conn, env, {f.ord_id for f in unique.values()})

    ids = await asyncio.to_thread(known)
    for f in {f.ord_id: f for f in unique.values() if f.ord_id not in ids}.values():
        # A fill of an order the DB never saw (placed outside Atlas and already finished): import it.
        row = await _one(trade, "trade/order", {"instId": f.inst_id, "ordId": f.ord_id})
        if row is None:
            continue
        u = await asyncio.to_thread(order_store.upsert_order, engine, env, row, now)
        if u is not None:
            if u.prev_state is None and u.origin == order_store.EXTERNAL:
                out.orders_imported.append(u.detail.ord_id)
            ev.order(u)

    def write() -> list[dict[str, Any]]:
        with engine.begin() as conn:
            order_ids = history_sync._order_ids(conn, env, {f.ord_id for f in unique.values()})
            rows = [
                r for f in unique.values() if (r := history_sync.fill_row(env, f, order_ids, now)) is not None
            ]
            existing = {
                (r[0], r[1])
                for r in conn.execute(
                    select(Fill.inst_id, Fill.trade_id).where(
                        Fill.env == env, Fill.trade_id.in_([r["trade_id"] for r in rows])
                    )
                ).all()
            }
            history_sync._upsert_fills(conn, rows, now)
            return [r for r in rows if (r["inst_id"], r["trade_id"]) not in existing]

    new_rows = await asyncio.to_thread(write)
    out.fills_new = len(new_rows)
    for r in new_rows:
        ev.publish(
            "atlas.account",
            "account.fill",
            {
                "fill_id": str(r["id"]),
                "order_id": str(r["order_id"]) if r["order_id"] else None,
                "trade_id": r["trade_id"],
                "ord_id": r["ord_id"],
                "cl_ord_id": r["cl_ord_id"],
                "inst_id": r["inst_id"],
                "side": r["side"],
                "pos_side": r["pos_side"],
                "fill_px": order_store.s(r["fill_px"]),
                "fill_sz": order_store.s(r["fill_sz"]),
                "fee": order_store.s(r["fee"]),
                "fee_ccy": r["fee_ccy"],
                "exec_type": r["exec_type"],
                "fill_pnl": order_store.s(r["fill_pnl"]),
                "ts": order_store.iso(r["ts"]),
            },
        )


# -- positions -------------------------------------------------------------------------------------


def position_direction(p: Position) -> str | None:
    """long | short | None (flat)."""
    if p.pos is None or p.pos == 0:
        return None
    if p.pos_side == "long":
        return "long"
    if p.pos_side == "short":
        return "short"
    return "long" if p.pos > 0 else "short"


def _signed_fill(side: str, pos_side: str | None, sz: Decimal) -> Decimal:
    """A fill's change to the position size as OKX reports ``pos`` (net: signed; long/short: >= 0)."""
    if pos_side == "short":
        return sz if side == "sell" else -sz
    return sz if side == "buy" else -sz


def fills_size(engine: Engine, env: str, p: Position) -> Decimal:
    """The position size the DB fills explain: every fill on the instrument (and posSide in long/short
    mode) since the position opened."""
    assert p.c_time is not None
    since = p.c_time - timedelta(seconds=float(_CFG["fill_ts_slack_seconds"]))
    stmt = select(Fill.side, Fill.pos_side, Fill.fill_sz).where(
        Fill.env == env, Fill.inst_id == p.inst_id, Fill.ts >= since
    )
    if p.pos_side in ("long", "short"):
        stmt = stmt.where(Fill.pos_side == p.pos_side)
    with Session(engine) as s:
        rows = s.execute(stmt).all()
    return sum((_signed_fill(r[0], r[1], r[2]) for r in rows), ZERO)


def _plain(value: Decimal) -> str:
    """``Decimal`` as a plain string without trailing zeros (``4.000`` → ``4``)."""
    return format(value.normalize(), "f")


def _mismatch(engine: Engine, env: str, p: Position, now: datetime) -> dict[str, Any] | None:
    if p.c_time is None or p.pos is None:
        return None
    if p.c_time < now - timedelta(days=int(_CFG["fills_lookback_days"])):
        # Opened before the fills window this run re-reads: not provable from OKX's data, not a mismatch.
        return None
    db = fills_size(engine, env, p)
    if db == p.pos:
        return None
    return {
        "inst_id": p.inst_id,
        "pos_id": p.pos_id,
        "pos_side": p.pos_side,
        "okx_pos": _plain(p.pos),
        "db_pos": _plain(db),
        "opened_at": order_store.iso(p.c_time),
    }


def _protected(p: Position, close_side: str, algos: Sequence[AlgoOrderDetail]) -> bool:
    for a in algos:
        if a.inst_id != p.inst_id or a.sl_trigger_px is None or a.state not in PROTECTING_ALGO_STATES:
            continue
        if a.ord_type not in ("conditional", "oco"):
            continue
        if a.side is not None and a.side != close_side:
            continue
        if p.pos_side in ("long", "short") and a.pos_side not in (None, p.pos_side):
            continue
        return True
    return False


def _executor_busy(engine: Engine, env: str, inst_id: str, now: datetime) -> bool:
    """The Executor is still placing or verifying an order on this instrument; its own 3 s stop check
    owns the protection until then."""
    grace = now - timedelta(seconds=float(_CFG["protect_grace_seconds"]))
    with Session(engine) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.env == env,
                    Order.inst_id == inst_id,
                    Order.origin == "atlas",
                    (Order.state.in_(UNSENT_ORDER_STATES)) | (Order.created_at >= grace),
                )
            )
            or 0
        ) > 0


@dataclass(frozen=True)
class _StopPlan:
    stop: Decimal
    source: str  # proposal | emergency_atr | emergency_max_stop_pct
    proposal_id: uuid.UUID | None
    atr_1h: Decimal | None
    note: str | None


def _opening_proposal(s: Session, env: str, p: Position, open_side: str) -> Proposal | None:
    """The Atlas proposal whose entry filled into this position: an Atlas entry with a fill on the
    opening side since the position opened (newest first)."""
    if p.c_time is None:
        return None
    since = p.c_time - timedelta(seconds=float(_CFG["fill_ts_slack_seconds"]))
    row = s.execute(
        select(Order.proposal_id)
        .join(Fill, Fill.order_id == Order.id)
        .where(
            Order.env == env,
            Order.inst_id == p.inst_id,
            Order.origin == "atlas",
            Order.purpose == "entry",
            Order.proposal_id.is_not(None),
            Fill.side == open_side,
            Fill.ts >= since,
        )
        .order_by(Fill.ts.desc())
        .limit(1)
    ).first()
    return None if row is None else s.get(Proposal, row[0])


def _settings(s: Session, env: str) -> Mapping[str, Any]:
    row = s.execute(text("SELECT data FROM settings WHERE env = :env"), {"env": env}).first()
    return row[0] if row is not None and isinstance(row[0], dict) else {}


def _setting_dec(data: Mapping[str, Any], key: str, fallback: Any) -> Decimal:
    raw = data.get(key)
    if isinstance(data.get("executor"), Mapping) and raw is None:
        raw = data["executor"].get(key)
    try:
        value = Decimal(str(fallback if raw is None or isinstance(raw, (bool, float)) else raw))
    except ArithmeticError:
        value = Decimal(str(fallback))
    return value


def plan_stop(
    engine: Engine, env: str, p: Position, direction: str, mark: Decimal, now: datetime
) -> _StopPlan:
    """Where the missing stop goes: the opening proposal's stop when it is still on the right side of the
    mark, else ``emergency_stop_atr_mult`` x ATR(1H) from the mark, capped at ``max_stop_pct`` of it.
    Rounded to tickSz toward the mark (never further away)."""
    open_side = "buy" if direction == "long" else "sell"
    with Session(engine) as s:
        data = _settings(s, env)
        tick = s.scalar(
            select(Instrument.tick_sz).where(Instrument.env == env, Instrument.inst_id == p.inst_id)
        )
        proposal = _opening_proposal(s, env, p, open_side)
        atr = _atr_1h(s, env, p.inst_id, now, rules_config())
    note: str | None = None
    if proposal is not None and proposal.stop is not None and proposal.stop > 0:
        stop = Decimal(proposal.stop)
        if (direction == "long" and stop < mark) or (direction == "short" and stop > mark):
            return _StopPlan(_round_stop(stop, tick, open_side), "proposal", proposal.id, atr, None)
        note = f"The proposal's stop {order_store.s(stop)} is already beyond the mark {order_store.s(mark)}."
    mult = _setting_dec(data, "emergency_stop_atr_mult", DEFAULTS["executor"]["emergency_stop_atr_mult"])
    max_pct = _setting_dec(data, "max_stop_pct", DEFAULTS["risk_engine"]["max_stop_pct"])
    cap = mark * max_pct / Decimal(100)
    dist, source = cap, "emergency_max_stop_pct"
    if atr is not None and mult * atr < cap:
        dist, source = mult * atr, "emergency_atr"
    raw = mark - dist if direction == "long" else mark + dist
    return _StopPlan(_round_stop(raw, tick, open_side), source, proposal.id if proposal else None, atr, note)


def protect_id(env: str, p: Position, n: int) -> str:
    """Deterministic algoClOrdId of the n-th reconciliation stop for one position (rule 8)."""
    seed = f"{env}:{p.inst_id}:{p.pos_id}:{order_store.iso(p.c_time)}:{n}"
    return "r" + hashlib.sha256(seed.encode()).hexdigest()[:31]


def _claim_id(engine: Engine, env: str, p: Position) -> str:
    """The id to place with: a row of ours still new/sending is resumed with its own id; ids already
    used (placed, failed, cancelled, triggered) are skipped."""
    with Session(engine) as s:
        for n in range(1000):
            cand = protect_id(env, p, n)
            state = s.scalar(select(AlgoOrder.state).where(AlgoOrder.algo_cl_ord_id == cand))
            if state is None or state in ("new", "sending"):
                return cand
    raise RuntimeError("no free reconciliation stop id")


def _record_algo(engine: Engine, env: str, cl: str, now: datetime, **values: Any) -> None:
    with engine.begin() as conn:
        exists = conn.execute(select(AlgoOrder.id).where(AlgoOrder.algo_cl_ord_id == cl)).first()
        if exists is None:
            conn.execute(
                insert(AlgoOrder).values(
                    id=uuid.uuid4(),
                    env=env,
                    algo_cl_ord_id=cl,
                    origin="atlas",
                    created_at=now,
                    updated_at=now,
                    **values,
                )
            )
        else:
            conn.execute(
                update(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == cl).values(updated_at=now, **values)
            )


async def _protect(
    trade: OkxTradeClient, engine: Engine, env: str, p: Position, direction: str, now: datetime, ev: _Bus
) -> dict[str, Any] | None:
    mark = p.mark_px or p.last or p.avg_px
    if mark is None or mark <= 0 or p.pos is None:
        log.warning("unprotected position without a mark price", extra={"inst_id": p.inst_id})
        return None
    plan = await asyncio.to_thread(plan_stop, engine, env, p, direction, mark, now)
    close_side = "sell" if direction == "long" else "buy"
    sz = abs(p.pos)
    td_mode = p.mgn_mode if p.mgn_mode in ("isolated", "cross") else "isolated"
    pos_side = p.pos_side if p.pos_side in ("net", "long", "short") else "net"
    cl = await asyncio.to_thread(_claim_id, engine, env, p)
    base = {
        "inst_id": p.inst_id,
        "kind": "sl",
        "side": close_side,
        "pos_side": pos_side,
        "trigger_px": plan.stop,
        "sz": sz,
        "raw": {
            "reason": "reconcile_unprotected",
            "stop_source": plan.source,
            "proposal_id": str(plan.proposal_id) if plan.proposal_id else None,
            "pos_id": p.pos_id,
        },
    }
    await asyncio.to_thread(_record_algo, engine, env, cl, now, state="sending", **base)
    req = AlgoOrderRequest(
        inst_id=p.inst_id,
        td_mode=td_mode,  # type: ignore[arg-type]
        side=close_side,  # type: ignore[arg-type]
        ord_type="conditional",
        algo_cl_ord_id=cl,
        sz=sz,
        pos_side=pos_side,  # type: ignore[arg-type]
        reduce_only=True,
        sl_trigger_px=plan.stop,
    )
    algo_id: str | None = None
    error: str | None = None
    code: str | None = None
    try:
        ack = await trade.place_algo_order(req)
        algo_id = ack.algo_id or None
        await asyncio.to_thread(_record_algo, engine, env, cl, now, state="live", algo_id=algo_id)
    except OkxClientError as err:
        code = err.s_code if isinstance(err, OkxTradeError) else None
        error = err.human_message if isinstance(err, OkxTradeError) else str(err)
        await asyncio.to_thread(
            _record_algo, engine, env, cl, now, state="failed", last_error_code=code, last_error_msg=error
        )
    stop_s = order_store.s(plan.stop)
    where = {
        "proposal": "at the stop of the proposal that opened it",
        "emergency_atr": "at the emergency distance (ATR(1H) x multiplier from the mark)",
        "emergency_max_stop_pct": "at the emergency distance, capped at the maximum stop %",
    }[plan.source]
    if error is None:
        detail = (
            f"{p.inst_id} {direction} {order_store.s(sz)} had no stop-loss on OKX. "
            f"Atlas placed one at {stop_s}, {where}."
        )
    else:
        detail = (
            f"{p.inst_id} {direction} {order_store.s(sz)} has no stop-loss on OKX and placing one at "
            f"{stop_s} failed: {error} Protect or close it now."
        )
    if plan.note:
        detail += f" {plan.note}"
    card = {
        "inst_id": p.inst_id,
        "pos_id": p.pos_id,
        "side": direction,
        "pos_side": pos_side,
        "sz": order_store.s(sz),
        "mark_px": order_store.s(mark),
        "stop": stop_s,
        "stop_source": plan.source,
        "proposal_id": str(plan.proposal_id) if plan.proposal_id else None,
        "atr_1h": order_store.s(plan.atr_1h),
        "algo_cl_ord_id": cl,
        "algo_id": algo_id,
        "placed": error is None,
        "okx_code": code,
        "error": error,
    }
    ev.alert(detail, card)
    ev.publish(
        ORDERS_STREAM,
        "algo_order.state_changed",
        {
            "algo_order_id": None,
            "algo_id": algo_id,
            "algo_cl_ord_id": cl,
            "inst_id": p.inst_id,
            "kind": "sl",
            "side": close_side,
            "sl_trigger_px": stop_s,
            "tp_trigger_px": None,
            "sz": order_store.s(sz),
            "state": "live" if error is None else "failed",
            "origin": "atlas",
            "from_state": "sending",
            "to_state": "live" if error is None else "failed",
        },
    )
    return card


# -- status ----------------------------------------------------------------------------------------


def _stamp(engine: Engine, env: str, now: datetime, error: str | None) -> None:
    values: dict[str, Any] = {
        "env": env,
        "stream": STREAM,
        "last_error": error,
        "created_at": now,
        "updated_at": now,
    }
    update_set: dict[str, Any] = {"last_error": error, "updated_at": now}
    if error is None:
        values.update(last_success_at=now, reconciled_at=now)
        update_set.update(last_success_at=now, reconciled_at=now)
    with engine.begin() as conn:
        stmt = insert(SyncStatus).values(**values)
        conn.execute(
            stmt.on_conflict_do_update(index_elements=[SyncStatus.env, SyncStatus.stream], set_=update_set)
        )


# -- the run ---------------------------------------------------------------------------------------


async def reconcile(
    env: str,
    *,
    okx: OkxClient,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
    breakers: Breakers | None = None,
) -> ReconcileResult:
    """One reconciliation run for ``env`` over an authenticated client. On failure the error is stored
    in ``sync_status`` (stream ``reconcile``) and re-raised; what was already fixed stays fixed."""
    clock = now or (lambda: datetime.now(UTC))
    at = clock()
    out = ReconcileResult(env=env, at=at)
    ev = _Bus(env, redis_client)
    trade = OkxTradeClient(okx)
    try:
        snap = await account_sync.fetch_account(okx)
        await asyncio.to_thread(account_sync.write_snapshot, engine, env, snap, at)
        await _orders(trade, engine, env, at, out, ev)
        algos = await _algos(trade, engine, env, at, out, ev)
        await _fills(trade, engine, env, at, out, ev)

        for p in snap.positions:
            direction = position_direction(p)
            if direction is None:
                continue
            out.positions += 1
            mismatch = await asyncio.to_thread(_mismatch, engine, env, p, at)
            if mismatch is not None:
                out.mismatches.append(mismatch)
            close_side = "sell" if direction == "long" else "buy"
            if _protected(p, close_side, algos):
                continue
            if await asyncio.to_thread(_executor_busy, engine, env, p.inst_id, at):
                continue
            card = await _protect(trade, engine, env, p, direction, at, ev)
            if card is not None:
                out.protected.append(card)

        if out.mismatches:
            b = breakers or Breakers(engine, env, redis_client=redis_client, now=clock)
            key = "|".join(
                sorted(f"{m['inst_id']}:{m['pos_side']}:{m['okx_pos']}:{m['db_pos']}" for m in out.mismatches)
            )
            names = ", ".join(sorted({m["inst_id"] for m in out.mismatches}))
            reason = f"Position size on OKX does not match Atlas's fills after reconciliation: {names}."
            out.breaker_tripped = await asyncio.to_thread(
                b.trip, BREAKER, reason, {"mismatches": out.mismatches}, key=key
            )
        await asyncio.to_thread(_stamp, engine, env, at, None)
    except Exception as exc:
        try:
            await asyncio.to_thread(_stamp, engine, env, at, f"{type(exc).__name__}: {exc}"[:2000])
        except Exception:
            log.exception("could not record reconciliation failure")
        raise
    log.info(
        "reconciled",
        extra={
            "env": env,
            "imported": len(out.orders_imported),
            "fills_new": out.fills_new,
            "protected": len(out.protected),
            "mismatches": len(out.mismatches),
        },
    )
    return out


def _default_client(row: Connection, app_key: str) -> OkxClient:
    return client_for_connection(row, app_key)


def reconcile_job(
    env: str,
    engine: Engine,
    app_key: str,
    gate: ReconcileGate,
    *,
    client_factory: ClientFactory | None = None,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], Any]:
    """The scheduler job. Opens ``gate`` after the first run that succeeded or found no verified
    connection (nothing to reconcile, and the Executor refuses without one)."""
    factory = client_factory or _default_client

    async def run() -> dict[str, Any]:
        row = await asyncio.to_thread(account_sync.verified_connection, engine, env)
        if row is None:
            gate.open()
            return {"env": env, "skipped": True, "reason": NO_CONNECTION}
        if not app_key:
            raise account_sync.AppKeyMissing("APP_KEY is not set; cannot decrypt the connection")
        rds = redis_client
        if rds is None:
            try:
                rds = bus.make_client()
            except redis.RedisError:
                rds = None
        client = factory(row, app_key)
        async with client as okx:
            result = await reconcile(env, okx=okx, engine=engine, redis_client=rds, now=now)
        gate.open()
        return {**result.as_dict(), "skipped": False}

    return run


__all__ = [
    "BREAKER",
    "JOB",
    "ReconcileGate",
    "ReconcileResult",
    "fills_size",
    "plan_stop",
    "position_direction",
    "protect_id",
    "reconcile",
    "reconcile_job",
]
