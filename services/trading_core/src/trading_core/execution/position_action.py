"""The Executor for an approved Portfolio Manager position action (ATL-185, brief §7.8, §11, rule 3).

``execute`` hands every risk check whose proposal has ``kind`` in ``amend_stop``, ``amend_targets``,
``partial_close`` or ``close`` to :func:`execute_position_action`. The flow:

1. **Refusals (no HTTP request).** The same gates as an entry: the risk check exists, is for the
   active env, is the newest of its proposal, is ``approved`` with decision ``send_now`` (or
   ``awaiting_owner`` and ``approved_by == "owner"``), the proposal is still
   ``risk_checked``/``awaiting_owner``, the settings version is unchanged and the env has a verified
   connection (a live key with withdraw is refused).
2. **Re-check against OKX right before sending.** The open position and the pending algo orders are
   read from OKX and the seven ATL-184 checks (:func:`run_position_checks`) run again on them: the
   live stop OKX holds now is the bar a new stop must tighten, and close sizes come from the position
   OKX holds now. A failure sends nothing: a ``risk_checks`` row (result ``rejected``) is written,
   the proposal becomes ``rejected`` / ``risk_rejected`` and ``proposal.rejected`` is published.
3. **The action.**

   - ``amend_stop``: every live stop (SL or OCO) protecting the position is amended in place to the
     new trigger (``trade/amend-algos`` ``newSlTriggerPx``); nothing new is placed. Only when OKX
     shows no stop at all is a stand-alone SL (``n`` + 31 hex) placed for the whole position.
   - ``amend_targets``: the TP leg moves to the first target. A protecting OCO is amended
     (``newTpTriggerPx``, its stop untouched); without one a TP-only order (``t`` + 31 hex) is placed
     for the whole position. Older TP-only orders on the position are cancelled. The stop is never
     touched.
   - ``partial_close``: one reduce-only market order (``x`` + 31 hex) for the checked contracts, then
     every protecting algo larger than the remainder is amended to the remainder (``newSz`` only).
   - ``close``: a reduce-only ``trade/close-position`` (``x`` + 31 hex), then that position's
     remaining algo orders are cancelled. A close OKX did not take keeps every stop.

   The client ids are derived from the risk check, so a retry (in this run, or a later run after a
   crash left the order row ``new``/``sending``) reuses them; before each retry the order is looked
   up by ``clOrdId`` and adopted if OKX already has it.
4. **Record.** Orders (``purpose = close``, ``raw.reduces_order_id`` = the Atlas entry the position
   came from, so ``EntryLifecycle`` sizes that entry's protection to what is left) and algo orders are
   written and published like the entry Executor's. The proposal moves to ``filled`` (amends, and a
   filled close) or ``sent`` (a close OKX took but has not reported filled); a PM ``tp1_partial`` in
   either status is what the Portfolio Manager reads as ``tp1_filled`` (ATL-169). The War Room gets a
   ``kind: order`` line with a ``position_action`` card.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Any

import redis
from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Instrument,
    Order,
    Proposal,
    RiskCheck,
    TradingState,
)
from atlas_shared.proposals import POSITION_ACTION_KINDS
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from trading_core.approvals.common import PROPOSALS_STREAM, proposal_payload
from trading_core.approvals.common import publish as publish_proposal
from trading_core.execution.config import (
    EXECUTABLE_PROPOSAL_STATUSES,
    EXECUTABLE_RESULTS,
    ORDER_NOT_FOUND,
    ORDERS_STREAM,
    OWNER,
    SOURCE,
    WITHDRAW,
    ExecutorConfig,
    derived_id,
    round_px,
    round_stop,
)
from trading_core.execution.control import Control, has_stop
from trading_core.execution.control import ref as algo_ref
from trading_core.execution.okx_access import CredSnapshot, default_factory, single_attempt
from trading_core.execution.okx_ops import _retryable
from trading_core.execution.recorder import _OKX_ORDER_STATES, _Recorder, _s
from trading_core.okx.errors import OkxClientError
from trading_core.okx.models import Position
from trading_core.okx.trade import AlgoOrderRequest, OkxTradeClient, OkxTradeError, OrderRequest
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail
from trading_core.risk.position_actions import (
    ActionContext,
    ActionOutcome,
    HeldPosition,
    LiveStop,
    action_info,
    load_action_context,
    run_position_checks,
    settings_row,
)

if TYPE_CHECKING:
    from trading_core.execution.executor import ExecutionResult, OkxFactory

log = logging.getLogger("trading_core.execution.position_action")

POSITION_KINDS = frozenset(POSITION_ACTION_KINDS)
CLOSE_KINDS = frozenset({"partial_close", "close"})
NO_POSITION_CODE = "51023"
CLOSE_PREFIX = "x"
NEW_STOP_PREFIX = "n"
NEW_TP_PREFIX = "t"
# The proposal statuses that mean "carried out" (the PM reads tp1_filled from these, ATL-169).
DONE = "filled"
SENT = "sent"

Sleep = Callable[[float], Awaitable[None]]


class _Refusal(Exception):
    def __init__(self, reason: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.extra = extra


@dataclass
class _ActionPlan:
    env: str
    risk_check_id: uuid.UUID
    proposal_id: uuid.UUID
    correlation_id: str
    kind: str
    inst_id: str
    stop: Decimal | None
    targets: tuple[Decimal, ...]
    tick_sz: Decimal | None
    settings_version: int
    ctx: ActionContext
    cl_ord_id: str
    order_id: uuid.UUID | None
    resume: bool
    cfg: ExecutorConfig
    creds: Any = field(repr=False)


def proposal_kind(engine: Engine, risk_check_id: uuid.UUID) -> str | None:
    """The kind of the proposal a risk check belongs to (None when either row is missing)."""
    with Session(engine) as s:
        return s.scalar(
            select(Proposal.kind)
            .join(RiskCheck, RiskCheck.proposal_id == Proposal.id)
            .where(RiskCheck.id == risk_check_id)
        )


def _plan_action(
    session: Session,
    rds: redis.Redis,
    rc_id: uuid.UUID,
    approved_by: str | None,
    active_env: str | None,
    at: datetime,
) -> _ActionPlan:
    rc = session.scalar(select(RiskCheck).where(RiskCheck.id == rc_id).with_for_update())
    if rc is None:
        raise _Refusal("risk_check_not_found", f"Risk check {rc_id} does not exist.")
    env = rc.env
    if active_env is not None and env != active_env:
        raise _Refusal("env_mismatch", f"Risk check is for {env}; trading_core runs {active_env}.")
    if rc.result not in EXECUTABLE_RESULTS:
        raise _Refusal("risk_rejected", f"The Risk Engine rejected this action: {rc.reason or ''}".strip())
    if rc.decision == "advise_only":
        raise _Refusal("advise_only", "Autonomy is set to advise only; nothing is sent.")
    if rc.decision == "awaiting_owner":
        if approved_by != OWNER:
            raise _Refusal("owner_approval_required", "This action needs the owner's approval first.")
    elif rc.decision != "send_now":
        raise _Refusal("decision_unknown", f"Risk decision {rc.decision!r} cannot be executed.")

    newer = session.scalar(
        select(RiskCheck.id)
        .where(RiskCheck.proposal_id == rc.proposal_id, RiskCheck.id != rc.id)
        .where(RiskCheck.created_at > rc.created_at)
        .limit(1)
    )
    if newer is not None:
        raise _Refusal("superseded", "A newer risk check exists for this proposal.")

    proposal = session.scalar(select(Proposal).where(Proposal.id == rc.proposal_id).with_for_update())
    if proposal is None:
        raise _Refusal("proposal_not_found", "The proposal no longer exists.")
    if proposal.kind not in POSITION_KINDS:
        raise _Refusal("proposal_not_executable", "Only position actions are executed here.")

    cl_ord_id = derived_id(CLOSE_PREFIX, rc.id)
    existing = session.scalar(select(Order).where(Order.cl_ord_id == cl_ord_id))
    if existing is not None and existing.state not in ("new", "sending"):
        raise _Refusal(
            "already_executed",
            f"This risk check was already executed (order {existing.state}).",
            order_id=str(existing.id),
            cl_ord_id=existing.cl_ord_id,
            ord_id=existing.ord_id,
            state=existing.state,
        )
    if existing is None and proposal.status not in EXECUTABLE_PROPOSAL_STATUSES:
        raise _Refusal("proposal_not_executable", f"The proposal is {proposal.status}.")
    if proposal.kind in CLOSE_KINDS and (rc.final_size is None or rc.final_size <= 0):
        raise _Refusal("size_missing", "The risk check has no approved close size.")
    if proposal.kind == "amend_stop" and (proposal.stop is None or proposal.stop <= 0):
        raise _Refusal("stop_missing", "An amend_stop needs the new stop.")

    settings = settings_row(session, env)
    if settings is None:
        raise _Refusal("settings_missing", f"No settings row for {env}.")
    if settings[0] != int(rc.settings_version):
        raise _Refusal(
            "settings_changed",
            f"Settings changed since the risk check (v{rc.settings_version} → v{settings[0]}); re-run it.",
        )
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

    state = session.scalar(select(TradingState).where(TradingState.env == env))
    breakers = state.breakers if state is not None and isinstance(state.breakers, dict) else {}
    active = tuple(sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active")))
    ctx = load_action_context(
        session,
        rds,
        proposal,
        at,
        settings_version=settings[0],
        settings_data=settings[1],
        paused=bool(state is not None and state.paused),
        pause_reason=state.reason if state is not None else None,
        active_breakers=active,
    )
    info = action_info(proposal)
    return _ActionPlan(
        env=env,
        risk_check_id=rc.id,
        proposal_id=proposal.id,
        correlation_id=str(proposal.cycle_id) if proposal.cycle_id else str(proposal.id),
        kind=proposal.kind,
        inst_id=proposal.inst_id,
        stop=info.stop,
        targets=info.targets,
        tick_sz=inst.tick_sz,
        settings_version=settings[0],
        ctx=ctx,
        cl_ord_id=cl_ord_id,
        order_id=existing.id if existing is not None else None,
        resume=existing is not None,
        cfg=ExecutorConfig.from_settings(settings[1].get("executor") or settings[1]),
        creds=CredSnapshot.of(conn),
    )


# --- OKX → the re-check's inputs --------------------------------------------------------------------


def _held(p: Position, at: datetime) -> HeldPosition:
    pos = p.pos or Decimal(0)
    if p.pos_side == "long":
        direction = 1
    elif p.pos_side == "short":
        direction = -1
    else:
        direction = 1 if pos > 0 else -1
    return HeldPosition(p.inst_id, p.pos_side or "net", direction, abs(pos), p.avg_px, p.mark_px, at)


def _live_stop(a: AlgoOrderDetail) -> LiveStop | None:
    if not has_stop(a) or a.sl_trigger_px is None:
        return None
    kind = "oco" if a.ord_type == "oco" else "sl"
    return LiveStop(a.algo_id, a.algo_cl_ord_id, kind, a.sl_trigger_px, a.pos_side, a.side)


def _protects(a: AlgoOrderDetail, pos: HeldPosition) -> bool:
    """Same matching rule as the Risk Engine's live stops (pos_side, then the closing side)."""
    closing = "sell" if pos.direction > 0 else "buy"
    if pos.pos_side in ("long", "short"):
        if a.pos_side != pos.pos_side:
            return False
    elif a.pos_side not in (None, "", "net"):
        return False
    return not (a.side and a.side != closing)


