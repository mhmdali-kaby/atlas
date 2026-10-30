"""Kill switch, pause/resume, one-position close and one-order cancel (brief §10, decision #251).

Every function here is reached through a signed ``/internal`` route (``execution/control_api.py``).

**Kill** (``kill(env, flatten=...)``), in this order:

1. ``trading_state.paused`` is set (reason "Kill switch") and ``system.trading_state`` published,
   before any request to OKX, so nothing new can be sent while the rest runs.
2. Every pending order on OKX is cancelled (``trade/cancel-order``, ten at a time), and every
   pending algo order **without a stop leg** (take-profit-only conditionals and trigger orders) is
   cancelled (``trade/cancel-algos``). Stop-loss conditionals and OCO orders are **kept**, so every open
   position stays protected on OKX (rule 3, decision #251). The report counts them in ``stops_kept``.
3. Only with ``flatten``: each open position is closed with ``trade/close-position`` (a market close
   that can only reduce), and once it is closed its remaining algo orders are cancelled. A position
   whose close failed keeps its stop.

Each failed request is retried up to ``executor.order_max_retries`` times with the Executor's backoff.
OKX's "nothing left to cancel/close" answers (51400/51401/51402, 51023) count as done, not failed.
The report is ``{cancelled, closed, stops_kept, failed[], items[]}``; ``failed`` rows carry OKX's
error in plain English (``trading_core.okx.error_messages``). The War Room gets one ``system_alert``
with the summary and ``atlas.system`` one ``system.kill``.

Nothing here places an opening order, withdraws or transfers (brief rules 1 and 5).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import redis
from atlas_shared import bus
from atlas_shared.db import AlgoOrder, Connection, Order, TradingState
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from trading_core.execution.config import (
    CANCEL_NOOP_CODES,
    SYSTEM_STREAM,
    WARROOM_STREAM,
    ExecutorConfig,
)
from trading_core.okx.client import OkxClient, RetryPolicy
from trading_core.okx.connection import client_for_connection
from trading_core.okx.errors import OkxClientError
from trading_core.okx.models import Position
from trading_core.okx.trade import OkxTradeClient, OkxTradeError, ack_error, new_cl_ord_id
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail
from trading_core.risk.breakers import SELF_CLEARING, Breakers
from trading_core.risk.context import ensure_trading_state

log = logging.getLogger("trading_core.execution.kill")

SOURCE = "service:trading_core.kill"
WARROOM_SOURCE = "system:kill_switch"
WARROOM_AUTHOR = "kill_switch"
NO_POSITION_CODE = "51023"
OPEN_ORDER_STATES = frozenset({"new", "sending", "live", "partially_filled"})
LIVE_ALGO_STATES = frozenset({"live", "partially_effective", "pause"})
CANCEL_CHUNK = 10
ALGO_TYPES = ("conditional,oco", "trigger")

Sleep = Callable[[float], Awaitable[None]]
OkxFactory = Callable[..., OkxClient]
Action = Literal["cancelled", "closed", "kept", "already_done", "failed"]


class ControlError(Exception):
    """A request the control functions refuse before touching OKX (``status`` is the HTTP code)."""

    def __init__(self, reason: str, detail: str, status: int) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.status = status


@dataclass
class Item:
    kind: Literal["order", "algo", "position", "read"]
    inst_id: str | None
    ref: str | None
    action: Action
    attempts: int = 0
    code: str | None = None
    message: str | None = None
    pos_side: str | None = None
    algo_kind: str | None = None

    def payload(self) -> dict[str, Any]:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


@dataclass
class Report:
    env: str
    flatten: bool
    at: datetime
    items: list[Item] = field(default_factory=list)

    def count(self, action: Action, kinds: Sequence[str] = ("order", "algo", "position")) -> int:
        return sum(1 for i in self.items if i.action == action and i.kind in kinds)

    @property
    def failed(self) -> list[Item]:
        return [i for i in self.items if i.action == "failed"]

    def payload(self) -> dict[str, Any]:
        return {
            "ok": not self.failed,
            "env": self.env,
            "flatten": self.flatten,
            "at": self.at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "cancelled": self.count("cancelled", ("order", "algo")),
            "cancelled_orders": self.count("cancelled", ("order",)),
            "cancelled_algos": self.count("cancelled", ("algo",)),
            "already_done": self.count("already_done"),
            "closed": self.count("closed", ("position",)),
            "stops_kept": self.count("kept", ("algo",)),
            "failed": [i.payload() for i in self.failed],
            "items": [i.payload() for i in self.items],
        }


@dataclass
class _Attempt:
    ok: bool
    value: Any = None
    attempts: int = 0
    noop: bool = False
    code: str | None = None
    message: str | None = None


def _error_parts(err: Exception) -> tuple[str | None, str]:
    if isinstance(err, OkxTradeError):
        return err.s_code, err.human_message
    return None, "OKX could not be reached." if isinstance(err, OkxClientError) else str(err)


def _has_stop(algo: AlgoOrderDetail) -> bool:
    return algo.ord_type == "oco" or algo.sl_trigger_px is not None


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


class _Control:
    """One env's OKX trade client plus the retry rule and the event publisher."""

    def __init__(
        self,
        engine: Engine,
        env: str,
        rds: redis.Redis,
        trade: OkxTradeClient | None,
        cfg: ExecutorConfig,
        sleep: Sleep,
        now: Callable[[], datetime],
    ) -> None:
        self.engine = engine
        self.env = env
        self.rds = rds
        self.trade = trade
        self.cfg = cfg
        self.sleep = sleep
        self.now = now

    # -- retries -------------------------------------------------------------------------------------

    async def retry(self, call: Callable[[], Awaitable[Any]], noop_codes: frozenset[str]) -> _Attempt:
        """Run ``call`` up to ``order_max_retries`` times. An OKX answer in ``noop_codes`` means the
        thing is already gone: done, not failed."""
        code: str | None = None
        message = ""
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
            try:
                return _Attempt(True, await call(), attempt)
            except OkxTradeError as err:
                if err.s_code in noop_codes:
                    return _Attempt(True, None, attempt, noop=True, code=err.s_code)
                code, message = _error_parts(err)
            except OkxClientError as err:
                code, message = _error_parts(err)
            log.warning("control request failed", extra={"attempt": attempt, "code": code})
        return _Attempt(False, None, self.cfg.order_max_retries, code=code, message=message)

    # -- reads ---------------------------------------------------------------------------------------

    async def pending_orders(self, inst_id: str | None = None) -> list[OrderDetail]:
        assert self.trade is not None
        trade = self.trade
        limit = int(DEFAULTS["okx_rest"]["page_limits"]["trade/orders-pending"])
        rows: list[OrderDetail] = []
        after: str | None = None
        while True:
            page = await trade.get_orders_pending(inst_id=inst_id, after=after, limit=limit)
            rows.extend(page)
            if len(page) < limit or not page[-1].ord_id:
                return rows
            after = page[-1].ord_id

    async def pending_algos(self, inst_id: str | None = None) -> list[AlgoOrderDetail]:
        assert self.trade is not None
        trade = self.trade
        limit = int(DEFAULTS["okx_rest"]["page_limits"]["trade/orders-algo-pending"])
        rows: list[AlgoOrderDetail] = []
        for ord_type in ALGO_TYPES:
            after: str | None = None
            while True:
                page = await trade.get_algo_orders_pending(
                    ord_type, inst_id=inst_id, after=after, limit=limit
                )
                rows.extend(page)
                if len(page) < limit or not page[-1].algo_id:
                    break
                after = page[-1].algo_id
        return rows

    async def positions(self, inst_id: str | None = None) -> list[Position]:
        assert self.trade is not None
        rows = await self.trade._client.get_positions(inst_id=inst_id)
        return [p for p in rows if p.pos is not None and p.pos != 0]

    async def read(self, report: Report, what: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """A read with retries; a read that never succeeds is a failed report row and returns None."""
        got = await self.retry(call, frozenset())
        if not got.ok:
            report.items.append(
                Item(
                    "read",
                    None,
                    what,
                    "failed",
                    got.attempts,
                    got.code,
                    f"Could not read {what}: {got.message}",
                )
            )
            return None
        return got.value

    # -- writes --------------------------------------------------------------------------------------

    async def cancel_order(self, inst_id: str, ord_id: str | None, cl_ord_id: str | None) -> Item:
        assert self.trade is not None
        trade = self.trade
        ref = ord_id or cl_ord_id

        async def call() -> Any:
            if ord_id:
                return await trade.cancel_order(inst_id, ord_id=ord_id)
            return await trade.cancel_order(inst_id, cl_ord_id=cl_ord_id)

        got = await self.retry(call, CANCEL_NOOP_CODES)
        if not got.ok:
            return Item("order", inst_id, ref, "failed", got.attempts, got.code, got.message)
        self._mark_order(ord_id, cl_ord_id)
        return Item(
            "order", inst_id, ref, "already_done" if got.noop else "cancelled", got.attempts, got.code
        )

    async def cancel_orders(self, orders: Sequence[OrderDetail]) -> list[Item]:
        items: list[Item] = []
        for start in range(0, len(orders), CANCEL_CHUNK):
            chunk = orders[start : start + CANCEL_CHUNK]
            items.extend(
                await asyncio.gather(
                    *(self.cancel_order(o.inst_id, o.ord_id or None, o.cl_ord_id) for o in chunk)
                )
            )
        return items

    async def cancel_algos(self, algos: Sequence[AlgoRef]) -> list[Item]:
        """Cancel algo orders ten per request; only the rows OKX refused are retried."""
        assert self.trade is not None
        trade = self.trade
        cap = int(DEFAULTS["okx_rest"]["page_limits"]["trade/cancel-algos"])
        items: list[Item] = []
        for start in range(0, len(algos), cap):
            todo = {a.algo_id: a for a in algos[start : start + cap]}
            errors: dict[str, tuple[str | None, str]] = {}
            done: dict[str, tuple[Action, int, str | None]] = {}
            for attempt in range(1, self.cfg.order_max_retries + 1):
                if not todo:
                    break
                if attempt > 1:
                    await self.sleep(self.cfg.backoff(attempt - 1))
                try:
                    acks = await trade.cancel_algos([(a.inst_id, a.algo_id) for a in todo.values()])
                except OkxClientError as err:
                    for algo_id in todo:
                        errors[algo_id] = _error_parts(err)
                    continue
                by_id = {a.algo_id: a for a in acks}
                for algo_id in list(todo):
                    ack = by_id.get(algo_id)
                    refused = ack_error(ack, "trade/cancel-algos") if ack is not None else None
                    if ack is not None and refused is None:
                        done[algo_id] = ("cancelled", attempt, None)
                    elif refused is not None and refused.s_code in CANCEL_NOOP_CODES:
                        done[algo_id] = ("already_done", attempt, refused.s_code)
                    else:
                        errors[algo_id] = (
                            _error_parts(refused) if refused is not None else (None, "OKX did not answer.")
                        )
                        continue
                    del todo[algo_id]
            for algo in algos[start : start + cap]:
                if algo.algo_id in done:
                    action, attempts, code = done[algo.algo_id]
                    self._mark_algo(algo.algo_id)
                    items.append(
                        Item("algo", algo.inst_id, algo.algo_id, action, attempts, code, algo_kind=algo.kind)
                    )
                else:
                    code, message = errors.get(algo.algo_id, (None, "OKX did not answer."))
                    items.append(
                        Item(
                            "algo",
                            algo.inst_id,
                            algo.algo_id,
                            "failed",
                            self.cfg.order_max_retries,
                            code,
                            message,
                            algo_kind=algo.kind,
                        )
                    )
        return items

    async def close(self, position: Position) -> Item:
        """Market-close a whole position (``trade/close-position`` only ever reduces). The clOrdId is
        minted once and reused on every retry."""
        assert self.trade is not None
        trade = self.trade
        pos_side = position.pos_side if position.pos_side in ("long", "short") else None
        cl_ord_id = new_cl_ord_id("kil")

        async def call() -> Any:
            return await trade.close_position(
                position.inst_id,
                position.mgn_mode,  # type: ignore[arg-type]
                pos_side=pos_side,  # type: ignore[arg-type]
                auto_cxl=True,
                cl_ord_id=cl_ord_id,
            )

        got = await self.retry(call, frozenset({NO_POSITION_CODE}))
        side = position.pos_side or "net"
        if not got.ok:
            return Item(
                "position", position.inst_id, cl_ord_id, "failed", got.attempts, got.code, got.message, side
            )
        action: Action = "already_done" if got.noop else "closed"
        return Item("position", position.inst_id, cl_ord_id, action, got.attempts, got.code, pos_side=side)

    # -- database ------------------------------------------------------------------------------------

    def _mark_order(self, ord_id: str | None, cl_ord_id: str | None) -> None:
        """Mark our copy cancelled now; the private feed overwrites it with OKX's final state."""
        cond = Order.ord_id == ord_id if ord_id else Order.cl_ord_id == cl_ord_id
        with Session(self.engine) as s, s.begin():
            s.execute(
                update(Order)
                .where(Order.env == self.env, cond, Order.state.in_(OPEN_ORDER_STATES))
                .values(state="canceled", updated_at=self.now())
            )

    def _mark_algo(self, algo_id: str) -> None:
        with Session(self.engine) as s, s.begin():
            s.execute(
                update(AlgoOrder)
                .where(
                    AlgoOrder.env == self.env,
                    AlgoOrder.algo_id == algo_id,
                    AlgoOrder.state.in_(("new", "sending", *LIVE_ALGO_STATES)),
                )
                .values(state="canceled", updated_at=self.now())
            )

    # -- events --------------------------------------------------------------------------------------

    def publish(self, stream: str, type_: str, payload: dict[str, Any], source: str = SOURCE) -> None:
        envelope = EventEnvelope.model_validate(
            {"type": type_, "env": self.env, "source": source, "payload": payload}
        )
        try:
            bus.publish(stream, envelope, client=self.rds)
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": type_})

    def warroom(self, kind: str, content_md: str, card: dict[str, Any] | None) -> None:
        self.publish(
            WARROOM_STREAM,
            "warroom.message",
            {
                "thread": {"kind": "main", "key": "main"},
                "author_type": "system",
                "author": WARROOM_AUTHOR,
                "kind": kind,
                "content_md": content_md,
                "card": card,
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


@dataclass(frozen=True)
class AlgoRef:
    inst_id: str
    algo_id: str
    kind: str | None = None


def _ref(algo: AlgoOrderDetail) -> AlgoRef:
    return AlgoRef(algo.inst_id, algo.algo_id, _algo_kind(algo))


def _algo_kind(algo: AlgoOrderDetail) -> str:
    if algo.ord_type == "oco":
        return "oco"
    if algo.ord_type == "conditional":
        return "sl" if algo.sl_trigger_px is not None else "tp"
    return algo.ord_type


# ---- trading state -----------------------------------------------------------------------------------


def _state_payload(row: TradingState, by: str, cleared: list[str] | None = None) -> dict[str, Any]:
    breakers = row.breakers if isinstance(row.breakers, dict) else {}
    return {
        "paused": bool(row.paused),
        "reason": row.reason,
        "paused_at": _iso(row.paused_at) if row.paused_at else None,
        "by": by,
        "active_breakers": sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active")),
        "breakers_cleared": cleared or [],
    }


def set_paused(
    engine: Engine,
    env: str,
    paused: bool,
    *,
    reason: str | None,
    by: str = "owner",
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
    cleared: list[str] | None = None,
) -> dict[str, Any]:
    """Write ``trading_state.paused`` for ``env``, publish ``system.trading_state``, return its payload."""
    at = (now or (lambda: datetime.now(UTC)))()
    with Session(engine) as s, s.begin():
        ensure_trading_state(s, env, at)
        row = s.scalar(
            select(TradingState)
            .where(TradingState.env == env)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        row.paused = paused
        row.reason = reason if paused else None
        row.paused_at = at if paused else None
        row.updated_at = at
        payload = _state_payload(row, by, cleared)
    rds = redis_client if redis_client is not None else bus.make_client()
    envelope = EventEnvelope.model_validate(
        {"type": "system.trading_state", "env": env, "source": SOURCE, "payload": payload}
    )
    try:
        bus.publish(SYSTEM_STREAM, envelope, client=rds)
    except redis.RedisError:
        log.warning("event publish failed", extra={"type": "system.trading_state"})
    return payload


def pause(
    engine: Engine,
    env: str,
    *,
    reason: str | None = None,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return set_paused(
        engine, env, True, reason=reason or "Paused by the owner.", redis_client=redis_client, now=now
    )


def resume(
    engine: Engine,
    env: str,
    *,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Clear every owner-clearable breaker (all but the self-clearing ``data_stale``), then unpause."""
    rds = redis_client if redis_client is not None else bus.make_client()
    breakers = Breakers(engine, env, redis_client=rds, now=now)
    cleared = [
        n for n in sorted(breakers.active()) if n not in SELF_CLEARING and breakers.clear(n, by="owner")
    ]
    return set_paused(engine, env, False, reason=None, redis_client=rds, now=now, cleared=cleared)


# ---- OKX client --------------------------------------------------------------------------------------


def _settings_data(session: Session, env: str) -> dict[str, Any]:
    row = session.execute(text("SELECT data FROM settings WHERE env = :env"), {"env": env}).first()
    data = row[0] if row is not None and isinstance(row[0], dict) else {}
    executor = data.get("executor")
    return executor if isinstance(executor, dict) else data


def _connection(session: Session, env: str) -> Connection | None:
    return session.scalar(
        select(Connection)
        .where(Connection.exchange == "okx", Connection.env == env)
        .order_by(Connection.last_verified_at.desc().nulls_last())
        .limit(1)
    )


@dataclass(frozen=True)
class _CredSnapshot:
    env: str
    api_key: str
    api_secret: str
    passphrase: str


def _open(
    engine: Engine, env: str, client_factory: OkxFactory | None, app_key: str
) -> tuple[OkxClient | None, ExecutorConfig]:
    with Session(engine) as s:
        cfg = ExecutorConfig.from_settings(_settings_data(s, env))
        conn = _connection(s, env)
        creds = (
            _CredSnapshot(conn.env, conn.api_key, conn.api_secret, conn.passphrase)
            if conn is not None
            else None
        )
    if creds is None:
        return None, cfg

    def default(c: Any, **kw: Any) -> OkxClient:
        return client_for_connection(c, app_key, **kw)

    factory = client_factory or default
    # One attempt per HTTP call: the retries are ours (order_max_retries, with backoff).
    retry = dataclasses.replace(RetryPolicy.from_defaults(), max_attempts=1)
    return factory(creds, retry=retry), cfg


# ---- kill --------------------------------------------------------------------------------------------


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "**Kill switch.** Trading is paused.",
        f"Cancelled {report['cancelled_orders']} order(s) and {report['cancelled_algos']} take-profit/trigger"
        f" order(s); kept {report['stops_kept']} stop-loss order(s) on OKX.",
    ]
    if report["flatten"]:
        lines.append(f"Closed {report['closed']} position(s).")
    if report["failed"]:
        lines.append(f"{len(report['failed'])} item(s) failed:")
        for f in report["failed"][:10]:
            what = " ".join(str(f[k]) for k in ("inst_id", "ref") if f.get(k))
            lines.append(f"- {f['kind']} {what}: {f.get('message') or 'failed'}")
    return "\n".join(lines)


async def kill(
    env: str,
    *,
    flatten: bool,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = now or (lambda: datetime.now(UTC))
    rds = redis_client if redis_client is not None else bus.make_client()
    reason = "Kill switch (positions closed)." if flatten else "Kill switch."
    set_paused(engine, env, True, reason=reason, redis_client=rds, now=clock)
    report = Report(env, flatten, clock())
    okx, cfg = _open(engine, env, client_factory, app_key)
    ctl = _Control(engine, env, rds, None, cfg, sleep, clock)
    if okx is None:
        report.items.append(
            Item("read", None, "connection", "failed", 0, None, f"No OKX {env} connection is stored.")
        )
    else:
        async with okx:
            ctl.trade = OkxTradeClient(okx)
            await _kill(ctl, report, flatten)
    payload = report.payload()
    log.warning(
        "kill switch",
        extra={
            "env": env,
            "flatten": flatten,
            "cancelled": payload["cancelled"],
            "closed": payload["closed"],
        },
    )
    payload["paused"] = True
    ctl.publish(SYSTEM_STREAM, "system.kill", payload)
    ctl.warroom("system_alert", _summary(payload), {"type": "kill_switch", "code": "kill_switch", **payload})
    return payload


async def _kill(ctl: _Control, report: Report, flatten: bool) -> None:
    orders = await ctl.read(report, "pending orders", ctl.pending_orders)
    algos: list[AlgoOrderDetail] = await ctl.read(report, "pending algo orders", ctl.pending_algos) or []
    if orders:
        report.items.extend(await ctl.cancel_orders(orders))
    report.items.extend(await ctl.cancel_algos([_ref(a) for a in algos if not _has_stop(a)]))
    stops = [a for a in algos if _has_stop(a)]
    released: set[str] = set()
    if flatten:
        positions: list[Position] = await ctl.read(report, "positions", ctl.positions) or []
        for position in positions:
            item = await ctl.close(position)
            report.items.append(item)
            if item.action == "failed":
                continue
            mine = [
                a
                for a in stops
                if a.inst_id == position.inst_id and (a.pos_side or "net") == (position.pos_side or "net")
            ]
            released.update(a.algo_id for a in mine)
            report.items.extend(await ctl.cancel_algos([_ref(a) for a in mine]))
    for algo in stops:
        if algo.algo_id not in released:
            report.items.append(Item("algo", algo.inst_id, algo.algo_id, "kept", algo_kind=_algo_kind(algo)))


def _attached(algo: AlgoOrder) -> bool:
    """The Executor's attached TP/SL (``s…``) rides on the entry order itself (ATL-143)."""
    return bool(algo.algo_cl_ord_id and algo.algo_cl_ord_id.startswith("s")) or bool(
        isinstance(algo.raw, dict) and algo.raw.get("attached")
    )


# ---- one position, one order ---------------------------------------------------------------------------


async def close_position(
    env: str,
    inst_id: str,
    pos_side: str | None = None,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Market-close one position (reduce-only), then cancel that position's algo orders (SL/TP/OCO)."""
    clock = now or (lambda: datetime.now(UTC))
    rds = redis_client if redis_client is not None else bus.make_client()
    okx, cfg = _open(engine, env, client_factory, app_key)
    if okx is None:
        raise ControlError("connection_missing", f"No OKX {env} connection is stored.", 409)
    report = Report(env, True, clock())
    ctl = _Control(engine, env, rds, None, cfg, sleep, clock)
    async with okx:
        ctl.trade = OkxTradeClient(okx)
        positions = await ctl.read(report, "positions", lambda: ctl.positions(inst_id))
        if positions is None:
            return {**report.payload(), "inst_id": inst_id, "status": "failed"}
        wanted = pos_side or None
        matches = [
            p
            for p in positions
            if p.inst_id == inst_id and (wanted is None or (p.pos_side or "net") == wanted)
        ]
        if not matches:
            raise ControlError("no_position", f"There is no open {inst_id} position.", 404)
        for position in matches:
            item = await ctl.close(position)
            report.items.append(item)
            if item.action == "failed":
                continue
            side = position.pos_side or "net"
            algos = await ctl.read(report, "pending algo orders", lambda: ctl.pending_algos(inst_id)) or []
            report.items.extend(
                await ctl.cancel_algos([_ref(a) for a in algos if (a.pos_side or "net") == side])
            )
    payload = {**report.payload(), "inst_id": inst_id}
    payload["status"] = "closed" if payload["ok"] else "failed"
    if payload["ok"]:
        ctl.warroom("text", f"Closed the {inst_id} position at market on the owner's request.", None)
    else:
        message = "; ".join(f.get("message") or "failed" for f in payload["failed"])
        ctl.warroom(
            "system_alert",
            f"**Close of {inst_id} failed.** {message}",
            {"type": "position_close", "code": "position_close_failed", **payload},
        )
    ctl.publish(SYSTEM_STREAM, "system.position_close", payload)
    return payload


async def cancel_order(
    env: str,
    order_id: str,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Cancel one open order. For an Atlas entry, also cancel its protection when nothing filled
    (with a fill behind it, the protection stays: it guards that position)."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        oid = uuid.UUID(str(order_id))
    except ValueError as exc:
        raise ControlError("order_not_found", "Not an order id.", 404) from exc
    with Session(engine) as s:
        row = s.scalar(select(Order).where(Order.id == oid, Order.env == env))
        if row is None:
            raise ControlError("order_not_found", "No such order.", 404)
        if row.state not in OPEN_ORDER_STATES:
            raise ControlError("order_not_open", f"The order is {row.state}.", 409)
        inst_id, ord_id, cl_ord_id = row.inst_id, row.ord_id, row.cl_ord_id
        is_entry = row.origin == "atlas" and row.purpose == "entry"
        protection = (
            [
                (a.inst_id, str(a.algo_id), _attached(a))
                for a in s.scalars(
                    select(AlgoOrder).where(
                        AlgoOrder.order_id == oid,
                        AlgoOrder.algo_id.is_not(None),
                        AlgoOrder.state.in_(LIVE_ALGO_STATES),
                    )
                )
            ]
            if is_entry
            else []
        )
    if not ord_id and not cl_ord_id:
        raise ControlError("order_not_open", "The order has no OKX id.", 409)
    rds = redis_client if redis_client is not None else bus.make_client()
    okx, cfg = _open(engine, env, client_factory, app_key)
    if okx is None:
        raise ControlError("connection_missing", f"No OKX {env} connection is stored.", 409)
    report = Report(env, False, clock())
    ctl = _Control(engine, env, rds, None, cfg, sleep, clock)
    filled: Decimal | None = None
    async with okx:
        ctl.trade = OkxTradeClient(okx)
        trade = ctl.trade
        item = await ctl.cancel_order(inst_id, ord_id, cl_ord_id)
        report.items.append(item)
        if item.action != "failed" and protection:

            async def lookup() -> OrderDetail:
                if ord_id:
                    return await trade.get_order(inst_id, ord_id=ord_id)
                return await trade.get_order(inst_id, cl_ord_id=cl_ord_id)

            detail = await ctl.read(report, "the cancelled order", lookup)
            if detail is not None:
                filled = detail.acc_fill_sz or Decimal(0)
                with Session(engine) as s, s.begin():
                    s.execute(
                        update(Order).where(Order.id == oid).values(filled_sz=filled, updated_at=clock())
                    )
                if filled == 0:
                    # An attached TP/SL dies with its unfilled entry; only stand-alone ones are cancelled.
                    for inst, algo_id, attached in protection:
                        if attached:
                            ctl._mark_algo(algo_id)
                            report.items.append(Item("algo", inst, algo_id, "already_done"))
                    report.items.extend(
                        await ctl.cancel_algos(
                            [AlgoRef(i, a) for i, a, attached in protection if not attached]
                        )
                    )
                else:
                    for inst, algo_id, _ in protection:
                        report.items.append(Item("algo", inst, algo_id, "kept"))
    payload = {**report.payload(), "order_id": str(oid), "inst_id": inst_id}
    payload["status"] = "cancelled" if payload["ok"] else "failed"
    payload["filled_sz"] = None if filled is None else format(filled, "f")
    ctl.publish(SYSTEM_STREAM, "system.order_cancel", payload)
    if not payload["ok"]:
        message = "; ".join(f.get("message") or "failed" for f in payload["failed"])
        ctl.warroom(
            "system_alert",
            f"**Cancel of the {inst_id} order failed.** {message}",
            {"type": "order_cancel", "code": "order_cancel_failed", **payload},
        )
    return payload


__all__ = ["ControlError", "cancel_order", "close_position", "kill", "pause", "resume", "set_paused"]
