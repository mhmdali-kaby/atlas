"""The Executor (brief §1 rules 1, 3 and 8; §11): sends one approved entry to OKX with a server-side stop.

``execute(risk_check_id, approved_by)`` is the only function in Atlas that sends an entry order
(``trade/order``). Its single input is a ``risk_checks`` row written by the Risk Engine (ATL-127):

1. **Refusals (no HTTP request at all).** The risk check must exist, be for the active env, be the
   newest check of its proposal, have result ``approved`` or ``resized`` and a size and leverage. Its
   decision must be ``send_now``, or ``awaiting_owner`` with ``approved_by == "owner"``
   (``advise_only`` is never sent). The proposal must still be ``risk_checked``/``awaiting_owner``.
   The settings row is re-read: a version other than the one the check used, a paused env or an
   active circuit breaker refuses. The env needs a verified OKX connection (and a live key must not
   carry withdraw, decision #240).
2. **Leverage.** ``account/set-leverage`` at the check's leverage, ``mgnMode`` = settings
   ``executor.td_mode_perps`` for derivatives; spot uses ``tdMode`` cash and skips this step.
3. **Entry.** The ``clOrdId`` is derived from the risk check id (``"e"`` + its last 31 hex digits), so a
   retry or a restart reuses it. The order carries ``attachAlgoOrds`` with ``slTriggerPx`` = the
   proposal's stop and ``tpTriggerPx`` = its first take-profit (both market on trigger). Network
   errors, HTTP 5xx/429 and OKX's retryable codes are retried up to ``executor.order_max_retries``
   times with exponential backoff and the same body; before each retry the first attempt is looked up
   by ``clOrdId`` (``GET trade/order``) and adopted if OKX has it.
4. **Attach refused.** When OKX refuses the order with one of ``executor.attach_refused_codes``, the
   entry is placed bare (``clOrdId`` ``"f"`` + 31 hex) and protected at once by an OCO algo order
   (``"o"`` + 31 hex; a plain stop when the proposal has no take-profit).
5. **Stop check.** Within ``executor.stop_verify_seconds`` OKX must show the stop: an unfilled entry
   with its attached SL (no fail code), or, once anything is filled, a pending SL/OCO algo order for
   it. If no stop can be confirmed, the entry is cancelled, whatever filled is closed with a
   reduce-only market order (``"c"`` + 31 hex) and a system alert is raised.

Every order state change is written to ``orders``/``algo_orders`` and published as
``order.state_changed`` / ``algo_order.state_changed`` on ``atlas.orders`` (the same payload shape as
the private WebSocket, ATL-142). The War Room gets a ``kind: order`` message for the result (OKX's
refusal in plain English from ``trading_core.okx.error_messages``) and a ``system_alert`` when a
position was left without a confirmed stop. Nothing here withdraws or transfers (brief rule 5).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal, Protocol

import redis
from atlas_shared import bus
from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Instrument,
    Order,
    Proposal,
    RiskCheck,
    TradingState,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from trading_core.okx.client import OkxClient, RetryPolicy
from trading_core.okx.connection import client_for_connection
from trading_core.okx.errors import OkxClientError, OkxHttpError, OkxNetworkError, OkxResponseError
from trading_core.okx.trade import (
    AlgoOrderRequest,
    AttachedTpSl,
    MgnMode,
    OkxTradeClient,
    OkxTradeError,
    OrderRequest,
    PosSide,
)
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

log = logging.getLogger("trading_core.execution")

ORDERS_STREAM = "atlas.orders"
SYSTEM_STREAM = "atlas.system"
WARROOM_STREAM = "atlas.warroom"
SOURCE = "service:trading_core.executor"
WARROOM_SOURCE = "system:executor"
WARROOM_AUTHOR = "executor"
OWNER = "owner"

EXECUTABLE_RESULTS = frozenset({"approved", "resized"})
EXECUTABLE_PROPOSAL_STATUSES = frozenset({"risk_checked", "awaiting_owner"})
ORDER_NOT_FOUND = "51603"
# OKX answers to a cancel that has nothing left to cancel (already gone, cancelled or filled).
CANCEL_NOOP_CODES = frozenset({"51400", "51401", "51402"})
LONG_SHORT_MODE = "long_short_mode"
WITHDRAW = "withdraw"

Status = Literal["refused", "sent", "failed", "unprotected_closed"]
OkxFactory = Callable[..., OkxClient]
Sleep = Callable[[float], Awaitable[None]]


class _Creds(Protocol):
    env: str
    api_key: str
    api_secret: str
    passphrase: str


@dataclass(frozen=True)
class ExecutionResult:
    """What ``execute`` did. ``status`` refused = nothing was sent; ``reason`` is a stable code."""

    status: Status
    reason: str
    detail: str
    risk_check_id: str
    order_id: str | None = None
    cl_ord_id: str | None = None
    ord_id: str | None = None
    state: str | None = None
    stop_verified: bool = False
    okx_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "sent"

    def payload(self) -> dict[str, Any]:
        return {"ok": self.ok, **dataclasses.asdict(self)}


# --- configuration --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutorConfig:
    td_mode_perps: str
    stop_verify_seconds: float
    order_max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    poll_seconds: float
    attach_refused_codes: frozenset[str]
    retryable_codes: frozenset[str]

    @classmethod
    def from_settings(cls, data: Mapping[str, Any]) -> ExecutorConfig:
        d: Mapping[str, Any] = DEFAULTS["executor"]
        rest: Mapping[str, Any] = DEFAULTS["okx_rest"]

        def pick(key: str) -> Any:
            value = data.get(key)
            return d[key] if value is None else value

        return cls(
            td_mode_perps=str(pick("td_mode_perps")),
            stop_verify_seconds=float(pick("stop_verify_seconds")),
            order_max_retries=max(1, int(pick("order_max_retries"))),
            backoff_base_seconds=float(d["retry_backoff_base_seconds"]),
            backoff_max_seconds=float(d["retry_backoff_max_seconds"]),
            poll_seconds=float(d["stop_verify_poll_seconds"]),
            attach_refused_codes=frozenset(str(c) for c in d["attach_refused_codes"]),
            retryable_codes=frozenset(str(c) for c in [*rest["retryable_codes"], *rest["rate_limit_codes"]]),
        )

    def backoff(self, retry: int) -> float:
        """Delay before retry number ``retry`` (1-based): base x 2^(retry-1), capped."""
        return float(min(self.backoff_max_seconds, self.backoff_base_seconds * (2 ** (retry - 1))))


def derived_id(prefix: str, risk_check_id: uuid.UUID) -> str:
    """A 32-alphanumeric OKX client id fixed by the risk check: one letter + its last 31 hex digits."""
    if len(prefix) != 1 or not prefix.isalpha():
        raise ValueError("prefix must be one letter")
    return prefix + risk_check_id.hex[1:]


def round_px(px: Decimal, tick: Decimal | None, side: str) -> Decimal:
    """Round a price to ``tick``: down for a buy, up for a sell (a buy's stop never moves closer,
    a buy's limit never pays more; mirrored for a sell)."""
    if tick is None or tick <= 0:
        return px.normalize()
    steps = (px / tick).to_integral_value(rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)
    rounded = (steps * tick).normalize()
    return rounded if rounded > 0 else px.normalize()


# --- the plan (everything decided before the first HTTP request) ----------------------------------


@dataclass
class _Plan:
    env: str
    risk_check_id: uuid.UUID
    proposal_id: uuid.UUID
    correlation_id: str
    order_id: uuid.UUID
    attach_algo_row_id: uuid.UUID
    inst_id: str
    is_spot: bool
    side: str
    close_side: str
    ord_type: str
    td_mode: str
    pos_side: str | None
    px: Decimal | None
    sz: Decimal
    lever: int
    stop: Decimal
    tp1: Decimal | None
    cl_ord_id: str
    attach_id: str
    bare_id: str
    oco_id: str
    close_id: str
    resume: bool
    cfg: ExecutorConfig
    creds: Any = field(repr=False)


class _Refusal(Exception):
    def __init__(self, reason: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.extra = extra


def _first_tp(take_profits: Any) -> Decimal | None:
    if not isinstance(take_profits, list) or not take_profits:
        return None
    first = take_profits[0]
    raw = first.get("price") if isinstance(first, Mapping) else None
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except ArithmeticError:
        return None
    return value if value.is_finite() and value > 0 else None


def _entry_px(p: Proposal) -> Decimal | None:
    """The limit price: the proposal's entry, else the entry-zone bound the Risk Engine sized at."""
    if p.entry is not None:
        return Decimal(p.entry)
    zone = p.entry_zone
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        try:
            low, high = sorted(Decimal(str(v)) for v in zone)
        except ArithmeticError:
            return None
        return high if p.side == "buy" else low
    return None


def _plan(
    session: Session, rc_id: uuid.UUID, approved_by: str | None, active_env: str | None, at: datetime
) -> _Plan:
    rc = session.scalar(select(RiskCheck).where(RiskCheck.id == rc_id).with_for_update())
    if rc is None:
        raise _Refusal("risk_check_not_found", f"Risk check {rc_id} does not exist.")
    env = rc.env
    if active_env is not None and env != active_env:
        raise _Refusal("env_mismatch", f"Risk check is for {env}; trading_core runs {active_env}.")
    if rc.result not in EXECUTABLE_RESULTS:
        raise _Refusal("risk_rejected", f"The Risk Engine rejected this proposal: {rc.reason or ''}".strip())
    if rc.decision == "advise_only":
        raise _Refusal("advise_only", "Autonomy is set to advise only; nothing is sent.")
    if rc.decision == "awaiting_owner":
        if approved_by != OWNER:
            raise _Refusal("owner_approval_required", "This order needs the owner's approval first.")
    elif rc.decision != "send_now":
        raise _Refusal("decision_unknown", f"Risk decision {rc.decision!r} cannot be executed.")
    if rc.final_size is None or rc.final_size <= 0 or rc.leverage is None or rc.leverage < 1:
        raise _Refusal("size_missing", "The risk check has no approved size or leverage.")

    newer = session.scalar(
        select(RiskCheck.id)
        .where(RiskCheck.proposal_id == rc.proposal_id, RiskCheck.id != rc.id)
        .where(RiskCheck.created_at > rc.created_at)
        .limit(1)
    )
    if newer is not None:
        raise _Refusal("superseded", "A newer risk check exists for this proposal.")

    existing = session.scalar(
        select(Order).where(Order.risk_check_id == rc.id, Order.purpose == "entry").limit(1)
    )
    if existing is not None and existing.state not in ("new", "sending"):
        raise _Refusal(
            "already_executed",
            f"This risk check was already executed (order {existing.state}).",
            order_id=str(existing.id),
            cl_ord_id=existing.cl_ord_id,
            ord_id=existing.ord_id,
            state=existing.state,
        )

    proposal = session.scalar(select(Proposal).where(Proposal.id == rc.proposal_id).with_for_update())
    if proposal is None:
        raise _Refusal("proposal_not_found", "The proposal no longer exists.")
    if existing is None and proposal.status not in EXECUTABLE_PROPOSAL_STATUSES:
        raise _Refusal("proposal_not_executable", f"The proposal is {proposal.status}.")
    if proposal.kind != "entry" or proposal.side not in ("buy", "sell"):
        raise _Refusal("proposal_not_executable", "Only entry proposals with a side are executed here.")
    if proposal.stop is None or proposal.stop <= 0:
        raise _Refusal("stop_missing", "The proposal has no stop; nothing is sent without one.")

    settings = session.execute(
        text("SELECT version, data FROM settings WHERE env = :env"), {"env": env}
    ).first()
    if settings is None or not isinstance(settings[1], dict):
        raise _Refusal("settings_missing", f"No settings row for {env}.")
    if int(settings[0]) != int(rc.settings_version):
        raise _Refusal(
            "settings_changed",
            f"Settings changed since the risk check (v{rc.settings_version} → v{settings[0]}); re-run it.",
        )
    state = session.scalar(select(TradingState).where(TradingState.env == env))
    if state is not None and state.paused:
        raise _Refusal("trading_paused", f"Trading is paused{': ' + state.reason if state.reason else ''}.")
    breakers = state.breakers if state is not None and isinstance(state.breakers, dict) else {}
    active = sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active"))
    if active:
        raise _Refusal("breaker_active", f"Circuit breaker active: {', '.join(active)}.")

    conn = session.scalar(
        select(Connection).where(
            Connection.exchange == "okx", Connection.env == env, Connection.last_verified_at.is_not(None)
        )
    )
    if conn is None:
        raise _Refusal("connection_not_verified", f"No verified OKX {env} connection.")
    perms = conn.permissions if isinstance(conn.permissions, list) else []
    if env == "live" and WITHDRAW in [str(p).lower() for p in perms]:
        raise _Refusal("withdraw_permission_enabled", "The live key can withdraw funds; Atlas refuses it.")

    inst = session.scalar(
        select(Instrument).where(Instrument.env == env, Instrument.inst_id == proposal.inst_id)
    )
    if inst is None:
        raise _Refusal("instrument_unknown", f"{proposal.inst_id} is not in the {env} universe.")

    cfg = ExecutorConfig.from_settings(settings[1].get("executor") or settings[1])
    side = str(proposal.side)
    is_spot = inst.inst_type == "SPOT"
    ord_type = str(proposal.order_type or "limit")
    px: Decimal | None = None
    if ord_type != "market":
        raw_px = _entry_px(proposal)
        if raw_px is None:
            raise _Refusal("entry_missing", "A limit entry needs an entry price or zone.")
        px = round_px(raw_px, inst.tick_sz, side)
    tp1 = _first_tp(proposal.take_profits)
    pos_side = None
    if not is_spot and conn.position_mode == LONG_SHORT_MODE:
        pos_side = "long" if side == "buy" else "short"

    order_id = existing.id if existing is not None else uuid.uuid4()
    cl_ord_id = derived_id("e", rc.id)
    attach_id = derived_id("s", rc.id)
    attach_row = session.scalar(select(AlgoOrder.id).where(AlgoOrder.algo_cl_ord_id == attach_id))
    plan = _Plan(
        env=env,
        risk_check_id=rc.id,
        proposal_id=proposal.id,
        correlation_id=str(proposal.cycle_id) if proposal.cycle_id else str(proposal.id),
        order_id=order_id,
        attach_algo_row_id=attach_row or uuid.uuid4(),
        inst_id=proposal.inst_id,
        is_spot=is_spot,
        side=side,
        close_side="sell" if side == "buy" else "buy",
        ord_type=ord_type,
        td_mode="cash" if is_spot else cfg.td_mode_perps,
        pos_side=pos_side,
        px=px,
        sz=Decimal(rc.final_size).normalize(),
        lever=int(rc.leverage),
        stop=round_px(Decimal(proposal.stop), inst.tick_sz, side),
        tp1=round_px(tp1, inst.tick_sz, side) if tp1 is not None else None,
        cl_ord_id=existing.cl_ord_id if existing is not None and existing.cl_ord_id else cl_ord_id,
        attach_id=attach_id,
        bare_id=derived_id("f", rc.id),
        oco_id=derived_id("o", rc.id),
        close_id=derived_id("c", rc.id),
        resume=existing is not None,
        cfg=cfg,
        creds=_CredSnapshot(conn.env, conn.api_key, conn.api_secret, conn.passphrase),
    )
    if existing is None:
        session.add(
            Order(
                id=order_id,
                env=env,
                proposal_id=proposal.id,
                risk_check_id=rc.id,
                cl_ord_id=plan.cl_ord_id,
                inst_id=plan.inst_id,
                side=side,
                pos_side=pos_side,
                ord_type=ord_type,
                td_mode=plan.td_mode,
                px=px,
                sz=plan.sz,
                lever=None if is_spot else plan.lever,
                state="new",
                reduce_only=False,
                origin="atlas",
                purpose="entry",
                raw={},
                created_at=at,
                updated_at=at,
            )
        )
        session.flush()
        session.add(_algo_row(plan, plan.attach_algo_row_id, attach_id, "attached", at))
    return plan


@dataclass(frozen=True)
class _CredSnapshot:
    env: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    passphrase: str = field(repr=False)


def _algo_row(plan: _Plan, row_id: uuid.UUID, algo_cl_ord_id: str, how: str, at: datetime) -> AlgoOrder:
    oco = plan.tp1 is not None
    return AlgoOrder(
        id=row_id,
        env=plan.env,
        order_id=plan.order_id,
        algo_cl_ord_id=algo_cl_ord_id,
        inst_id=plan.inst_id,
        kind="oco" if oco else "sl",
        side=plan.close_side,
        pos_side=plan.pos_side,
        trigger_px=None if oco else plan.stop,
        sl_trigger_px=plan.stop if oco else None,
        tp_trigger_px=plan.tp1,
        ord_px=None,
        sz=plan.sz,
        state="new",
        reduce_only=True,
        origin="atlas",
        raw={"placed_as": how},
        created_at=at,
        updated_at=at,
    )


# --- persistence + events -------------------------------------------------------------------------

_OKX_ORDER_STATES = {
    "live": "live",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
}


class _Recorder:
    """Writes order/algo transitions and publishes them; one short transaction per write."""

    def __init__(self, engine: Engine, rds: redis.Redis, plan: _Plan, now: Callable[[], datetime]) -> None:
        self.engine = engine
        self.rds = rds
        self.plan = plan
        self.now = now

    def _publish(self, stream: str, type_: str, payload: dict[str, Any], source: str = SOURCE) -> None:
        envelope = EventEnvelope.model_validate(
            {
                "type": type_,
                "env": self.plan.env,
                "source": source,
                "correlation_id": self.plan.correlation_id,
                "payload": payload,
            }
        )
        try:
            bus.publish(stream, envelope, client=self.rds)
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": type_})

    def order(self, order_id: uuid.UUID, to_state: str, **fields: Any) -> None:
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = s.get(Order, order_id, with_for_update=True)
            if row is None:
                return
            before = row.state
            row.state = to_state
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = at
            payload = _order_payload(row)
        if before != to_state:
            self._publish(
                ORDERS_STREAM, "order.state_changed", {**payload, "from_state": before, "to_state": to_state}
            )

    def new_order(self, row: Order) -> None:
        with Session(self.engine) as s, s.begin():
            if s.scalar(select(Order.id).where(Order.cl_ord_id == row.cl_ord_id)) is None:
                s.add(row)
                s.flush()
                payload = _order_payload(row)
            else:
                return
        self._publish(
            ORDERS_STREAM,
            "order.state_changed",
            {**payload, "from_state": None, "to_state": payload["state"]},
        )

    def algo(self, cl_id: str, to_state: str, *, new: AlgoOrder | None = None, **fields: Any) -> None:
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = s.scalar(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == cl_id).with_for_update())
            if row is None:
                if new is None:
                    return
                s.add(new)
                s.flush()
                row = new
                before = None
            else:
                before = row.state
            row.state = to_state
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = at
            payload = {
                "algo_order_id": str(row.id),
                "order_id": str(row.order_id) if row.order_id else None,
                "algo_id": row.algo_id,
                "algo_cl_ord_id": row.algo_cl_ord_id,
                "inst_id": row.inst_id,
                "kind": row.kind,
                "side": row.side,
                "sl_trigger_px": _s(row.sl_trigger_px if row.kind == "oco" else row.trigger_px),
                "tp_trigger_px": _s(row.tp_trigger_px),
                "sz": _s(row.sz),
                "state": row.state,
                "origin": row.origin,
            }
        if before != to_state:
            self._publish(
                ORDERS_STREAM,
                "algo_order.state_changed",
                {**payload, "from_state": before, "to_state": to_state},
            )

    def proposal_sent(self) -> None:
        with Session(self.engine) as s, s.begin():
            row = s.get(Proposal, self.plan.proposal_id, with_for_update=True)
            if row is not None and row.status in EXECUTABLE_PROPOSAL_STATUSES:
                row.status = "sent"
                row.updated_at = self.now()

    def warroom(self, kind: str, content_md: str, card: dict[str, Any]) -> None:
        self._publish(
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

    def alert(self, code: str, detail: str, card: dict[str, Any]) -> None:
        self._publish(SYSTEM_STREAM, "system.alert", {"code": code, "detail": detail, **card})
        self.warroom("system_alert", detail, {"code": code, **card})


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _order_payload(row: Order) -> dict[str, Any]:
    return {
        "order_id": str(row.id),
        "cl_ord_id": row.cl_ord_id,
        "ord_id": row.ord_id,
        "inst_id": row.inst_id,
        "side": row.side,
        "pos_side": row.pos_side,
        "ord_type": row.ord_type,
        "state": row.state,
        "px": _s(row.px),
        "sz": _s(row.sz),
        "filled_sz": _s(row.filled_sz),
        "avg_px": _s(row.avg_px),
        "origin": row.origin,
        "purpose": row.purpose,
        "reduce_only": row.reduce_only,
        "proposal_id": str(row.proposal_id) if row.proposal_id else None,
        "risk_check_id": str(row.risk_check_id) if row.risk_check_id else None,
    }


# --- talking to OKX -------------------------------------------------------------------------------


def _retryable(err: Exception, cfg: ExecutorConfig) -> bool:
    if isinstance(err, (OkxNetworkError, OkxResponseError)):
        return True
    if isinstance(err, OkxHttpError):
        return err.status == 429 or err.status >= 500
    if isinstance(err, OkxTradeError):
        status = err.http_status
        return (status is not None and (status == 429 or status >= 500)) or err.s_code in cfg.retryable_codes
    return False


@dataclass
class _Placed:
    ord_id: str | None
    detail: OrderDetail | None = None


class _Executor:
    def __init__(
        self, plan: _Plan, trade: OkxTradeClient, rec: _Recorder, sleep: Sleep, mono: Callable[[], float]
    ):
        self.plan = plan
        self.cfg = plan.cfg
        self.trade = trade
        self.rec = rec
        self.sleep = sleep
        self.mono = mono

    async def lookup(self, cl_ord_id: str) -> OrderDetail | None:
        """The order OKX holds under ``cl_ord_id``, or None (absent, or OKX could not say)."""
        try:
            return await self.trade.get_order(self.plan.inst_id, cl_ord_id=cl_ord_id)
        except OkxTradeError as err:
            if err.s_code != ORDER_NOT_FOUND:
                log.warning("order lookup failed", extra={"code": err.s_code})
            return None
        except OkxClientError as err:
            log.warning("order lookup failed", extra={"error": type(err).__name__})
            return None

    async def place(self, req: OrderRequest) -> _Placed:
        """Send ``req`` with up to ``order_max_retries`` attempts and the same clOrdId; before every
        retry ask OKX whether the previous attempt landed. Raises the last error when it did not."""
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
                found = await self.lookup(req.cl_ord_id)
                if found is not None:
                    return _Placed(found.ord_id, found)
            try:
                ack = await self.trade.place_order(req)
                return _Placed(ack.ord_id or None)
            except OkxTradeError as err:
                if err.duplicate_cl_ord_id:
                    found = await self.lookup(req.cl_ord_id)
                    if found is not None:
                        return _Placed(found.ord_id, found)
                    last = err
                    continue
                if not _retryable(err, self.cfg):
                    raise
                last = err
            except OkxClientError as err:
                if not _retryable(err, self.cfg):
                    raise
                last = err
            log.warning("order attempt failed", extra={"attempt": attempt, "error": type(last).__name__})
        found = await self.lookup(req.cl_ord_id)
        if found is not None:
            return _Placed(found.ord_id, found)
        assert last is not None
        raise last

    async def idempotent(self, call: Callable[[], Awaitable[Any]]) -> Any:
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
            try:
                return await call()
            except OkxClientError as err:
                if not _retryable(err, self.cfg):
                    raise
                last = err
        assert last is not None
        raise last

    def entry_request(self, cl_ord_id: str, attach: bool) -> OrderRequest:
        p = self.plan
        attached: tuple[AttachedTpSl, ...] = ()
        if attach:
            attached = (
                AttachedTpSl(
                    sl_trigger_px=p.stop,
                    tp_trigger_px=p.tp1,
                    attach_algo_cl_ord_id=p.attach_id,
                ),
            )
        return OrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.side,  # type: ignore[arg-type]
            ord_type=p.ord_type,  # type: ignore[arg-type]
            sz=p.sz,
            cl_ord_id=cl_ord_id,
            px=p.px,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            attach_algo_ords=attached,
        )

    async def protect_with_algo(self) -> AlgoOrderDetail | None:
        """Place the stand-alone OCO (or SL-only conditional) for a bare entry, retrying with the same
        algoClOrdId. Returns None when OKX refused it."""
        p = self.plan
        oco = p.tp1 is not None
        req = AlgoOrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.close_side,  # type: ignore[arg-type]
            ord_type="oco" if oco else "conditional",
            algo_cl_ord_id=p.oco_id,
            sz=p.sz,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            reduce_only=True,
            sl_trigger_px=p.stop,
            tp_trigger_px=p.tp1,
        )
        at = self.rec.now()
        self.rec.algo(p.oco_id, "sending", new=_algo_row(p, uuid.uuid4(), p.oco_id, "order-algo", at))
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
                if (found := await self.find_algo(p.oco_id)) is not None:
                    self.rec.algo(p.oco_id, "live", algo_id=found.algo_id)
                    return found
            try:
                ack = await self.trade.place_algo_order(req)
                self.rec.algo(p.oco_id, "live", algo_id=ack.algo_id)
                return None
            except OkxClientError as err:
                last = err
                if not _retryable(err, self.cfg):
                    break
        code = last.s_code if isinstance(last, OkxTradeError) else None
        msg = last.human_message if isinstance(last, OkxTradeError) else str(last)
        self.rec.algo(p.oco_id, "failed", last_error_code=code, last_error_msg=msg)
        return None

    async def find_algo(self, algo_cl_ord_id: str, ord_id: str | None = None) -> AlgoOrderDetail | None:
        """A pending SL/OCO algo on OKX carrying a stop for this entry, or None."""
        try:
            rows = await self.trade.get_algo_orders_pending("conditional,oco", inst_id=self.plan.inst_id)
        except OkxClientError as err:
            log.warning("algo lookup failed", extra={"error": type(err).__name__})
            return None
        for row in rows:
            ours = row.algo_cl_ord_id == algo_cl_ord_id or (ord_id is not None and row.ord_id == ord_id)
            if (
                ours
                and row.sl_trigger_px is not None
                and row.state in ("live", "effective", "partially_effective")
            ):
                return row
        return None

    async def stop_present(
        self, cl_ord_id: str, algo_cl_ord_id: str, attached: bool
    ) -> tuple[bool, OrderDetail | None]:
        detail = await self.lookup(cl_ord_id)
        filled = (detail.acc_fill_sz or Decimal(0)) if detail is not None else Decimal(0)
        algo = await self.find_algo(algo_cl_ord_id, detail.ord_id if detail is not None else None)
        if algo is not None:
            self.rec.algo(algo_cl_ord_id, "live", algo_id=algo.algo_id)
            return True, detail
        if attached and detail is not None and detail.state in ("live", "partially_filled"):
            # Not yet (fully) filled: OKX holds the stop on the order itself until the fill.
            for a in detail.attach_algo_ords:
                if (
                    a.sl_trigger_px is not None
                    and not a.fail_code
                    and (filled == 0 or detail.state == "partially_filled")
                ):
                    if a.attach_algo_id:
                        self.rec.algo(algo_cl_ord_id, "live", algo_id=a.attach_algo_id)
                    else:
                        self.rec.algo(algo_cl_ord_id, "live")
                    return True, detail
        return False, detail

    async def verify(
        self, cl_ord_id: str, algo_cl_ord_id: str, attached: bool
    ) -> tuple[bool, OrderDetail | None]:
        deadline = self.mono() + self.cfg.stop_verify_seconds
        while True:
            ok, detail = await self.stop_present(cl_ord_id, algo_cl_ord_id, attached)
            if ok:
                return True, detail
            if detail is not None and detail.state in ("canceled", "mmp_canceled") and not detail.acc_fill_sz:
                return False, detail
            if self.mono() >= deadline:
                return False, detail
            await self.sleep(self.cfg.poll_seconds)

    async def close_unprotected(self, cl_ord_id: str, detail: OrderDetail | None) -> Decimal:
        """Cancel what is left of the entry and market-close (reduce-only) whatever filled."""
        p = self.plan
        if detail is None or detail.state in ("live", "partially_filled"):
            try:
                await self.trade.cancel_order(p.inst_id, cl_ord_id=cl_ord_id)
            except OkxTradeError as err:
                if err.s_code not in CANCEL_NOOP_CODES:
                    log.warning("cancel of unprotected entry failed", extra={"code": err.s_code})
            except OkxClientError as err:
                log.warning("cancel of unprotected entry failed", extra={"error": type(err).__name__})
            detail = await self.lookup(cl_ord_id) or detail
        if detail is not None:
            self.sync_from_detail(p.order_id, detail)
        # If OKX could not tell us, close the whole size: reduce-only can never open a position.
        filled = (detail.acc_fill_sz or Decimal(0)) if detail is not None else p.sz
        if filled <= 0:
            return Decimal(0)
        at = self.rec.now()
        close_id = uuid.uuid4()
        close_row = Order(
            id=close_id,
            env=p.env,
            proposal_id=p.proposal_id,
            risk_check_id=p.risk_check_id,
            cl_ord_id=p.close_id,
            inst_id=p.inst_id,
            side=p.close_side,
            pos_side=p.pos_side,
            ord_type="market",
            td_mode=p.td_mode,
            sz=filled,
            state="new",
            reduce_only=True,
            origin="atlas",
            purpose="close",
            raw={"reason": "stop_unverified"},
            created_at=at,
            updated_at=at,
        )
        self.rec.new_order(close_row)
        with Session(self.rec.engine) as s:
            close_id = s.scalar(select(Order.id).where(Order.cl_ord_id == p.close_id)) or close_id
        self.rec.order(close_id, "sending")
        req = OrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.close_side,  # type: ignore[arg-type]
            ord_type="market",
            sz=filled,
            cl_ord_id=p.close_id,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            reduce_only=True,
        )
        try:
            placed = await self.place(req)
            self.rec.order(close_id, "live", ord_id=placed.ord_id)
        except OkxClientError as err:
            code = err.s_code if isinstance(err, OkxTradeError) else None
            msg = err.human_message if isinstance(err, OkxTradeError) else str(err)
            self.rec.order(close_id, "failed", last_error_code=code, last_error_msg=msg)
        return filled

    def sync_from_detail(self, order_id: uuid.UUID, detail: OrderDetail) -> str:
        state = _OKX_ORDER_STATES.get(detail.state, "live")
        fields: dict[str, Any] = {"ord_id": detail.ord_id, "raw": detail.model_dump(mode="json")}
        if detail.acc_fill_sz is not None:
            fields["filled_sz"] = detail.acc_fill_sz
        if detail.avg_px:
            fields["avg_px"] = detail.avg_px
        if detail.fee is not None:
            fields["fee"] = detail.fee
            fields["fee_ccy"] = detail.fee_ccy
        self.rec.order(order_id, state, **fields)
        return state