def _okx_context(
    base: ActionContext, positions: Sequence[Position], algos: Sequence[AlgoOrderDetail], at: datetime
) -> ActionContext:
    held = tuple(_held(p, at) for p in positions)
    stops = tuple(s for s in (_live_stop(a) for a in algos) if s is not None)
    mark, source = base.mark, base.mark_source
    if len(held) == 1 and held[0].mark_px:
        mark, source = held[0].mark_px, "okx_position"
    return dataclasses.replace(
        base, positions=held, positions_taken_at=at, live_stops=stops, mark=mark, mark_source=source
    )


def _lot_floor(value: Decimal, lot: Decimal | None) -> Decimal:
    if lot is None or lot <= 0:
        return value
    return ((value / lot).to_integral_value(rounding=ROUND_FLOOR) * lot).normalize()


# --- the run ----------------------------------------------------------------------------------------


@dataclass
class _Run:
    plan: _ActionPlan
    ctl: Control
    trade: OkxTradeClient
    rec: _Recorder
    engine: Engine
    rds: redis.Redis
    sleep: Sleep
    now: Callable[[], datetime]
    card: dict[str, Any] = field(default_factory=dict)
    td_mode: str = "isolated"

    @property
    def cfg(self) -> ExecutorConfig:
        return self.plan.cfg

    def result(self, status: str, reason: str, detail: str, **kw: Any) -> ExecutionResult:
        from trading_core.execution.executor import ExecutionResult

        return ExecutionResult(
            status=status,  # type: ignore[arg-type]
            reason=reason,
            detail=detail,
            risk_check_id=str(self.plan.risk_check_id),
            **kw,
        )

    # -- OKX helpers ---------------------------------------------------------------------------------

    async def lookup(self, cl_ord_id: str) -> OrderDetail | None:
        try:
            return await self.trade.get_order(self.plan.inst_id, cl_ord_id=cl_ord_id)
        except OkxTradeError as err:
            if err.s_code != ORDER_NOT_FOUND:
                log.warning("order lookup failed", extra={"code": err.s_code})
            return None
        except OkxClientError as err:
            log.warning("order lookup failed", extra={"error": type(err).__name__})
            return None

    async def send(
        self, call: Callable[[], Awaitable[Any]], cl_ord_id: str
    ) -> tuple[bool, OkxClientError | None]:
        """Send an order with up to ``order_max_retries`` attempts and the same clOrdId. Before each
        retry OKX is asked whether the previous attempt landed. Returns (sent, last error)."""
        last: OkxClientError | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
                if await self.lookup(cl_ord_id) is not None:
                    return True, None
            try:
                await call()
                return True, None
            except OkxTradeError as err:
                if err.s_code == NO_POSITION_CODE and self.plan.kind == "close":
                    return True, None
                if err.duplicate_cl_ord_id and await self.lookup(cl_ord_id) is not None:
                    return True, None
                last = err
                if not err.duplicate_cl_ord_id and not _retryable(err, self.cfg):
                    return False, err
            except OkxClientError as err:
                last = err
                if not _retryable(err, self.cfg):
                    return False, err
            log.warning(
                "position action attempt failed", extra={"attempt": attempt, "error": type(last).__name__}
            )
        if await self.lookup(cl_ord_id) is not None:
            return True, None
        return False, last

    async def settle_order(self, order_id: uuid.UUID, cl_ord_id: str) -> OrderDetail | None:
        """Poll the close order until OKX reports it filled (or the verify window ends); sync the row."""
        polls = max(1, math.ceil(self.cfg.stop_verify_seconds / max(self.cfg.poll_seconds, 0.01)))
        detail: OrderDetail | None = None
        for poll in range(polls + 1):
            if poll:
                await self.sleep(self.cfg.poll_seconds)
            detail = await self.lookup(cl_ord_id) or detail
            if detail is not None and detail.state in ("filled", "canceled", "mmp_canceled"):
                break
        if detail is not None:
            fields: dict[str, Any] = {"ord_id": detail.ord_id}
            if detail.acc_fill_sz is not None:
                fields["filled_sz"] = detail.acc_fill_sz
            if detail.avg_px:
                fields["avg_px"] = detail.avg_px
            if detail.fee is not None:
                fields["fee"], fields["fee_ccy"] = detail.fee, detail.fee_ccy
            self.rec.order(order_id, _OKX_ORDER_STATES.get(detail.state, "live"), **fields)
        return detail

    # -- DB helpers ----------------------------------------------------------------------------------

    def proposal_status(self, status: str, **extra: Any) -> None:
        p = self.plan
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = s.get(Proposal, p.proposal_id, with_for_update=True)
            if row is None or row.status in (status, DONE, "closed", "rejected", "cancelled", "expired"):
                return
            before = row.status
            row.status = status
            row.approval_expires_at = None
            row.updated_at = at
        self.rec._publish(
            PROPOSALS_STREAM,
            "proposal.status_changed",
            {
                "proposal_id": str(p.proposal_id),
                "kind": p.kind,
                "from_status": before,
                "to_status": status,
                **extra,
            },
        )

    def algo_amended(self, a: AlgoOrderDetail, **values: Any) -> None:
        """Mirror an amend on our algo_orders row (matched by algoClOrdId, else algoId) and publish it."""
        p = self.plan
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = None
            if a.algo_cl_ord_id:
                row = s.scalar(
                    select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == a.algo_cl_ord_id).with_for_update()
                )
            if row is None:
                row = s.scalar(
                    select(AlgoOrder)
                    .where(AlgoOrder.env == p.env, AlgoOrder.algo_id == a.algo_id)
                    .with_for_update()
                )
            row_id = None
            if row is not None:
                for key, value in values.items():
                    setattr(row, key, value)
                row.algo_id = a.algo_id
                row.updated_at = at
                row_id = str(row.id)
        self.rec._publish(
            ORDERS_STREAM,
            "algo_order.amended",
            {
                "algo_order_id": row_id,
                "proposal_id": str(p.proposal_id),
                "algo_id": a.algo_id,
                "algo_cl_ord_id": a.algo_cl_ord_id,
                "inst_id": a.inst_id,
                "kind": a.ord_type,
                "changes": {k: _s(v) if isinstance(v, Decimal) else v for k, v in values.items()},
            },
        )

    def reject(self, outcome: ActionOutcome) -> ExecutionResult:
        """The pre-send re-check failed: record a rejected risk check and close the proposal."""
        p = self.plan
        at = self.now()
        failed = next((c for c in outcome.checks if c.get("status") == "failed"), {})
        code = failed.get("code") or outcome.failed_check or "recheck_failed"
        reason = f"Not sent: {outcome.reason}"
        with Session(self.engine, expire_on_commit=False) as s, s.begin():
            rc = RiskCheck(
                id=uuid.uuid4(),
                env=p.env,
                proposal_id=p.proposal_id,
                settings_version=p.settings_version,
                result="rejected",
                checks=outcome.checks,
                final_size=None,
                final_notional_usd=None,
                leverage=None,
                reason=reason,
                decision=None,
                created_at=at,
                updated_at=at,
            )
            s.add(rc)
            row = s.scalar(
                update(Proposal)
                .where(Proposal.id == p.proposal_id, Proposal.status.in_(EXECUTABLE_PROPOSAL_STATUSES))
                .values(
                    status="rejected",
                    status_reason="risk_rejected",
                    approval_expires_at=None,
                    updated_at=at,
                )
                .returning(Proposal)
            )
            payload = (
                proposal_payload(
                    row,
                    decided_by=row.decided_by or "system",
                    risk_check_id=str(rc.id),
                    result="rejected",
                    reason=reason,
                    failed_check=outcome.failed_check,
                    code=code,
                )
                if row is not None
                else None
            )
        if row is not None and payload is not None:
            publish_proposal(self.rds, PROPOSALS_STREAM, "proposal.rejected", row, payload, SOURCE)
        self.rec.warroom(
            "order",
            f"{p.kind} on {p.inst_id} not sent: {outcome.reason}",
            {
                **self.card,
                "state": "rejected",
                "code": code,
                "failed_check": outcome.failed_check,
                "risk_check_id": str(rc.id),
            },
        )
        return self.result("refused", "risk_rejected", outcome.reason)

    def done(self, status: str, summary: str, **card: Any) -> ExecutionResult:
        self.rec.warroom("order", summary, {**self.card, **card, "state": status})
        return self.result(
            "sent",
            "executed",
            summary,
            state=status,
            stop_verified=True,
            **{k: v for k, v in card.items() if k in ("order_id", "cl_ord_id", "ord_id")},
        )

    def failed(self, code: str | None, message: str | None, what: str, **card: Any) -> ExecutionResult:
        message = message or (
            "OKX could not be reached, so the change was not confirmed. Check Orders before retrying."
        )
        self.rec.warroom(
            "order",
            f"{self.plan.kind} on {self.plan.inst_id} failed ({what}): {message}",
            {**self.card, **card, "state": "failed", "okx_code": code, "error": message},
        )
        return self.result("failed", "okx_refused" if code else "okx_unreachable", message, okx_code=code)


