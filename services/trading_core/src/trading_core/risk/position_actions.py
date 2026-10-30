"""Risk checks for the Portfolio Manager's position actions (ATL-184, brief §7.8, §10, rule 3).

An entry goes through the nine §10 checks; a position action (``amend_stop``, ``amend_targets``,
``partial_close``, ``close``) changes or reduces a position that already exists, so it gets its own
seven checks, run in order and stored in ``risk_checks.checks`` exactly like the entry checks:

1 ``env_state``        a verified connection; a pause or a tripped breaker only refuses kinds that
                       do not reduce risk (``amend_targets``); stops and closes pass.
2 ``position_open``    an open position on the instrument in the newest positions snapshot (synced
                       from OKX), no older than ``positions_max_age_seconds``.
3 ``stop_tightens``    amend_stop: the new stop is strictly tighter than the live exchange stop (the
                       tightest live sl/oco algo order protecting that position). Never wider.
4 ``stop_vs_mark``     amend_stop: the new stop stays on the safe side of mark (below for a long).
5 ``targets_vs_mark``  amend_targets: every target is on the profit side of mark.
6 ``close_size``       partial_close: close_pct in (0, 100]; close = 100. Contracts to close, rounded
                       down to lotSz, must reach minSz.
7 ``autonomy``         advise_only at level 1, awaiting_owner at level 2 (as for entries).

A check that does not apply to the kind is recorded as ``not_applicable``. After the first failure
the rest are ``not_run``. :func:`run_position_checks` is pure; :func:`load_action_context` does the
reads (Postgres and the market-data hash, never OKX).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

from atlas_shared.db import AlgoOrder, Connection, Instrument, PositionSnapshot, Proposal
from atlas_shared.defaults import DEFAULTS
from atlas_shared.market_data import read_snapshot
from atlas_shared.proposals import POSITION_ACTION_KINDS
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trading_core.risk.inputs import infer_contract_type
from trading_core.risk.rules import (
    CheckResult,
    Decision,
    EngineSettings,
    decide_autonomy,
    not_run,
    rules_config,
)

POSITION_CHECK_NAMES: tuple[str, ...] = (
    "env_state",
    "position_open",
    "stop_tightens",
    "stop_vs_mark",
    "targets_vs_mark",
    "close_size",
    "autonomy",
)

# Which checks each kind runs (env_state, position_open and autonomy always run).
_APPLIES: dict[str, frozenset[str]] = {
    "amend_stop": frozenset({"stop_tightens", "stop_vs_mark"}),
    "amend_targets": frozenset({"targets_vs_mark"}),
    "partial_close": frozenset({"close_size"}),
    "close": frozenset({"close_size"}),
}
_ALWAYS = frozenset({"env_state", "position_open", "autonomy"})
_HUNDRED = Decimal(100)

Result = Literal["approved", "rejected"]


def position_config(config: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = (config or rules_config())["position_actions"]
    return cfg


# --- Inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActionInfo:
    id: uuid.UUID
    env: str
    kind: str
    inst_id: str
    side: str | None
    """Optional: buy = the long position, sell = the short one (only needed in long/short mode)."""
    stop: Decimal | None
    targets: tuple[Decimal, ...]
    close_pct: Decimal | None
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class HeldPosition:
    inst_id: str
    pos_side: str
    direction: int
    """1 long, -1 short."""
    contracts: Decimal
    """abs(pos)."""
    avg_px: Decimal | None
    mark_px: Decimal | None
    taken_at: datetime


@dataclass(frozen=True, slots=True)
class LiveStop:
    algo_id: str | None
    algo_cl_ord_id: str | None
    kind: str
    price: Decimal
    pos_side: str | None
    side: str | None


@dataclass(frozen=True, slots=True)
class CloseSpec:
    ct_type: str
    ct_val: Decimal
    ct_mult: Decimal
    lot_sz: Decimal
    min_sz: Decimal


@dataclass(frozen=True)
class ActionContext:
    now: datetime
    action: ActionInfo
    settings_version: int
    settings_data: Mapping[str, Any]
    paused: bool = False
    pause_reason: str | None = None
    active_breakers: tuple[str, ...] = ()
    connection_verified: bool = False
    positions: tuple[HeldPosition, ...] = ()
    """Every open position on the instrument in the newest snapshot."""
    positions_taken_at: datetime | None = None
    """When the env's newest positions snapshot was taken (None = never synced)."""
    live_stops: tuple[LiveStop, ...] = ()
    """Live sl/oco algo orders on the instrument (matched to the position in check 3)."""
    mark: Decimal | None = None
    mark_source: str | None = None
    spec: CloseSpec | None = None


