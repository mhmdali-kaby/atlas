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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import redis
from atlas_shared import bus
from atlas_shared.db import Order
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.execution.config import ORDERS_STREAM, SOURCE, ExecutorConfig, derived_id, round_px
from trading_core.execution.okx_ops import Sleep, _Executor, _retryable
from trading_core.execution.plan import _plan, _Refusal
from trading_core.execution.recorder import _new_payload, _Recorder, _s
from trading_core.okx.client import OkxClient, RetryPolicy
from trading_core.okx.connection import client_for_connection
from trading_core.okx.errors import OkxClientError
from trading_core.okx.trade import MgnMode, OkxTradeClient, OkxTradeError, PosSide
from trading_core.okx.trade_models import OrderDetail

log = logging.getLogger("trading_core.execution")

Status = Literal["refused", "sent", "failed", "unprotected_closed"]
OkxFactory = Callable[..., OkxClient]


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


__all__ = [
    "ExecutionResult",
    "ExecutorConfig",
    "derived_id",
    "execute",
    "round_px",
]