def _err_parts(err: OkxClientError | None) -> tuple[str | None, str | None]:
    if isinstance(err, OkxTradeError):
        return err.s_code, err.human_message
    return None, None


def _amend(
    run: _Run,
    algo_id: str,
    *,
    new_sz: Decimal | None = None,
    new_sl_trigger_px: Decimal | None = None,
    new_tp_trigger_px: Decimal | None = None,
) -> Callable[[], Awaitable[Any]]:
    """One amend-algos call, as a retryable thunk."""

    def call() -> Awaitable[Any]:
        return run.trade.amend_algo(
            run.plan.inst_id,
            algo_id=algo_id,
            new_sz=new_sz,
            new_sl_trigger_px=new_sl_trigger_px,
            new_tp_trigger_px=new_tp_trigger_px,
        )

    return call


def _entry_for(session: Session, env: str, inst_id: str, pos: HeldPosition) -> uuid.UUID | None:
    """The newest filled Atlas entry that opened this position (None for an external position)."""
    open_side = "buy" if pos.direction > 0 else "sell"
    q = (
        select(Order.id)
        .where(
            Order.env == env,
            Order.inst_id == inst_id,
            Order.origin == "atlas",
            Order.purpose == "entry",
            Order.side == open_side,
            Order.filled_sz > 0,
        )
        .order_by(Order.created_at.desc())
        .limit(1)
    )
    if pos.pos_side in ("long", "short"):
        q = q.where(Order.pos_side == pos.pos_side)
    return session.scalar(q)