@dataclass(frozen=True)
class ActionOutcome:
    checks: list[dict[str, Any]]
    result: Result
    decision: Decision | None
    reason: str
    failed_check: str | None
    position: HeldPosition | None = None
    current_stop: Decimal | None = None
    close_contracts: Decimal | None = None
    close_notional_usd: Decimal | None = None
    values: dict[str, Any] = field(default_factory=dict)


# --- Pure checks -------------------------------------------------------------------------------


def _not_applicable(n: int, name: str, kind: str) -> dict[str, Any]:
    return {
        "n": n,
        "check": name,
        "status": "not_applicable",
        "passed": None,
        "code": None,
        "detail": f"Not applicable to {kind}.",
        "values": {},
    }


def matching_stops(stops: Sequence[LiveStop], pos: HeldPosition) -> list[LiveStop]:
    """The live stops that protect ``pos``: same pos_side (net/None for a net position) and, when the
    algo order records a side, the closing side (sell for a long)."""
    closing = "sell" if pos.direction > 0 else "buy"
    out: list[LiveStop] = []
    for s in stops:
        if pos.pos_side in ("long", "short"):
            if s.pos_side != pos.pos_side:
                continue
        elif s.pos_side not in (None, "", "net"):
            continue
        if s.side and s.side != closing:
            continue
        out.append(s)
    return out


def _env_state(ctx: ActionContext, cfg: Mapping[str, Any]) -> CheckResult:
    name = "env_state"
    kind = ctx.action.kind
    values: dict[str, Any] = {
        "paused": ctx.paused,
        "active_breakers": list(ctx.active_breakers),
        "connection_verified": ctx.connection_verified,
    }
    if not ctx.connection_verified:
        return CheckResult(
            name, False, "connection_unverified", f"No verified OKX connection for {ctx.action.env}.", values
        )
    halted = ctx.paused or bool(ctx.active_breakers)
    allowed = {str(k) for k in cfg["paused_allowed_kinds"]}
    if halted and kind not in allowed:
        if ctx.paused:
            why = f"Trading is paused ({ctx.pause_reason or 'no reason given'})"
            code = "trading_paused"
        else:
            why = f"Circuit breaker active: {', '.join(ctx.active_breakers)}"
            code = "circuit_breaker_active"
        return CheckResult(name, False, code, f"{why}; only risk-reducing actions pass.", values)
    if halted:
        values["note"] = "risk_reducing_allowed_while_halted"
        return CheckResult(name, True, None, f"Trading is halted, but a {kind} reduces risk.", values)
    return CheckResult(name, True, None, "Connection verified; trading is not halted.", values)


def _position_open(ctx: ActionContext, cfg: Mapping[str, Any]) -> tuple[CheckResult, HeldPosition | None]:
    name = "position_open"
    a = ctx.action
    max_age = int(cfg["positions_max_age_seconds"])
    values: dict[str, Any] = {
        "positions_taken_at": ctx.positions_taken_at,
        "max_age_seconds": max_age,
    }
    if ctx.positions_taken_at is None or (ctx.now - ctx.positions_taken_at).total_seconds() > max_age:
        return (
            CheckResult(
                name,
                False,
                "positions_stale",
                f"The positions snapshot is older than {max_age} s, so an open position on {a.inst_id} "
                "cannot be confirmed.",
                values,
            ),
            None,
        )
    found = list(ctx.positions)
    if a.side in ("buy", "sell"):
        want = 1 if a.side == "buy" else -1
        found = [p for p in found if p.direction == want]
        values["side"] = a.side
    if not found:
        return (
            CheckResult(
                name, False, "no_open_position", f"There is no open position on {a.inst_id}.", values
            ),
            None,
        )
    if len(found) > 1:
        values["pos_sides"] = [p.pos_side for p in found]
        return (
            CheckResult(
                name,
                False,
                "position_ambiguous",
                f"{a.inst_id} has {len(found)} open positions; the action does not say which one.",
                values,
            ),
            None,
        )
    pos = found[0]
    values.update(
        pos_side=pos.pos_side,
        direction="long" if pos.direction > 0 else "short",
        contracts=pos.contracts,
        avg_px=pos.avg_px,
    )
    return CheckResult(name, True, None, f"Open {values['direction']} position on {a.inst_id}.", values), pos