async def _run(ex: _Executor) -> ExecutionResult:
    p = ex.plan
    rec = ex.rec
    rc = str(p.risk_check_id)
    card = {
        "order_id": str(p.order_id),
        "proposal_id": str(p.proposal_id),
        "risk_check_id": rc,
        "inst_id": p.inst_id,
        "side": p.side,
        "ord_type": p.ord_type,
        "sz": _s(p.sz),
        "px": _s(p.px),
        "stop": _s(p.stop),
        "tp1": _s(p.tp1),
        "leverage": None if p.is_spot else p.lever,
        "td_mode": p.td_mode,
    }

    def result(status: Status, reason: str, detail: str, **kw: Any) -> ExecutionResult:
        return ExecutionResult(
            status=status, reason=reason, detail=detail, risk_check_id=rc, order_id=str(p.order_id), **kw
        )

    def refused_by_okx(err: OkxClientError, cl_ord_id: str) -> ExecutionResult:
        code = err.s_code if isinstance(err, OkxTradeError) else None
        message = (
            err.human_message
            if isinstance(err, OkxTradeError)
            else ("OKX could not be reached, so the order was not confirmed. Check Orders before retrying.")
        )
        rejected = isinstance(err, OkxTradeError) and not _retryable(err, ex.cfg)
        state = "rejected" if rejected else "failed"
        rec.order(p.order_id, state, last_error_code=code, last_error_msg=message)
        rec.warroom(
            "order",
            f"Order not placed for {p.inst_id}: {message}",
            {**card, "cl_ord_id": cl_ord_id, "state": state, "okx_code": code, "error": message},
        )
        return result(
            "failed",
            "okx_refused" if rejected else "okx_unreachable",
            message,
            cl_ord_id=cl_ord_id,
            state=state,
            okx_code=code,
        )

    existing: OrderDetail | None = None
    if p.resume:
        existing = await ex.lookup(p.cl_ord_id)
    attached = p.cl_ord_id == derived_id("e", p.risk_check_id)
    cl_ord_id = p.cl_ord_id
    algo_cl = p.attach_id if attached else p.oco_id

    if existing is None:
        if not p.is_spot:
            try:
                mgn: MgnMode = "cross" if p.td_mode == "cross" else "isolated"
                lev_pos: PosSide | None = (
                    "long" if p.pos_side == "long" else "short" if p.pos_side == "short" else None
                )
                await ex.idempotent(
                    lambda: ex.trade.set_leverage(
                        p.inst_id, p.lever, mgn, pos_side=lev_pos if mgn == "isolated" else None
                    )
                )
            except OkxClientError as err:
                return refused_by_okx(err, cl_ord_id)
        rec.order(p.order_id, "sending")
        try:
            placed = await ex.place(ex.entry_request(cl_ord_id, attach=attached))
        except OkxTradeError as err:
            if not (attached and err.s_code in ex.cfg.attach_refused_codes):
                rec.algo(p.attach_id, "failed", last_error_code=err.s_code, last_error_msg=err.human_message)
                return refused_by_okx(err, cl_ord_id)
            # OKX will not attach a TP/SL here: send the entry bare and protect it with an OCO.
            rec.algo(p.attach_id, "failed", last_error_code=err.s_code, last_error_msg=err.human_message)
            attached, cl_ord_id, algo_cl = False, p.bare_id, p.oco_id
            rec.order(p.order_id, "sending", cl_ord_id=cl_ord_id, raw={"attach_refused": err.s_code})
            try:
                placed = await ex.place(ex.entry_request(cl_ord_id, attach=False))
            except OkxClientError as err2:
                return refused_by_okx(err2, cl_ord_id)
        except OkxClientError as err:
            return refused_by_okx(err, cl_ord_id)
        rec.order(p.order_id, "live", ord_id=placed.ord_id)
        if placed.detail is not None:
            ex.sync_from_detail(p.order_id, placed.detail)
        rec.proposal_sent()
        if not attached:
            await ex.protect_with_algo()
    else:
        ex.sync_from_detail(p.order_id, existing)
        rec.proposal_sent()
        if not attached and await ex.find_algo(p.oco_id, existing.ord_id) is None:
            await ex.protect_with_algo()

    ok, detail = await ex.verify(cl_ord_id, algo_cl, attached)
    state = ex.sync_from_detail(p.order_id, detail) if detail is not None else None
    ord_id = detail.ord_id if detail is not None else None
    if ok:
        how = "attached to the order" if attached else "as a separate OCO order"
        rec.warroom(
            "order",
            f"Sent {p.side} {_s(p.sz)} {p.inst_id} ({p.ord_type}"
            + (f" at {_s(p.px)}" if p.px is not None else "")
            + f"). Stop {_s(p.stop)} confirmed on OKX {how}.",
            {**card, "cl_ord_id": cl_ord_id, "ord_id": ord_id, "state": state, "stop_verified": True},
        )
        return result(
            "sent",
            "sent",
            "Order sent; stop confirmed on OKX.",
            cl_ord_id=cl_ord_id,
            ord_id=ord_id,
            state=state,
            stop_verified=True,
        )

    if detail is not None and detail.state in ("canceled", "mmp_canceled") and not detail.acc_fill_sz:
        rec.warroom(
            "order",
            f"Order for {p.inst_id} was cancelled by OKX before it filled; no position.",
            {**card, "cl_ord_id": cl_ord_id, "ord_id": ord_id, "state": "canceled"},
        )
        return result(
            "failed",
            "entry_canceled",
            "OKX cancelled the entry before any fill.",
            cl_ord_id=cl_ord_id,
            ord_id=ord_id,
            state="canceled",
        )

    closed = await ex.close_unprotected(cl_ord_id, detail)
    message = (
        f"No stop-loss could be confirmed on OKX for {p.inst_id} within {ex.cfg.stop_verify_seconds:g} s. "
        + (
            f"Atlas cancelled the entry and closed {_s(closed)} with a reduce-only market order."
            if closed > 0
            else "Atlas cancelled the entry; nothing had filled."
        )
    )
    rec.alert(
        "stop_unverified",
        message,
        {**card, "cl_ord_id": cl_ord_id, "ord_id": ord_id, "closed_sz": _s(closed)},
    )
    with Session(rec.engine) as s:
        final_state = s.scalar(select(Order.state).where(Order.id == p.order_id))
    return result(
        "unprotected_closed",
        "stop_unverified",
        message,
        cl_ord_id=cl_ord_id,
        ord_id=ord_id,
        state=final_state,
    )