async def _amend_stop(run: _Run, pos: HeldPosition, algos: Sequence[AlgoOrderDetail]) -> ExecutionResult:
    p = run.plan
    assert p.stop is not None
    open_side = "buy" if pos.direction > 0 else "sell"
    new_stop = round_stop(p.stop, p.tick_sz, open_side)
    run.card["new_stop"] = _s(new_stop)
    stops = [a for a in algos if has_stop(a) and _protects(a, pos)]
    if not stops:
        placed = await _place_algo(run, pos, NEW_STOP_PREFIX, sl=new_stop, tp=None)
        assert placed is not None
        return placed
    amended: list[str] = []
    for a in stops:
        got = await run.ctl.retry(
            _amend(run, a.algo_id, new_sl_trigger_px=new_stop),
            frozenset(),
        )
        if not got.ok:
            # The old stop is still on OKX: the position stays protected.
            return run.failed(got.code, got.message, "stop amend", amended=amended, algo_id=a.algo_id)
        field_name = "sl_trigger_px" if a.ord_type == "oco" else "trigger_px"
        run.algo_amended(a, **{field_name: new_stop})
        amended.append(a.algo_id)
    run.proposal_status(DONE)
    return run.done(DONE, f"Stop on {p.inst_id} moved to {_s(new_stop)}.", amended=amended)