def _stop_tightens(ctx: ActionContext, pos: HeldPosition) -> tuple[CheckResult, Decimal | None]:
    name = "stop_tightens"
    stop = ctx.action.stop
    if stop is None or stop <= 0:
        return CheckResult(name, False, "stop_missing", "amend_stop has no stop price."), None
    stops = matching_stops(ctx.live_stops, pos)
    values: dict[str, Any] = {
        "new_stop": stop,
        "live_stops": [
            {"algo_id": s.algo_id, "algo_cl_ord_id": s.algo_cl_ord_id, "kind": s.kind, "price": s.price}
            for s in stops
        ],
    }
    if not stops:
        values["current_stop"] = None
        values["note"] = "no_live_stop"
        return (
            CheckResult(
                name,
                True,
                None,
                "No live exchange stop protects the position; any stop on the safe side of mark "
                "adds protection.",
                values,
            ),
            None,
        )
    current = max(s.price for s in stops) if pos.direction > 0 else min(s.price for s in stops)
    values["current_stop"] = current
    tighter = stop > current if pos.direction > 0 else stop < current
    if stop == current:
        return (
            CheckResult(
                name, False, "stop_unchanged", f"The new stop {stop} equals the live stop {current}.", values
            ),
            current,
        )
    if not tighter:
        return (
            CheckResult(
                name,
                False,
                "stop_widens",
                f"The new stop {stop} is wider than the live stop {current}; a stop may only tighten.",
                values,
            ),
            current,
        )
    return CheckResult(name, True, None, f"The stop tightens from {current} to {stop}.", values), current


def _stop_vs_mark(ctx: ActionContext, pos: HeldPosition) -> CheckResult:
    name = "stop_vs_mark"
    stop = ctx.action.stop
    assert stop is not None
    values: dict[str, Any] = {"new_stop": stop, "mark": ctx.mark, "mark_source": ctx.mark_source}
    if ctx.mark is None:
        return CheckResult(
            name, False, "mark_unknown", f"No fresh mark price for {ctx.action.inst_id}.", values
        )
    safe = stop < ctx.mark if pos.direction > 0 else stop > ctx.mark
    if not safe:
        side = "below" if pos.direction > 0 else "above"
        return CheckResult(
            name,
            False,
            "stop_beyond_mark",
            f"The new stop {stop} is not {side} the mark {ctx.mark}; it would trigger at once.",
            values,
        )
    return CheckResult(name, True, None, f"The stop {stop} is on the safe side of mark {ctx.mark}.", values)


def _targets_vs_mark(ctx: ActionContext, pos: HeldPosition) -> CheckResult:
    name = "targets_vs_mark"
    targets = ctx.action.targets
    values: dict[str, Any] = {"targets": list(targets), "mark": ctx.mark, "mark_source": ctx.mark_source}
    if not targets:
        return CheckResult(name, False, "targets_missing", "amend_targets has no targets.", values)
    if ctx.mark is None:
        return CheckResult(
            name, False, "mark_unknown", f"No fresh mark price for {ctx.action.inst_id}.", values
        )
    bad = [t for t in targets if (t <= ctx.mark if pos.direction > 0 else t >= ctx.mark)]
    if bad:
        values["wrong_side"] = bad
        side = "above" if pos.direction > 0 else "below"
        return CheckResult(
            name,
            False,
            "target_beyond_mark",
            f"Target(s) {', '.join(str(b) for b in bad)} are not {side} the mark {ctx.mark}.",
            values,
        )
    return CheckResult(name, True, None, f"Every target is on the profit side of mark {ctx.mark}.", values)


def _close_size(ctx: ActionContext, pos: HeldPosition) -> tuple[CheckResult, Decimal | None, Decimal | None]:
    name = "close_size"
    kind = ctx.action.kind
    pct = _HUNDRED if kind == "close" else ctx.action.close_pct
    values: dict[str, Any] = {"close_pct": pct, "position_contracts": pos.contracts}
    if pct is None:
        return (
            CheckResult(name, False, "close_pct_missing", "partial_close has no close_pct.", values),
            None,
            None,
        )
    if not (Decimal(0) < pct <= _HUNDRED):
        return (
            CheckResult(
                name,
                False,
                "close_pct_out_of_range",
                f"close_pct {pct} is outside (0, 100].",
                values,
            ),
            None,
            None,
        )
    contracts = pos.contracts * pct / _HUNDRED
    spec = ctx.spec
    if pct < _HUNDRED and spec is not None and spec.lot_sz > 0:
        contracts = (contracts / spec.lot_sz).to_integral_value(rounding=ROUND_DOWN) * spec.lot_sz
        if contracts < spec.min_sz or contracts <= 0:
            values.update(contracts=contracts, lot_sz=spec.lot_sz, min_sz=spec.min_sz)
            return (
                CheckResult(
                    name,
                    False,
                    "close_below_min_size",
                    f"{pct}% of {pos.contracts} contracts rounds down to {contracts}, below minSz "
                    f"{spec.min_sz}.",
                    values,
                ),
                None,
                None,
            )
    notional: Decimal | None = None
    if spec is not None:
        size = contracts * spec.ct_val * spec.ct_mult
        px = ctx.mark or pos.mark_px or pos.avg_px
        if spec.ct_type == "inverse":
            notional = size
        elif px is not None:
            notional = size * px
    values.update(contracts=contracts, notional_usd=notional)
    return (
        CheckResult(name, True, None, f"Close {contracts} of {pos.contracts} contracts ({pct}%).", values),
        contracts,
        notional,
    )


