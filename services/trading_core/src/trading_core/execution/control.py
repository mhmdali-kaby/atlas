"""The OKX side of the owner's trading controls: one env's trade client with the retry rule, the
reads (pending orders, pending algos, positions), the cancels and the market close, and the report the
kill switch and the single close/cancel return.

Each failed request is retried up to ``executor.order_max_retries`` times with the Executor's backoff.
OKX's "nothing left to cancel/close" answers (51400/51401/51402, 51023) count as done, not failed;
``failed`` rows carry OKX's error in plain English (``trading_core.okx.error_messages``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import redis
from atlas_shared.db import AlgoOrder, Order
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from trading_core.execution.config import CANCEL_NOOP_CODES, ExecutorConfig
from trading_core.execution.okx_access import publish, publish_warroom
from trading_core.execution.trading_state import SOURCE, iso
from trading_core.okx.client import OkxClient
from trading_core.okx.errors import OkxClientError
from trading_core.okx.models import Position
from trading_core.okx.trade import OkxTradeClient, OkxTradeError, ack_error, new_cl_ord_id
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

log = logging.getLogger("trading_core.execution.control")

WARROOM_SOURCE = "system:kill_switch"
WARROOM_AUTHOR = "kill_switch"
NO_POSITION_CODE = "51023"
OPEN_ORDER_STATES = frozenset({"new", "sending", "live", "partially_filled"})
LIVE_ALGO_STATES = frozenset({"live", "partially_effective", "pause"})
CANCEL_CHUNK = 10
ALGO_TYPES = ("conditional,oco", "trigger")

Sleep = Callable[[float], Awaitable[None]]
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
            "at": iso(self.at),
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


def has_stop(algo: AlgoOrderDetail) -> bool:
    return algo.ord_type == "oco" or algo.sl_trigger_px is not None


class Control:
    """One env's OKX client plus the retry rule and the event publisher. ``bind`` it to an open
    OkxClient before any OKX call."""

    def __init__(
        self,
        engine: Engine,
        env: str,
        rds: redis.Redis,
        cfg: ExecutorConfig,
        sleep: Sleep,
        now: Callable[[], datetime],
    ) -> None:
        self.engine = engine
        self.env = env
        self.rds = rds
        self.cfg = cfg
        self.sleep = sleep
        self.now = now
        self.okx: OkxClient | None = None
        self.trade: OkxTradeClient | None = None

    def bind(self, okx: OkxClient) -> OkxTradeClient:
        self.okx = okx
        self.trade = OkxTradeClient(okx)
        return self.trade

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
        assert self.okx is not None
        rows = await self.okx.get_positions(inst_id=inst_id)
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
        publish(self.rds, stream, type_, self.env, payload, source=source)

    def warroom(self, kind: str, content_md: str, card: dict[str, Any] | None) -> None:
        publish_warroom(
            self.rds,
            self.env,
            author=WARROOM_AUTHOR,
            kind=kind,
            content_md=content_md,
            card=card,
            source=WARROOM_SOURCE,
        )


@dataclass(frozen=True)
class AlgoRef:
    inst_id: str
    algo_id: str
    kind: str | None = None


def ref(algo: AlgoOrderDetail) -> AlgoRef:
    return AlgoRef(algo.inst_id, algo.algo_id, algo_kind(algo))


def algo_kind(algo: AlgoOrderDetail) -> str:
    if algo.ord_type == "oco":
        return "oco"
    if algo.ord_type == "conditional":
        return "sl" if algo.sl_trigger_px is not None else "tp"
    return algo.ord_type


__all__ = ["AlgoRef", "Control", "ControlError", "Item", "Report", "algo_kind", "has_stop", "ref"]