async def _amend_targets(run: _Run, pos: HeldPosition, algos: Sequence[AlgoOrderDetail]) -> ExecutionResult:
    p = run.plan
    close_side = "sell" if pos.direction > 0 else "buy"
    tp = round_px(p.targets[0], p.tick_sz, close_side)
    run.card["new_tp"] = _s(tp)
    mine = [a for a in algos if _protects(a, pos)]
    ocos = [a for a in mine if a.ord_type == "oco"]
    tp_only = [
        a
        for a in mine
        if a.ord_type == "conditional" and a.sl_trigger_px is None and a.tp_trigger_px is not None
    ]
    amended: list[str] = []
    placed: ExecutionResult | None = None
    for a in ocos:
        got = await run.ctl.retry(_amend(run, a.algo_id, new_tp_trigger_px=tp), frozenset())
        if not got.ok:
            return run.failed(got.code, got.message, "take-profit amend", amended=amended, algo_id=a.algo_id)
        run.algo_amended(a, tp_trigger_px=tp)
        amended.append(a.algo_id)
    if not ocos:
        placed = await _place_algo(run, pos, NEW_TP_PREFIX, sl=None, tp=tp, finish=False)
        if placed is not None:
            return placed
    cancelled = await run.ctl.cancel_algos([algo_ref(a) for a in tp_only]) if tp_only else []
    run.proposal_status(DONE)
    return run.done(
        DONE,
        f"Take-profit on {p.inst_id} moved to {_s(tp)}.",
        amended=amended,
        cancelled=[i.ref for i in cancelled if i.action in ("cancelled", "already_done")],
    )