def run_position_checks(ctx: ActionContext, config: Mapping[str, Any] | None = None) -> ActionOutcome:
    """The seven position-action checks in order (pure)."""
    rules = config or rules_config()
    cfg = position_config(rules)
    kind = ctx.action.kind
    if kind not in POSITION_ACTION_KINDS:
        raise ValueError(f"{kind} is not a position action")
    applies = _ALWAYS | _APPLIES[kind]
    eng = EngineSettings.from_settings_data(ctx.settings_data)
    checks: list[dict[str, Any]] = []
    failed: CheckResult | None = None
    decision: Decision | None = None
    pos: HeldPosition | None = None
    current: Decimal | None = None
    contracts: Decimal | None = None
    notional: Decimal | None = None

    for n, name in enumerate(POSITION_CHECK_NAMES, start=1):
        if failed is not None:
            checks.append(not_run(n, name))
            continue
        if name not in applies:
            checks.append(_not_applicable(n, name, kind))
            continue
        if name == "env_state":
            res = _env_state(ctx, cfg)
        elif name == "position_open":
            res, pos = _position_open(ctx, cfg)
        elif name == "autonomy":
            res, decision = decide_autonomy(
                eng.autonomy_level, int(DEFAULTS["autonomy"]["max_enabled_level"])
            )
        else:
            assert pos is not None
            if name == "stop_tightens":
                res, current = _stop_tightens(ctx, pos)
            elif name == "stop_vs_mark":
                res = _stop_vs_mark(ctx, pos)
            elif name == "targets_vs_mark":
                res = _targets_vs_mark(ctx, pos)
            else:
                res, contracts, notional = _close_size(ctx, pos)
        checks.append(res.as_check(n))
        if not res.passed:
            failed = res

    if failed is not None:
        return ActionOutcome(checks, "rejected", None, failed.reason, failed.name, pos, current)
    assert pos is not None and decision is not None
    what = {
        "amend_stop": f"stop to {ctx.action.stop}",
        "amend_targets": "targets " + ", ".join(str(t) for t in ctx.action.targets),
        "partial_close": f"close {contracts} contracts",
        "close": f"close {contracts} contracts",
    }[kind]
    reason = f"Approved {kind} on {ctx.action.inst_id}: {what}."
    return ActionOutcome(checks, "approved", decision, reason, None, pos, current, contracts, notional)


# --- Loading (I/O) -----------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    """A finite Decimal without trailing zeros (numeric(38,18) columns come back as 3900.000...)."""
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except ArithmeticError:
        return None
    return Decimal(format(d.normalize(), "f")) if d.is_finite() else None


def action_info(row: Proposal) -> ActionInfo:
    targets: list[Decimal] = []
    for t in row.take_profits or []:
        px = _dec(t.get("price")) if isinstance(t, Mapping) else None
        if px is not None:
            targets.append(px)
    return ActionInfo(
        id=row.id,
        env=row.env,
        kind=row.kind,
        inst_id=row.inst_id,
        side=row.side if row.side in ("buy", "sell") else None,
        stop=_dec(row.stop),
        targets=tuple(targets),
        close_pct=_dec(row.close_pct),
        correlation_id=str(row.cycle_id) if row.cycle_id else None,
    )


