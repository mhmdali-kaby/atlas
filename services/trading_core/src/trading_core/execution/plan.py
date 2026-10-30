"""The plan: every check and decision the Executor makes before its first HTTP request to OKX."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import (
    AlgoOrder,
    Connection,
    Instrument,
    Order,
    Proposal,
    RiskCheck,
    TradingState,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from trading_core.execution.config import (
    EXECUTABLE_PROPOSAL_STATUSES,
    EXECUTABLE_RESULTS,
    LONG_SHORT_MODE,
    OWNER,
    WITHDRAW,
    ExecutorConfig,
    derived_id,
    round_px,
    round_stop,
)
from trading_core.execution.okx_access import CredSnapshot


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
        stop=round_stop(Decimal(proposal.stop), inst.tick_sz, side),
        tp1=round_px(tp1, inst.tick_sz, side) if tp1 is not None else None,
        cl_ord_id=existing.cl_ord_id if existing is not None and existing.cl_ord_id else cl_ord_id,
        attach_id=attach_id,
        bare_id=derived_id("f", rc.id),
        oco_id=derived_id("o", rc.id),
        close_id=derived_id("c", rc.id),
        resume=existing is not None,
        cfg=cfg,
        creds=CredSnapshot.of(conn),
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