async def _place_algo(
    run: _Run, pos: HeldPosition, prefix: str, *, sl: Decimal | None, tp: Decimal | None, finish: bool = True
) -> ExecutionResult | None:
    """Place a stand-alone SL (no stop on OKX at all) or TP for the whole position."""
    p = run.plan
    cl = derived_id(prefix, p.risk_check_id)
    close_side = "sell" if pos.direction > 0 else "buy"
    pos_side = pos.pos_side if pos.pos_side in ("long", "short") else None
    at = run.now()
    run.rec.algo(
        cl,
        "sending",
        new=AlgoOrder(
            id=uuid.uuid4(),
            env=p.env,
            order_id=None,
            algo_cl_ord_id=cl,
            inst_id=p.inst_id,
            kind="sl" if sl is not None else "tp",
            side=close_side,
            pos_side=pos_side,
            trigger_px=sl if sl is not None else tp,
            sl_trigger_px=None,
            tp_trigger_px=tp,
            ord_px=None,
            sz=pos.contracts,
            state="new",
            reduce_only=True,
            origin="atlas",
            raw={"placed_as": f"position_action:{p.kind}", "proposal_id": str(p.proposal_id)},
            created_at=at,
            updated_at=at,
        ),
    )
    req = AlgoOrderRequest(
        inst_id=p.inst_id,
        td_mode=run.td_mode,  # type: ignore[arg-type]
        side=close_side,  # type: ignore[arg-type]
        ord_type="conditional",
        algo_cl_ord_id=cl,
        sz=pos.contracts,
        pos_side=pos_side,  # type: ignore[arg-type]
        reduce_only=True,
        sl_trigger_px=sl,
        tp_trigger_px=tp,
    )
    got = await run.ctl.retry(lambda: run.trade.place_algo_order(req), frozenset())
    if not got.ok:
        run.rec.algo(cl, "failed", last_error_code=got.code, last_error_msg=got.message)
        return run.failed(got.code, got.message, "algo order", algo_cl_ord_id=cl)
    run.rec.algo(cl, "live", algo_id=got.value.algo_id)
    if not finish:
        return None
    run.proposal_status(DONE)
    what = f"stop at {_s(sl)}" if sl is not None else f"take-profit at {_s(tp)}"
    return run.done(DONE, f"Placed a {what} on {p.inst_id} (no stop was live).", placed=cl)


