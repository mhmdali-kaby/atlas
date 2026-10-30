"""The Risk Engine (brief §10): ``evaluate(proposal_id)`` runs the nine checks in order.

1 env & kill switch, 2 instrument allowed, 3 proposal valid, 4 position size, 5 portfolio, 6 loss
limits, 7 liquidation buffer, 8 event blackout, 9 autonomy. The first hard failure rejects and the
checks after it are recorded as ``not_run``; caps in check 4 resize. Every evaluation writes one
``risk_checks`` row (all nine entries + the settings version it used), sets the proposal to
``risk_checked`` and publishes one ``risk.decision`` envelope on ``atlas.risk``.

The settings row is re-read on every call (brief §3.2). Inputs come from Postgres (snapshot tables
written by the sync loops) and the market-data hash in Redis (ATL-125). Nothing here talks to OKX.
:func:`run_checks` is pure: :func:`trading_core.risk.context.load_context` does all the I/O.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import redis
from atlas_shared import bus
from atlas_shared.db import (
    Proposal,
    RiskCheck,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from atlas_shared.proposals import POSITION_ACTION_KINDS
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.risk.context import (
    InstrumentInfo,
    ProposalInfo,
    RiskContext,
    RiskEngineError,
    ensure_trading_state,
    load_context,
)
from trading_core.risk.inputs import (
    ProposalInput,
    RiskSettings,
)
from trading_core.risk.position_actions import (
    load_action_context,
    run_position_checks,
    settings_row,
)
from trading_core.risk.rules import (
    CheckResult,
    Decision,
    EngineSettings,
    check_environment,
    check_event_blackout,
    check_instrument,
    check_liquidation,
    check_loss_limits,
    check_portfolio,
    decide_autonomy,
    not_run,
    rules_config,
)
from trading_core.risk.sizing import SizingResult, size_position
from trading_core.risk.validity import check_validity

CHECK_NAMES: tuple[str, ...] = (
    "env_and_kill_switch",
    "instrument_allowed",
    "proposal_valid",
    "position_size",
    "portfolio",
    "loss_limits",
    "liquidation_buffer",
    "event_blackout",
    "autonomy",
)
STREAM = "atlas.risk"
EVENT_TYPE = "risk.decision"
SOURCE = "trading_core:risk"

Result = Literal["approved", "resized", "rejected"]


@dataclass(frozen=True)
class Outcome:
    checks: list[dict[str, Any]]
    result: Result
    decision: Decision | None
    reason: str
    failed_check: str | None
    sizing: SizingResult | None


@dataclass(frozen=True)
class RiskDecision:
    proposal_id: uuid.UUID
    risk_check_id: uuid.UUID
    env: str
    settings_version: int
    result: Result
    decision: Decision | None
    reason: str
    failed_check: str | None
    checks: list[dict[str, Any]]
    final_size: Decimal | None
    final_notional_usd: Decimal | None
    leverage: int | None

    def payload(self) -> dict[str, Any]:
        def s(v: Decimal | None) -> str | None:
            return None if v is None else str(v)

        return {
            "proposal_id": str(self.proposal_id),
            "risk_check_id": str(self.risk_check_id),
            "settings_version": self.settings_version,
            "result": self.result,
            "decision": self.decision,
            "reason": self.reason,
            "failed_check": self.failed_check,
            "final_size": s(self.final_size),
            "final_notional_usd": s(self.final_notional_usd),
            "leverage": self.leverage,
        }


# --- The nine checks in order (pure) -----------------------------------------------------------


def resolve_entry(p: ProposalInfo, mark: Decimal | None) -> Decimal | None:
    """The price to size at: the mark for a market order, else the entry, else the entry-zone bound
    farthest from the stop (the widest stop distance, so the smallest size)."""
    if p.order_type == "market":
        return mark if mark is not None else p.entry
    if p.entry is not None:
        return p.entry
    if p.entry_zone is not None:
        low, high = sorted(p.entry_zone)
        if p.stop is None:
            return (low + high) / 2
        return high if p.side == "buy" else low
    return None


def _direction(side: str) -> int:
    return 1 if side == "buy" else -1


def run_checks(ctx: RiskContext, config: Mapping[str, Any] | None = None) -> Outcome:
    cfg = config or rules_config()
    eng = EngineSettings.from_settings_data(ctx.settings_data)
    risk = RiskSettings.from_settings_data(ctx.settings_data)
    p = ctx.proposal
    checks: list[dict[str, Any]] = []
    sizing: SizingResult | None = None
    entry: Decimal | None = None
    inst = ctx.instrument

    def step(n: int) -> CheckResult:
        nonlocal sizing, entry
        if n == 1:
            return check_environment(
                paused=ctx.paused,
                pause_reason=ctx.pause_reason,
                active_breakers=ctx.active_breakers,
                connection_verified=ctx.connection_verified,
                env=p.env,
                ticker_at=ctx.ticker_at,
                now=ctx.now,
                max_age_seconds=eng.market_data_max_age_seconds,
            )
        if n == 2:
            return check_instrument(
                inst_id=p.inst_id,
                found=inst is not None,
                inst_type=inst.inst_type if inst else None,
                asset_class=inst.asset_class if inst else None,
                state=inst.state if inst else None,
                settings=eng,
                now=ctx.now,
                config=cfg,
            )
        assert inst is not None
        if n == 3:
            entry = resolve_entry(p, ctx.mark)
            if entry is None or entry <= 0:
                return CheckResult(
                    "proposal_valid",
                    False,
                    "entry_unknown",
                    "The proposal has no entry price and there is no mark price.",
                )
            if ctx.atr_1h is None:
                return CheckResult(
                    "proposal_valid",
                    False,
                    "atr_unavailable",
                    f"ATR(1H) cannot be computed for {p.inst_id}: not enough closed 1H bars.",
                )
            v = check_validity(_proposal_input(p, entry), risk, ctx.atr_1h)
            c = v.as_check()
            values = dict(c["values"], atr_1h=str(ctx.atr_1h), entry_used=str(entry))
            return CheckResult("proposal_valid", v.passed, v.code, v.reason, values)
        if n == 4:
            assert entry is not None
            if ctx.equity is None or ctx.equity <= 0:
                return CheckResult(
                    "position_size", False, "equity_unknown", "Account equity is not synced yet."
                )
            try:
                sizing = size_position(
                    _proposal_input(p, entry),
                    risk,
                    inst.spec,
                    equity=ctx.equity,
                    available_usd=ctx.available_usd,
                    fee_rate=ctx.fee_rate,
                    new_exposure_used_today_usd=ctx.used_exposure_today_usd,
                    instrument_exposure_usd=sum(
                        (x.notional_usd for x in ctx.positions if x.inst_id == p.inst_id), Decimal(0)
                    ),
                    mark_price=ctx.mark,
                    usdc_usd_rate=ctx.usdc_usd_rate,
                )
            except ValueError as exc:
                return CheckResult("position_size", False, "sizing_input_missing", str(exc))
            c = sizing.as_check()
            values = dict(
                c["values"],
                binding_cap=c["binding_cap"],
                leverage_binding=c["leverage_binding"],
                equity=str(ctx.equity),
                fee_rate=str(ctx.fee_rate),
                fee_source=ctx.fee_source,
            )
            return CheckResult("position_size", sizing.passed, sizing.code, sizing.reason, values)
        assert sizing is not None and sizing.final_notional_usd is not None and ctx.equity is not None
        if n == 5:
            return check_portfolio(
                inst_id=p.inst_id,
                direction=_direction(p.side),
                notional_usd=sizing.final_notional_usd,
                asset_class=inst.asset_class,
                positions=ctx.positions,
                correlations=ctx.correlations,
                equity=ctx.equity,
                trades_today=ctx.trades_today,
                settings=eng,
                threshold=Decimal(str(cfg["correlation_threshold"])),
            )
        if n == 6:
            return check_loss_limits(
                day_loss_usd=ctx.day_loss_usd,
                day_base_usd=ctx.day_base_usd,
                week_loss_usd=ctx.week_loss_usd,
                week_base_usd=ctx.week_base_usd,
                consecutive_losses=ctx.consecutive_losses,
                settings=eng,
            )
        if n == 7:
            assert sizing.entry is not None and sizing.stop is not None and sizing.leverage is not None
            return check_liquidation(
                side=p.side,
                entry=sizing.entry,
                stop=sizing.stop,
                leverage=sizing.leverage,
                ct_type=inst.spec.ct_type,
                mmr=Decimal(str(cfg["maint_margin_ratio"])),
                settings=eng,
            )
        if n == 8:
            return check_event_blackout(
                events=ctx.events,
                now=ctx.now,
                settings=eng,
                min_importance=int(cfg["high_impact_importance"]),
            )
        raise AssertionError(n)

    failed: CheckResult | None = None
    decision: Decision | None = None
    for n, name in enumerate(CHECK_NAMES, start=1):
        if failed is not None:
            checks.append(not_run(n, name))
            continue
        if n == 9:
            res, decision = decide_autonomy(
                eng.autonomy_level, int(DEFAULTS["autonomy"]["max_enabled_level"])
            )
        else:
            res = step(n)
        checks.append(res.as_check(n))
        if not res.passed:
            failed = res

    if failed is not None:
        return Outcome(checks, "rejected", None, failed.reason, failed.name, sizing)
    assert sizing is not None and decision is not None
    result: Result = "resized" if sizing.resized else "approved"
    reason = (
        f"Resized by {sizing.binding_cap}: {sizing.contracts} contracts, {sizing.final_notional_usd} USD."
        if result == "resized"
        else f"Approved: {sizing.contracts} contracts, {sizing.final_notional_usd} USD."
    )
    return Outcome(checks, result, decision, reason, None, sizing)


def _proposal_input(p: ProposalInfo, entry: Decimal) -> ProposalInput:
    return ProposalInput(
        side=p.side,
        entry=entry,
        stop=p.stop,
        expected_r=p.expected_r if p.expected_r is not None else Decimal(0),
        leverage_requested=max(1, p.leverage_suggestion or 1),
        size_suggestion_pct_equity=p.size_suggestion_pct,
    )


# --- evaluate ----------------------------------------------------------------------------------


def _evaluate_action(
    session: Session, rds: Any, row: Proposal, at: datetime, config: Mapping[str, Any] | None
) -> RiskDecision:
    """A Portfolio Manager position action (ATL-184): the seven position-action checks instead of
    the nine entry checks. final_size is the contracts to close (closes only); leverage is None."""
    found = settings_row(session, row.env)
    if found is None:
        raise RiskEngineError(f"no settings row for {row.env}")
    state = ensure_trading_state(session, row.env, at)
    breakers = state.breakers if isinstance(state.breakers, dict) else {}
    active = tuple(sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active")))
    actx = load_action_context(
        session,
        rds,
        row,
        at,
        settings_version=found[0],
        settings_data=found[1],
        paused=bool(state.paused),
        pause_reason=state.reason,
        active_breakers=active,
        config=config,
    )
    out = run_position_checks(actx, config)
    return RiskDecision(
        proposal_id=row.id,
        risk_check_id=uuid.uuid4(),
        env=row.env,
        settings_version=found[0],
        result=out.result,
        decision=out.decision,
        reason=out.reason,
        failed_check=out.failed_check,
        checks=out.checks,
        final_size=out.close_contracts,
        final_notional_usd=out.close_notional_usd,
        leverage=None,
    )


def evaluate(
    proposal_id: uuid.UUID | str,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
    config: Mapping[str, Any] | None = None,
    allowed_statuses: Collection[str] | None = None,
) -> RiskDecision:
    """Run the nine checks on one entry (or the seven position-action checks on a Portfolio Manager
    amend/close, :mod:`trading_core.risk.position_actions`), store the risk_checks row, mark the proposal
    risk_checked and publish ``risk.decision``. Raises :class:`RiskEngineError` when it cannot run.

    ``allowed_statuses`` overrides ``risk_rules.evaluable_statuses`` for one call; only the owner's
    manual check (``POST /internal/risk/check``, ATL-128) uses it, for the draft row it just wrote."""
    pid = proposal_id if isinstance(proposal_id, uuid.UUID) else uuid.UUID(str(proposal_id))
    rds = redis_client if redis_client is not None else bus.make_client()
    at = (now or (lambda: datetime.now(UTC)))()
    with Session(engine) as session, session.begin():
        row = session.scalar(select(Proposal).where(Proposal.id == pid).with_for_update())
        if row is None:
            raise RiskEngineError(f"proposal {pid} does not exist")
        allowed = (
            [str(x) for x in allowed_statuses]
            if allowed_statuses is not None
            else [str(x) for x in (config or rules_config())["evaluable_statuses"]]
        )
        if row.status not in allowed:
            raise RiskEngineError(
                f"proposal {pid} is {row.status}; only {', '.join(allowed)} proposals are risk-checked"
            )
        if row.kind in POSITION_ACTION_KINDS:
            decision = _evaluate_action(session, rds, row, at, config)
            correlation = str(row.cycle_id) if row.cycle_id else None
        else:
            ctx = load_context(session, rds, row, at, config)
            outcome = run_checks(ctx, config)
            sizing = outcome.sizing if outcome.result != "rejected" else None
            decision = RiskDecision(
                proposal_id=pid,
                risk_check_id=uuid.uuid4(),
                env=ctx.proposal.env,
                settings_version=ctx.settings_version,
                result=outcome.result,
                decision=outcome.decision,
                reason=outcome.reason,
                failed_check=outcome.failed_check,
                checks=outcome.checks,
                final_size=sizing.contracts if sizing else None,
                final_notional_usd=sizing.final_notional_usd if sizing else None,
                leverage=sizing.leverage if sizing else None,
            )
            correlation = ctx.proposal.correlation_id
        session.add(
            RiskCheck(
                id=decision.risk_check_id,
                env=decision.env,
                proposal_id=pid,
                settings_version=decision.settings_version,
                result=decision.result,
                checks=decision.checks,
                final_size=decision.final_size,
                final_notional_usd=decision.final_notional_usd,
                leverage=decision.leverage,
                reason=decision.reason,
                decision=decision.decision,
                created_at=at,
                updated_at=at,
            )
        )
        row.status = "risk_checked"
        row.updated_at = at
    envelope = EventEnvelope.model_validate(
        {
            "type": EVENT_TYPE,
            "env": decision.env,
            "source": SOURCE,
            "correlation_id": correlation or str(pid),
            "payload": decision.payload(),
        }
    )
    bus.publish(STREAM, envelope, client=rds)
    return decision


__all__ = [
    "CHECK_NAMES",
    "InstrumentInfo",
    "Outcome",
    "ProposalInfo",
    "RiskContext",
    "RiskDecision",
    "RiskEngineError",
    "ensure_trading_state",
    "evaluate",
    "load_context",
    "resolve_entry",
    "run_checks",
]
