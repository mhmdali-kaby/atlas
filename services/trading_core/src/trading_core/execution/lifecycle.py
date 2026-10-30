"""After the entry is sent (brief §11): protection sized to the fill, the entry timeout, fill status.

``EntryLifecycle`` is driven three ways, all idempotent and serialised per order:

- **Every order push** from the private WebSocket (``PrivateFeed.on_order``). A new fill posts one War
  Room ``fill`` message (the ``fills`` row is unique per trade id, so a replayed push posts nothing).
  For an Atlas entry with anything filled, the protection is brought to exactly the filled size and
  the proposal moves to ``partially_filled`` / ``filled``.
- **The sweep** (every ``executor.lifecycle_sweep_seconds``): limit and post_only entries still open
  ``entry_timeout_minutes`` after they were created are cancelled. Nothing filled → the proposal is
  ``expired`` and any stand-alone protection is cancelled; a partial fill keeps its stop at the filled
  size and the proposal stays ``partially_filled``. The sweep also re-settles any filled entry whose
  stored protection does not match its filled size, which is how a restart in the middle of a resize
  finishes it.
- **The owner's cancel** (``cancel``, from ``POST /internal/orders/{id}/cancel``): the same steps as
  the timeout, under the same lock; with nothing filled the proposal becomes ``cancelled``.

Protection rules. OKX is asked first (``orders-algo-pending``): our protective algos are the pending
SL/OCO orders carrying a stop whose ``algoClOrdId`` is derived from the risk check (``s`` attached,
``o`` stand-alone OCO, ``p`` placed here) or whose ``ordId`` is the entry's. The first of them (s, o, p
order) is kept and amended to ``sz = filled_sz`` (``trade/amend-algos`` with only ``newSz``, so the
stop and take-profit prices never move); any other one is cancelled. When OKX shows none, a stand-alone
stop ``p`` + 31 hex is placed at the stored stop and take-profit for the filled size. A protection
that already has the filled size is left alone: no amend, no cancel, no place. A stop that has fired
(``effective``) or a protection someone cancelled with no live one left is never replaced.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import redis
from atlas_shared import bus
from atlas_shared.db import AlgoOrder, Order, Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.execution.config import (
    CANCEL_NOOP_CODES,
    ORDER_NOT_FOUND,
    ORDERS_STREAM,
    SOURCE,
    WARROOM_SOURCE,
    derived_id,
)
from trading_core.execution.okx_access import (
    OkxFactory,
    default_factory,
    executor_settings,
    load_creds,
    publish,
    single_attempt,
    warroom_payload,
)
from trading_core.execution.order_store import OrderUpsert, fill_payload
from trading_core.execution.recorder import _OKX_ORDER_STATES, _Recorder, _s
from trading_core.okx.client import OkxClient
from trading_core.okx.errors import OkxClientError
from trading_core.okx.trade import AlgoOrderRequest, OkxTradeClient, OkxTradeError
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

log = logging.getLogger("trading_core.execution.lifecycle")

JOB = "execution.lifecycle"
PROPOSALS_STREAM = "atlas.proposals"
PROTECT_PREFIXES = ("s", "o", "p")
PROTECTING = frozenset({"live", "partially_effective"})
TIMEOUT_ORD_TYPES = ("limit", "post_only")
OPEN_ENTRY_STATES = ("live", "partially_filled")
# The proposal statuses each fill status may move from (never backwards: filled stays filled).
_FROM = {
    "partially_filled": frozenset({"risk_checked", "awaiting_owner", "sent"}),
    "filled": frozenset({"risk_checked", "awaiting_owner", "sent", "partially_filled"}),
    "expired": frozenset({"risk_checked", "awaiting_owner", "sent"}),
    "cancelled": frozenset({"risk_checked", "awaiting_owner", "sent"}),
}

Sleep = Callable[[float], Awaitable[None]]
EndReason = Literal["timeout", "owner"]
ProtectStatus = Literal["nothing", "in_sync", "amended", "placed", "skipped", "failed"]


@dataclass(frozen=True)
class ProtectResult:
    """What ``settle`` did to an entry's protection. ``target_sz`` is the entry's filled size."""

    status: ProtectStatus
    target_sz: Decimal
    algo_cl_ord_id: str | None = None
    canceled: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class EntryCancel:
    """What ``cancel``/``expire`` did to one Atlas entry.

    ``status``: ``cancelled`` (OKX confirms it is no longer open; ``noop`` when it already was not),
    ``not_open`` (the stored entry is not live/partially_filled: nothing sent), ``failed`` (the cancel
    request failed every attempt; ``code``/``message`` carry OKX's error) or ``unconfirmed`` (cancel
    sent, but OKX's read-back still shows it open or could not be read)."""

    status: Literal["cancelled", "not_open", "failed", "unconfirmed"]
    attempts: int = 0
    noop: bool = False
    code: str | None = None
    message: str | None = None
    filled_sz: Decimal | None = None
    protection: ProtectResult | None = None


@dataclass(frozen=True)
class _Ctx:
    env: str
    correlation_id: str
    proposal_id: uuid.UUID


@dataclass(frozen=True)
class _Entry:
    """A snapshot of one Atlas entry order row, read in one short transaction."""

    id: uuid.UUID
    env: str
    proposal_id: uuid.UUID
    risk_check_id: uuid.UUID
    correlation_id: str
    cl_ord_id: str
    ord_id: str | None
    inst_id: str
    side: str
    pos_side: str | None
    ord_type: str
    td_mode: str
    sz: Decimal
    filled_sz: Decimal
    state: str

    @property
    def ctx(self) -> _Ctx:
        return _Ctx(self.env, self.correlation_id, self.proposal_id)

    def protect_id(self, prefix: str) -> str:
        return derived_id(prefix, self.risk_check_id)

    @property
    def protect_ids(self) -> tuple[str, ...]:
        return tuple(self.protect_id(p) for p in PROTECT_PREFIXES)


def _load_entry(session: Session, order_id: Any) -> _Entry | None:
    row = session.get(Order, order_id)
    if (
        row is None
        or row.origin != "atlas"
        or row.purpose != "entry"
        or row.proposal_id is None
        or row.risk_check_id is None
        or row.cl_ord_id is None
    ):
        return None
    cycle = session.scalar(select(Proposal.cycle_id).where(Proposal.id == row.proposal_id))
    return _Entry(
        id=row.id,
        env=row.env,
        proposal_id=row.proposal_id,
        risk_check_id=row.risk_check_id,
        correlation_id=str(cycle) if cycle else str(row.proposal_id),
        cl_ord_id=row.cl_ord_id,
        ord_id=row.ord_id,
        inst_id=row.inst_id,
        side=row.side,
        pos_side=row.pos_side,
        ord_type=row.ord_type,
        td_mode=row.td_mode,
        sz=Decimal(row.sz).normalize(),
        filled_sz=Decimal(row.filled_sz or 0).normalize(),
        state=row.state,
    )


def _rank(entry: _Entry, row: AlgoOrderDetail) -> int:
    ids = entry.protect_ids
    return ids.index(row.algo_cl_ord_id) if row.algo_cl_ord_id in ids else len(ids)


class EntryLifecycle:
    """Fill handling and entry timeouts for one env. One instance per process: its per-order locks
    are what keep the WebSocket callback and the sweep from resizing the same stop at once."""

    def __init__(
        self,
        engine: Engine,
        env: str,
        *,
        redis_client: redis.Redis | None = None,
        client_factory: OkxFactory | None = None,
        app_key: str = "",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.env = env
        self._rds = redis_client
        self._factory = client_factory or default_factory(app_key)
        self.now: Callable[[], datetime] = now or (lambda: datetime.now(UTC))
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    # -- plumbing ------------------------------------------------------------------------------------

    @property
    def rds(self) -> redis.Redis:
        if self._rds is None:
            self._rds = bus.make_client()
        return self._rds

    def _lock(self, order_id: uuid.UUID) -> asyncio.Lock:
        return self._locks.setdefault(order_id, asyncio.Lock())

    def _client(self) -> OkxClient | None:
        with Session(self.engine) as s:
            creds = load_creds(s, self.env, verified_only=True)
        if creds is None:
            log.warning("no verified OKX connection; protection not checked", extra={"env": self.env})
            return None
        # One attempt per call: every step re-reads OKX before acting, and the sweep retries.
        return self._factory(creds, retry=single_attempt())

    def _publish(
        self, stream: str, type_: str, payload: dict[str, Any], correlation_id: str, source: str = SOURCE
    ) -> None:
        publish(self.rds, stream, type_, self.env, payload, source=source, correlation_id=correlation_id)

    def entry_timeout(self) -> timedelta:
        value = executor_settings(self.engine, self.env).get("entry_timeout_minutes")
        minutes = int(value if value is not None else DEFAULTS["executor"]["entry_timeout_minutes"])
        return timedelta(minutes=max(1, minutes))

    # -- the WebSocket hook --------------------------------------------------------------------------

    async def on_order(self, u: OrderUpsert) -> None:
        """``PrivateFeed.on_order`` callback: one War Room message per new fill, then settle the entry."""
        fill = fill_payload(u)
        if fill is not None:
            await asyncio.to_thread(self._post_fill, u.order_id, fill)
        if u.order_id is None:
            return
        with Session(self.engine) as s:
            entry = _load_entry(s, u.order_id)
        if entry is not None and entry.filled_sz > 0:
            await self.settle(entry.id)

    def _post_fill(self, order_id: Any, fill: dict[str, Any]) -> None:
        with Session(self.engine) as s:
            order = s.get(Order, order_id) if order_id is not None else None
            proposal_id = order.proposal_id if order is not None else None
            cycle = (
                s.scalar(select(Proposal.cycle_id).where(Proposal.id == proposal_id)) if proposal_id else None
            )
            card = {
                "type": "fill",
                "fill_id": fill["fill_id"],
                "order_id": fill["order_id"],
                "proposal_id": str(proposal_id) if proposal_id else None,
                "trade_id": fill["trade_id"],
                "inst_id": fill["inst_id"],
                "side": fill["side"],
                "size": fill["fill_sz"],
                "price": fill["fill_px"],
                "fee": fill["fee"],
                "fee_ccy": fill["fee_ccy"],
                "exec_type": fill["exec_type"],
                "filled_sz": _s(order.filled_sz) if order is not None else None,
                "order_sz": _s(order.sz) if order is not None else None,
                "purpose": order.purpose if order is not None else None,
                "origin": order.origin if order is not None else None,
                "ts": fill["ts"],
            }
        correlation = str(cycle) if cycle else str(proposal_id or fill["order_id"])
        fee = f" (fee {card['fee']} {card['fee_ccy'] or ''})".rstrip() if card["fee"] is not None else ""
        content = f"Filled {card['side']} {card['size']} {card['inst_id']} at {card['price']}{fee}."
        self._publish(
            "atlas.warroom",
            "warroom.message",
            warroom_payload("executor", "fill", content, card),
            correlation,
            source=WARROOM_SOURCE,
        )

    # -- settle: protection + proposal status ---------------------------------------------------------

    async def settle(self, order_id: uuid.UUID, trade: OkxTradeClient | None = None) -> ProtectResult | None:
        """Bring one Atlas entry's protection to its filled size and set the proposal's fill status.
        None when the order is not an Atlas entry or there is no verified connection."""
        async with self._lock(order_id):
            with Session(self.engine) as s:
                entry = _load_entry(s, order_id)
            if entry is None:
                return None
            if trade is not None:
                return await self._settle(entry, trade)
            okx = self._client()
            if okx is None:
                return None
            async with okx:
                return await self._settle(entry, OkxTradeClient(okx))

    async def _settle(self, entry: _Entry, trade: OkxTradeClient) -> ProtectResult:
        result = await self._protect(entry, trade)
        if entry.state == "filled":
            self._proposal_status(entry, "filled")
        elif entry.filled_sz > 0:
            self._proposal_status(entry, "partially_filled")
        return result

    def _stored_protection(self, entry: _Entry) -> list[AlgoOrder]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(AlgoOrder).where(
                        AlgoOrder.env == entry.env, AlgoOrder.algo_cl_ord_id.in_(entry.protect_ids)
                    )
                )
            )

    @staticmethod
    def _protection_ended(rows: Sequence[AlgoOrder], has_close: bool) -> str | None:
        """Why the entry must not get a new stop: it fired, or it was deliberately removed."""
        if any(r.state in ("effective", "partially_effective") for r in rows):
            return "stop_triggered"
        if has_close:
            return "position_closing"
        if (
            rows
            and not any(r.state in ("new", "sending", "live") for r in rows)
            and any(r.state == "canceled" for r in rows)
        ):
            return "protection_canceled"
        return None

    def _has_close_order(self, entry: _Entry) -> bool:
        with Session(self.engine) as s:
            return (
                s.scalar(
                    select(Order.id)
                    .where(Order.proposal_id == entry.proposal_id, Order.purpose.in_(("close", "kill")))
                    .limit(1)
                )
                is not None
            )

    async def _protect(self, entry: _Entry, trade: OkxTradeClient) -> ProtectResult:
        target = entry.filled_sz
        if target <= 0:
            return ProtectResult("nothing", target)
        try:
            pending = await trade.get_algo_orders_pending("conditional,oco", inst_id=entry.inst_id)
        except OkxClientError as err:
            log.warning("algo lookup failed", extra={"error": type(err).__name__})
            return ProtectResult("failed", target, detail="algo_lookup_failed")
        ours = sorted(
            (
                r
                for r in pending
                if r.sl_trigger_px is not None
                and r.state in PROTECTING
                and (r.algo_cl_ord_id in entry.protect_ids or (entry.ord_id and r.ord_id == entry.ord_id))
            ),
            key=lambda r: _rank(entry, r),
        )
        rec = _Recorder(self.engine, self.rds, entry.ctx, self.now)
        if not ours:
            stored = self._stored_protection(entry)
            ended = self._protection_ended(stored, self._has_close_order(entry))
            if ended is not None:
                return ProtectResult("skipped", target, detail=ended)
            return await self._place(entry, trade, rec, stored)

        keeper, extras = ours[0], ours[1:]
        status: ProtectStatus = "in_sync"
        if keeper.sz != target:
            try:
                await trade.amend_algo(entry.inst_id, algo_id=keeper.algo_id, new_sz=target)
            except OkxClientError as err:
                msg = err.human_message if isinstance(err, OkxTradeError) else type(err).__name__
                self._alert(rec, entry, "protection_resize_failed", msg, target)
                return ProtectResult("failed", target, keeper.algo_cl_ord_id, detail=msg)
            status = "amended"
            self._record_resize(entry, keeper, target)
        canceled: list[str] = []
        if extras:
            try:
                acks = await trade.cancel_algos([(entry.inst_id, r.algo_id) for r in extras])
            except OkxClientError as err:
                log.warning("cancel of extra protection failed", extra={"error": type(err).__name__})
                acks = []
            done = {a.algo_id for a in acks if a.ok}
            for r in extras:
                if r.algo_id in done:
                    canceled.append(r.algo_cl_ord_id or r.algo_id)
                    self._record_algo_state(rec, entry, r, "canceled")
        return ProtectResult(status, target, keeper.algo_cl_ord_id, tuple(canceled))

    async def _place(
        self, entry: _Entry, trade: OkxTradeClient, rec: _Recorder, stored: Sequence[AlgoOrder]
    ) -> ProtectResult:
        target = entry.filled_sz
        template = next((r for r in stored if r.sl_trigger_px is not None or r.trigger_px is not None), None)
        if template is None:
            self._alert(rec, entry, "protection_missing", "No stored stop to protect the fill with.", target)
            return ProtectResult("failed", target, detail="no_stored_stop")
        raw_stop = template.sl_trigger_px if template.sl_trigger_px is not None else template.trigger_px
        assert raw_stop is not None
        stop = Decimal(raw_stop).normalize()
        tp = Decimal(template.tp_trigger_px).normalize() if template.tp_trigger_px is not None else None
        p_id = entry.protect_id("p")
        oco = tp is not None
        at = self.now()
        rec.algo(
            p_id,
            "sending",
            new=AlgoOrder(
                id=uuid.uuid4(),
                env=entry.env,
                order_id=entry.id,
                algo_cl_ord_id=p_id,
                inst_id=entry.inst_id,
                kind="oco" if oco else "sl",
                side=template.side,
                pos_side=entry.pos_side,
                trigger_px=None if oco else stop,
                sl_trigger_px=stop if oco else None,
                tp_trigger_px=tp,
                ord_px=None,
                sz=target,
                state="new",
                reduce_only=True,
                origin="atlas",
                raw={"placed_as": "fill-protect"},
                created_at=at,
                updated_at=at,
            ),
            sz=target,
        )
        req = AlgoOrderRequest(
            inst_id=entry.inst_id,
            td_mode=entry.td_mode,  # type: ignore[arg-type]
            side=template.side,  # type: ignore[arg-type]
            ord_type="oco" if oco else "conditional",
            algo_cl_ord_id=p_id,
            sz=target,
            pos_side=entry.pos_side,  # type: ignore[arg-type]
            reduce_only=True,
            sl_trigger_px=stop,
            tp_trigger_px=tp,
        )
        try:
            ack = await trade.place_algo_order(req)
        except OkxClientError as err:
            code = err.s_code if isinstance(err, OkxTradeError) else None
            msg = err.human_message if isinstance(err, OkxTradeError) else type(err).__name__
            rec.algo(p_id, "failed", last_error_code=code, last_error_msg=msg)
            self._alert(rec, entry, "protection_place_failed", msg, target)
            return ProtectResult("failed", target, p_id, detail=msg)
        rec.algo(p_id, "live", algo_id=ack.algo_id, sz=target)
        return ProtectResult("placed", target, p_id)

    def _record_resize(self, entry: _Entry, row: AlgoOrderDetail, target: Decimal) -> None:
        at = self.now()
        with Session(self.engine) as s, s.begin():
            stored = None
            if row.algo_cl_ord_id:
                stored = s.scalar(
                    select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == row.algo_cl_ord_id).with_for_update()
                )
            if stored is None:
                stored = s.scalar(
                    select(AlgoOrder)
                    .where(AlgoOrder.env == entry.env, AlgoOrder.algo_id == row.algo_id)
                    .with_for_update()
                )
            stored_id = None
            if stored is not None:
                stored.sz = target
                stored.algo_id = row.algo_id
                if stored.state in ("new", "sending"):
                    stored.state = "live"
                stored.updated_at = at
                stored_id = str(stored.id)
        self._publish(
            ORDERS_STREAM,
            "algo_order.resized",
            {
                "algo_order_id": stored_id,
                "order_id": str(entry.id),
                "proposal_id": str(entry.proposal_id),
                "algo_id": row.algo_id,
                "algo_cl_ord_id": row.algo_cl_ord_id,
                "inst_id": entry.inst_id,
                "from_sz": _s(row.sz),
                "to_sz": _s(target),
                "sl_trigger_px": _s(row.sl_trigger_px),
                "tp_trigger_px": _s(row.tp_trigger_px),
                "filled_sz": _s(entry.filled_sz),
                "order_sz": _s(entry.sz),
            },
            entry.correlation_id,
        )

    def _record_algo_state(self, rec: _Recorder, entry: _Entry, row: AlgoOrderDetail, state: str) -> None:
        if row.algo_cl_ord_id:
            rec.algo(row.algo_cl_ord_id, state, algo_id=row.algo_id)
            return
        with Session(self.engine) as s, s.begin():
            stored = s.scalar(
                select(AlgoOrder).where(AlgoOrder.env == entry.env, AlgoOrder.algo_id == row.algo_id)
            )
            if stored is not None and stored.algo_cl_ord_id:
                cl = stored.algo_cl_ord_id
            else:
                return
        rec.algo(cl, state)

    def _alert(self, rec: _Recorder, entry: _Entry, code: str, detail: str, target: Decimal) -> None:
        text_ = f"{entry.inst_id}: the stop could not be sized to the filled {_s(target)} — {detail}"
        rec.alert(
            code,
            text_,
            {
                "inst_id": entry.inst_id,
                "order_id": str(entry.id),
                "proposal_id": str(entry.proposal_id),
                "filled_sz": _s(target),
            },
        )

    def _proposal_status(self, entry: _Entry, status: str) -> None:
        with Session(self.engine) as s, s.begin():
            row = s.get(Proposal, entry.proposal_id, with_for_update=True)
            if row is None or row.status not in _FROM[status]:
                return
            before = row.status
            row.status = status
            row.updated_at = self.now()
        self._publish(
            PROPOSALS_STREAM,
            "proposal.status_changed",
            {
                "proposal_id": str(entry.proposal_id),
                "order_id": str(entry.id),
                "from_status": before,
                "to_status": status,
                "filled_sz": _s(entry.filled_sz),
                "order_sz": _s(entry.sz),
            },
            entry.correlation_id,
        )

    # -- the sweep: entry timeouts + restart recovery -------------------------------------------------

    def _due_entries(self, now: datetime) -> list[uuid.UUID]:
        cutoff = now - self.entry_timeout()
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(Order.id)
                    .where(
                        Order.env == self.env,
                        Order.origin == "atlas",
                        Order.purpose == "entry",
                        Order.ord_type.in_(TIMEOUT_ORD_TYPES),
                        Order.state.in_(OPEN_ENTRY_STATES),
                        Order.created_at <= cutoff,
                    )
                    .order_by(Order.created_at)
                )
            )

    def _unsettled_entries(self) -> list[uuid.UUID]:
        """Filled Atlas entries whose stored protection is not exactly one live stop of the filled size."""
        out: list[uuid.UUID] = []
        with Session(self.engine) as s:
            rows = s.scalars(
                select(Order).where(
                    Order.env == self.env,
                    Order.origin == "atlas",
                    Order.purpose == "entry",
                    Order.filled_sz > 0,
                    Order.state.in_(("partially_filled", "filled", "canceled")),
                    Order.risk_check_id.is_not(None),
                )
            ).all()
            for order in rows:
                assert order.risk_check_id is not None
                ids = [derived_id(p, order.risk_check_id) for p in PROTECT_PREFIXES]
                algos = list(s.scalars(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id.in_(ids))))
                has_close = (
                    s.scalar(
                        select(Order.id)
                        .where(Order.proposal_id == order.proposal_id, Order.purpose.in_(("close", "kill")))
                        .limit(1)
                    )
                    is not None
                )
                if self._protection_ended(algos, has_close) is not None:
                    continue
                live = [a for a in algos if a.state in ("new", "sending", "live")]
                if len(live) != 1 or Decimal(live[0].sz or 0) != Decimal(order.filled_sz):
                    out.append(order.id)
        return out

    async def sweep(self) -> dict[str, Any]:
        """Cancel entries past the timeout, then re-settle entries whose protection is out of step."""
        now = self.now()
        due = self._due_entries(now)
        unsettled = [i for i in self._unsettled_entries() if i not in due]
        if not due and not unsettled:
            return {"expired": [], "settled": []}
        okx = self._client()
        if okx is None:
            return {"expired": [], "settled": [], "error": "connection_not_verified"}
        expired: list[str] = []
        settled: list[str] = []
        async with okx:
            trade = OkxTradeClient(okx)
            for order_id in due:
                if await self.expire(order_id, trade):
                    expired.append(str(order_id))
            for order_id in unsettled:
                if await self.settle(order_id, trade) is not None:
                    settled.append(str(order_id))
        return {"expired": expired, "settled": settled}

    async def expire(self, order_id: uuid.UUID, trade: OkxTradeClient) -> bool:
        """Cancel one timed-out entry. True when OKX confirmed it is no longer open."""
        return (await self._end(order_id, trade, "timeout")).status == "cancelled"

    async def cancel(
        self,
        order_id: uuid.UUID,
        trade: OkxTradeClient,
        *,
        attempts: int = 1,
        backoff: Callable[[int], float] | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> EntryCancel:
        """The owner cancels one Atlas entry: the same steps as the timeout, under the same per-order
        lock. Nothing filled → its protection is cancelled and the proposal is ``cancelled``; a partial
        fill keeps one stop, amended to the filled size. The cancel request is tried ``attempts`` times."""
        return await self._end(order_id, trade, "owner", attempts=attempts, backoff=backoff, sleep=sleep)

    async def _end(
        self,
        order_id: uuid.UUID,
        trade: OkxTradeClient,
        reason: EndReason,
        *,
        attempts: int = 1,
        backoff: Callable[[int], float] | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> EntryCancel:
        async with self._lock(order_id):
            with Session(self.engine) as s:
                entry = _load_entry(s, order_id)
            if entry is None or entry.state not in OPEN_ENTRY_STATES:
                return EntryCancel("not_open")
            sent = await self._send_cancel(entry, trade, max(1, attempts), backoff, sleep)
            if sent.status == "failed":
                return sent
            detail = await self._lookup(entry, trade)
            if detail is None or detail.state in OPEN_ENTRY_STATES:
                return EntryCancel("unconfirmed", sent.attempts, message="OKX still shows the order open.")
            rec = _Recorder(self.engine, self.rds, entry.ctx, self.now)
            self._sync(rec, entry, detail)
            with Session(self.engine) as s:
                entry = _load_entry(s, order_id)
            assert entry is not None
            minutes = int(self.entry_timeout().total_seconds() // 60)
            ended = f"timed out after {minutes} min" if reason == "timeout" else "cancelled by the owner"
            result: ProtectResult | None = None
            if entry.filled_sz > 0:
                result = await self._settle(entry, trade)
                protected = (
                    f" The stop covers the filled {_s(entry.filled_sz)}."
                    if result.status in ("in_sync", "amended", "placed")
                    else ""
                )
                content = (
                    f"{entry.inst_id} entry {ended}: "
                    f"{_s(entry.filled_sz)} of {_s(entry.sz)} filled, the rest cancelled.{protected}"
                )
            else:
                await self._cancel_unfilled_protection(entry, trade, rec)
                self._proposal_status(entry, "expired" if reason == "timeout" else "cancelled")
                content = f"{entry.inst_id} entry {ended} with nothing filled; cancelled."
            payload: dict[str, Any] = {
                "order_id": str(entry.id),
                "proposal_id": str(entry.proposal_id),
                "cl_ord_id": entry.cl_ord_id,
                "ord_id": entry.ord_id,
                "inst_id": entry.inst_id,
                "side": entry.side,
                "sz": _s(entry.sz),
                "filled_sz": _s(entry.filled_sz),
                "state": entry.state,
            }
            if reason == "timeout":
                payload["timeout_minutes"] = minutes
                self._publish(ORDERS_STREAM, "order.expired", payload, entry.correlation_id)
                rec.warroom("order", content, {"type": "entry_expired", **payload})
            else:
                payload["protection"] = result.status if result is not None else None
                self._publish(ORDERS_STREAM, "order.cancelled", payload, entry.correlation_id)
                rec.warroom("order", content, {"type": "entry_cancelled", **payload})
            return EntryCancel(
                "cancelled", sent.attempts, sent.noop, sent.code, filled_sz=entry.filled_sz, protection=result
            )

    @staticmethod
    async def _send_cancel(
        entry: _Entry,
        trade: OkxTradeClient,
        attempts: int,
        backoff: Callable[[int], float] | None,
        sleep: Sleep,
    ) -> EntryCancel:
        """``trade/cancel-order`` by clOrdId, retried; OKX's "already gone" answers count as done."""
        code: str | None = None
        message = ""
        for attempt in range(1, attempts + 1):
            if attempt > 1 and backoff is not None:
                await sleep(backoff(attempt - 1))
            try:
                await trade.cancel_order(entry.inst_id, cl_ord_id=entry.cl_ord_id)
                return EntryCancel("cancelled", attempt)
            except OkxTradeError as err:
                if err.s_code in CANCEL_NOOP_CODES:
                    return EntryCancel("cancelled", attempt, noop=True, code=err.s_code)
                code, message = err.s_code, err.human_message
            except OkxClientError as err:
                code, message = None, "OKX could not be reached."
                log.warning("entry cancel failed", extra={"error": type(err).__name__})
            log.warning("entry cancel failed", extra={"code": code, "attempt": attempt})
        return EntryCancel("failed", attempts, code=code, message=message)

    async def _lookup(self, entry: _Entry, trade: OkxTradeClient) -> OrderDetail | None:
        try:
            return await trade.get_order(entry.inst_id, cl_ord_id=entry.cl_ord_id)
        except OkxTradeError as err:
            if err.s_code != ORDER_NOT_FOUND:
                log.warning("order lookup failed", extra={"code": err.s_code})
            return None
        except OkxClientError as err:
            log.warning("order lookup failed", extra={"error": type(err).__name__})
            return None

    @staticmethod
    def _sync(rec: _Recorder, entry: _Entry, detail: OrderDetail) -> None:
        state = _OKX_ORDER_STATES.get(detail.state, entry.state)
        fields: dict[str, Any] = {"ord_id": detail.ord_id, "raw": detail.model_dump(mode="json")}
        if detail.acc_fill_sz is not None and detail.acc_fill_sz >= entry.filled_sz:
            fields["filled_sz"] = detail.acc_fill_sz
        if detail.avg_px:
            fields["avg_px"] = detail.avg_px
        if detail.fee is not None:
            fields["fee"] = detail.fee
            fields["fee_ccy"] = detail.fee_ccy
        rec.order(entry.id, state, **fields)

    async def _cancel_unfilled_protection(self, entry: _Entry, trade: OkxTradeClient, rec: _Recorder) -> None:
        """Nothing filled: a stand-alone OCO placed for a bare entry would sit on OKX with no position."""
        try:
            pending = await trade.get_algo_orders_pending("conditional,oco", inst_id=entry.inst_id)
        except OkxClientError as err:
            log.warning("algo lookup failed", extra={"error": type(err).__name__})
            pending = []
        ours = [r for r in pending if r.algo_cl_ord_id in entry.protect_ids]
        done: set[str | None] = set()
        if ours:
            try:
                acks = await trade.cancel_algos([(entry.inst_id, r.algo_id) for r in ours])
                done = {a.algo_id for a in acks if a.ok}
            except OkxClientError as err:
                log.warning("cancel of unfilled protection failed", extra={"error": type(err).__name__})
        on_okx: set[str | None] = {r.algo_cl_ord_id for r in ours if r.algo_id not in done}
        for row in self._stored_protection(entry):
            if row.state in ("new", "sending", "live") and row.algo_cl_ord_id not in on_okx:
                assert row.algo_cl_ord_id is not None
                rec.algo(row.algo_cl_ord_id, "canceled")


def sweep_job(lifecycle: EntryLifecycle) -> Callable[[], Any]:
    """The scheduler job: ``lifecycle.sweep()``."""

    async def run() -> dict[str, Any]:
        return await lifecycle.sweep()

    return run


__all__ = ["JOB", "EntryCancel", "EntryLifecycle", "ProtectResult", "sweep_job"]