async def _close(
    run: _Run,
    pos: HeldPosition | None,
    okx_pos: Position | None,
    contracts: Decimal,
    algos: Sequence[AlgoOrderDetail],
    found: OrderDetail | None,
) -> ExecutionResult:
    p = run.plan
    at = run.now()
    pos_side = pos.pos_side if pos is not None and pos.pos_side in ("long", "short") else None
    close_side = ("sell" if pos.direction > 0 else "buy") if pos is not None else None
    order_id = p.order_id
    if order_id is None:
        assert pos is not None and okx_pos is not None and close_side is not None
        with Session(run.engine) as s:
            reduces = _entry_for(s, p.env, p.inst_id, pos)
        order_id = uuid.uuid4()
        run.rec.new_order(
            Order(
                id=order_id,
                env=p.env,
                proposal_id=p.proposal_id,
                risk_check_id=p.risk_check_id,
                cl_ord_id=p.cl_ord_id,
                inst_id=p.inst_id,
                side=close_side,
                pos_side=pos_side,
                ord_type="market",
                td_mode=okx_pos.mgn_mode
                if okx_pos.mgn_mode in ("isolated", "cross")
                else run.cfg.td_mode_perps,
                px=None,
                sz=contracts,
                lever=None,
                state="new",
                reduce_only=True,
                origin="atlas",
                purpose="close",
                raw={
                    "position_action": p.kind,
                    "reduces_order_id": str(reduces) if reduces else None,
                    "position_contracts": _s(pos.contracts),
                },
                created_at=at,
                updated_at=at,
            )
        )
    run.card.update(order_id=str(order_id), cl_ord_id=p.cl_ord_id, sz=_s(contracts))

    if found is None:
        assert okx_pos is not None and close_side is not None
        run.rec.order(order_id, "sending")
        if p.kind == "close":
            mgn = okx_pos.mgn_mode

            async def call() -> Any:
                return await run.trade.close_position(
                    p.inst_id,
                    mgn,  # type: ignore[arg-type]
                    pos_side=pos_side,  # type: ignore[arg-type]
                    auto_cxl=True,
                    cl_ord_id=p.cl_ord_id,
                )
        else:
            req = OrderRequest(
                inst_id=p.inst_id,
                td_mode=okx_pos.mgn_mode,  # type: ignore[arg-type]
                side=close_side,  # type: ignore[arg-type]
                ord_type="market",
                sz=contracts,
                cl_ord_id=p.cl_ord_id,
                pos_side=pos_side,  # type: ignore[arg-type]
                reduce_only=True,
            )

            async def call() -> Any:
                return await run.trade.place_order(req)

        sent, err = await run.send(call, p.cl_ord_id)
        if not sent:
            code, msg = _err_parts(err)
            rejected = isinstance(err, OkxTradeError) and not _retryable(err, run.cfg)
            run.rec.order(
                order_id, "rejected" if rejected else "failed", last_error_code=code, last_error_msg=msg
            )
            # Nothing was closed: every stop stays where it is.
            return run.failed(code, msg, "close order")
        run.rec.order(order_id, "live")
    detail = await run.settle_order(order_id, p.cl_ord_id)
    filled = detail is not None and detail.state == "filled"

    if p.kind == "close":
        stale = [a for a in algos if pos is None or _protects(a, pos)]
        items = await run.ctl.cancel_algos([algo_ref(a) for a in stale]) if stale else []
        bad = [i for i in items if i.action == "failed"]
        if bad:
            run.rec.alert(
                "position_action_cancel_failed",
                f"{p.inst_id} closed, but {len(bad)} algo order(s) could not be cancelled.",
                {**run.card, "failed": [i.payload() for i in bad]},
            )
        status = DONE if filled else SENT
        run.proposal_status(status, order_id=str(order_id))
        return run.done(
            status,
            f"Closed the {p.inst_id} position ({_s(contracts)} contracts).",
            cancelled=[i.ref for i in items if i.action in ("cancelled", "already_done")],
            order_id=str(order_id),
            cl_ord_id=p.cl_ord_id,
            ord_id=detail.ord_id if detail else None,
        )

    # partial_close: resize protection to what is left.
    remaining = Decimal(0)
    if pos is not None:
        remaining = max(Decimal(0), pos.contracts - contracts)
    resized: list[str] = []
    if pos is not None and remaining > 0:
        mine = [a for a in algos if _protects(a, pos)]
        if not any(has_stop(a) for a in mine):
            run.rec.alert(
                "position_unprotected",
                f"{p.inst_id} has no stop on OKX after the partial close; reconciliation will place one.",
                {**run.card, "remaining": _s(remaining)},
            )
        for a in mine:
            if a.sz is None or a.sz <= remaining:
                continue
            got = await run.ctl.retry(_amend(run, a.algo_id, new_sz=remaining), frozenset())
            if not got.ok:
                # The stop is still on OKX at the old size; reduce-only, so it still protects.
                run.rec.alert(
                    "protection_resize_failed",
                    f"Could not resize the {p.inst_id} stop to {_s(remaining)}: {got.message}",
                    {**run.card, "algo_id": a.algo_id},
                )
                continue
            run.algo_amended(a, sz=remaining)
            resized.append(a.algo_id)
    status = DONE if filled else SENT
    run.proposal_status(status, order_id=str(order_id))
    return run.done(
        status,
        f"Closed {_s(contracts)} contracts of {p.inst_id}; {_s(remaining)} left with the stop resized.",
        resized=resized,
        remaining=_s(remaining),
        order_id=str(order_id),
        cl_ord_id=p.cl_ord_id,
        ord_id=detail.ord_id if detail else None,
    )