def load_action_context(
    session: Session,
    rds: Any,
    proposal: Proposal,
    now: datetime,
    *,
    settings_version: int,
    settings_data: Mapping[str, Any],
    paused: bool,
    pause_reason: str | None,
    active_breakers: tuple[str, ...],
    config: Mapping[str, Any] | None = None,
) -> ActionContext:
    rules = config or rules_config()
    cfg = position_config(rules)
    a = action_info(proposal)
    env = a.env
    verified = (
        session.scalar(
            select(func.count())
            .select_from(Connection)
            .where(
                Connection.exchange == "okx", Connection.env == env, Connection.last_verified_at.is_not(None)
            )
        )
        or 0
    )
    latest = session.scalar(select(func.max(PositionSnapshot.taken_at)).where(PositionSnapshot.env == env))
    positions: list[HeldPosition] = []
    if latest is not None:
        for row in session.scalars(
            select(PositionSnapshot).where(
                PositionSnapshot.env == env,
                PositionSnapshot.taken_at == latest,
                PositionSnapshot.inst_id == a.inst_id,
                PositionSnapshot.pos != 0,
            )
        ):
            if row.pos_side == "long":
                direction = 1
            elif row.pos_side == "short":
                direction = -1
            else:
                direction = 1 if row.pos > 0 else -1
            positions.append(
                HeldPosition(
                    row.inst_id,
                    row.pos_side,
                    direction,
                    abs(_dec(row.pos) or Decimal(0)),
                    _dec(row.avg_px),
                    _dec(row.mark_px),
                    row.taken_at,
                )
            )
    stops: list[LiveStop] = []
    for algo in session.scalars(
        select(AlgoOrder).where(
            AlgoOrder.env == env,
            AlgoOrder.inst_id == a.inst_id,
            AlgoOrder.kind.in_([str(k) for k in cfg["stop_kinds"]]),
            AlgoOrder.state.in_([str(s) for s in cfg["protecting_states"]]),
        )
    ):
        price = _dec(algo.sl_trigger_px if algo.kind == "oco" else algo.trigger_px)
        if price is None:
            continue
        stops.append(LiveStop(algo.algo_id, algo.algo_cl_ord_id, algo.kind, price, algo.pos_side, algo.side))

    eng = EngineSettings.from_settings_data(settings_data)
    snap = read_snapshot(rds, env, a.inst_id)
    mark: Decimal | None = None
    source: str | None = None
    ticker_at = snap.channel_at.get("tickers") or snap.channel_at.get("mark")
    if ticker_at is not None and (now - ticker_at) <= timedelta(seconds=eng.market_data_max_age_seconds):
        mark = _dec((snap.mark or {}).get("markPx")) or _dec((snap.ticker or {}).get("last"))
        source = "market_data" if mark is not None else None
    if mark is None and len(positions) == 1 and positions[0].mark_px:
        pos_age = (now - positions[0].taken_at).total_seconds()
        if pos_age <= int(cfg["positions_max_age_seconds"]):
            mark, source = positions[0].mark_px, "positions_snapshot"

    spec: CloseSpec | None = None
    inst = session.scalar(select(Instrument).where(Instrument.env == env, Instrument.inst_id == a.inst_id))
    if inst is not None:
        spec = CloseSpec(
            ct_type=infer_contract_type(inst.inst_type, inst.base_ccy, inst.settle_ccy),
            ct_val=(_dec(inst.ct_val) if inst.ct_val and inst.ct_val > 0 else None) or Decimal(1),
            ct_mult=(_dec(inst.ct_mult) if inst.ct_mult and inst.ct_mult > 0 else None) or Decimal(1),
            lot_sz=_dec(inst.lot_sz) or Decimal(0),
            min_sz=_dec(inst.min_sz) or Decimal(0),
        )

    return ActionContext(
        now=now,
        action=a,
        settings_version=settings_version,
        settings_data=settings_data,
        paused=paused,
        pause_reason=pause_reason,
        active_breakers=active_breakers,
        connection_verified=verified > 0,
        positions=tuple(positions),
        positions_taken_at=latest,
        live_stops=tuple(stops),
        mark=mark,
        mark_source=source,
        spec=spec,
    )


def settings_row(session: Session, env: str) -> tuple[int, Mapping[str, Any]] | None:
    row = session.execute(text("SELECT version, data FROM settings WHERE env = :env"), {"env": env}).first()
    if row is None or not isinstance(row[1], dict):
        return None
    return int(row[0]), row[1]


__all__ = [
    "POSITION_CHECK_NAMES",
    "ActionContext",
    "ActionInfo",
    "ActionOutcome",
    "CloseSpec",
    "HeldPosition",
    "LiveStop",
    "action_info",
    "load_action_context",
    "matching_stops",
    "position_config",
    "run_position_checks",
    "settings_row",
]