def _default_factory(app_key: str) -> OkxFactory:
    def build(creds: _Creds, **kwargs: Any) -> OkxClient:
        return client_for_connection(creds, app_key, **kwargs)

    return build


async def execute(
    risk_check_id: uuid.UUID | str,
    approved_by: str | None = None,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    active_env: str | None = None,
    sleep: Sleep = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] | None = None,
) -> ExecutionResult:
    """Send the order a risk check approved, with a server-side stop, or refuse without touching OKX.

    ``client_factory(connection, retry=RetryPolicy)`` builds the OkxClient (default: decrypt the
    stored connection with ``app_key``). The client gets one attempt per call; the Executor runs the
    retries itself so it can look the order up between them.
    """
    clock = now or (lambda: datetime.now(UTC))
    try:
        rc_id = risk_check_id if isinstance(risk_check_id, uuid.UUID) else uuid.UUID(str(risk_check_id))
    except ValueError:
        return ExecutionResult("refused", "risk_check_not_found", "Not a risk check id.", str(risk_check_id))
    rds = redis_client if redis_client is not None else bus.make_client()
    try:
        with Session(engine) as session, session.begin():
            plan = _plan(session, rc_id, approved_by, active_env, clock())
    except _Refusal as refusal:
        extra = {k: v for k, v in refusal.extra.items() if k in ("order_id", "cl_ord_id", "ord_id", "state")}
        result = ExecutionResult("refused", refusal.reason, refusal.detail, str(rc_id), **extra)
        try:
            bus.publish(
                ORDERS_STREAM,
                EventEnvelope.model_validate(
                    {
                        "type": "order.refused",
                        "env": active_env or DEFAULTS["env"]["default"],
                        "source": SOURCE,
                        "payload": result.payload(),
                    }
                ),
                client=rds,
            )
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": "order.refused"})
        return result

    rec = _Recorder(engine, rds, plan, clock)
    if not plan.resume:
        rec._publish(
            ORDERS_STREAM,
            "order.state_changed",
            {**_new_payload(engine, plan.order_id), "from_state": None, "to_state": "new"},
        )
    factory = client_factory or _default_factory(app_key)
    retry = dataclasses.replace(RetryPolicy.from_defaults(), max_attempts=1)
    okx = factory(plan.creds, retry=retry)
    async with okx:
        return await _run(_Executor(plan, OkxTradeClient(okx), rec, sleep, monotonic))


def _new_payload(engine: Engine, order_id: uuid.UUID) -> dict[str, Any]:
    with Session(engine) as s:
        row = s.get(Order, order_id)
        return _order_payload(row) if row is not None else {"order_id": str(order_id)}


__all__ = [
    "ExecutionResult",
    "ExecutorConfig",
    "derived_id",
    "execute",
    "round_px",
]