async def _run_action(run: _Run) -> ExecutionResult:
    p = run.plan
    found = await run.lookup(p.cl_ord_id) if p.resume else None
    got_pos = await run.ctl.retry(lambda: run.ctl.positions(p.inst_id), frozenset())
    got_algos = await run.ctl.retry(lambda: run.ctl.pending_algos(p.inst_id), frozenset())
    if not got_pos.ok or not got_algos.ok:
        miss = got_pos if not got_pos.ok else got_algos
        return run.failed(miss.code, miss.message, "reading the position")
    positions: list[Position] = got_pos.value or []
    algos: list[AlgoOrderDetail] = [a for a in (got_algos.value or []) if a.inst_id == p.inst_id]
    at = run.now()
    outcome = run_position_checks(_okx_context(p.ctx, positions, algos, at))
    pos = outcome.position
    okx_pos = next((x for x in positions if pos is not None and (x.pos_side or "net") == pos.pos_side), None)
    if okx_pos is not None and okx_pos.mgn_mode in ("isolated", "cross"):
        run.td_mode = okx_pos.mgn_mode
    if found is not None:
        # A crashed run's order already landed: finish the protection work, send nothing new.
        with Session(run.engine) as s:
            row = s.get(Order, p.order_id)
            contracts = Decimal(format(Decimal(row.sz).normalize(), "f")) if row is not None else Decimal(0)
            before = (row.raw or {}).get("position_contracts") if row is not None else None
        if pos is not None and before is not None:
            # The position OKX shows is already reduced: size protection from the pre-close size.
            pos = dataclasses.replace(pos, contracts=Decimal(str(before)))
        return await _close(run, pos, okx_pos, contracts, algos, found)
    if outcome.result == "rejected":
        return run.reject(outcome)
    assert pos is not None
    if p.kind == "amend_stop":
        return await _amend_stop(run, pos, algos)
    if p.kind == "amend_targets":
        return await _amend_targets(run, pos, algos)
    close_contracts = outcome.close_contracts
    if close_contracts is None or close_contracts <= 0:
        return run.reject(outcome)
    return await _close(run, pos, okx_pos, close_contracts, algos, None)


async def execute_position_action(
    rc_id: uuid.UUID,
    approved_by: str | None,
    *,
    engine: Engine,
    rds: redis.Redis,
    client_factory: OkxFactory | None,
    app_key: str,
    active_env: str | None,
    sleep: Sleep,
    now: Callable[[], datetime],
) -> ExecutionResult:
    from trading_core.execution.executor import ExecutionResult

    try:
        with Session(engine) as session, session.begin():
            plan = _plan_action(session, rds, rc_id, approved_by, active_env, now())
    except _Refusal as refusal:
        extra = {k: v for k, v in refusal.extra.items() if k in ("order_id", "cl_ord_id", "ord_id", "state")}
        result = ExecutionResult("refused", refusal.reason, refusal.detail, str(rc_id), **extra)
        from trading_core.execution.okx_access import publish

        publish(rds, ORDERS_STREAM, "order.refused", active_env or "demo", result.payload(), source=SOURCE)
        return result
    rec = _Recorder(engine, rds, plan, now)
    okx = (client_factory or default_factory(app_key))(plan.creds, retry=single_attempt())
    ctl = Control(engine, plan.env, rds, plan.cfg, sleep, now)
    async with okx:
        trade = ctl.bind(okx)
        run = _Run(plan, ctl, trade, rec, engine, rds, sleep, now, td_mode=plan.cfg.td_mode_perps)
        run.card = {
            "type": "position_action",
            "proposal_id": str(plan.proposal_id),
            "risk_check_id": str(plan.risk_check_id),
            "kind": plan.kind,
            "inst_id": plan.inst_id,
        }
        return await _run_action(run)


__all__ = ["POSITION_KINDS", "execute_position_action", "proposal_kind"]
