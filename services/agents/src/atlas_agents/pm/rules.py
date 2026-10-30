"""Position management rules, computed in code (brief §7.8, decision #249 J; ATL-168).

Pure and deterministic: no database, no clock, no network. The caller (the Portfolio Manager's
5-minute check) loads the position, its proposal, ATR(1H), the last 1H swing, the events and the
funding rate, and :func:`evaluate` says which rules fire. The PM proposes the resulting actions,
the Risk Engine validates them and the Executor acts.

The rules run in the brief's order:

1. ``breakeven`` — at +1R, move the stop to entry + round-trip fees.
2. ``tp1_partial`` / ``tp1_trail`` — at TP1, close the configured % and trail the stop at
   max(1.5 x ATR(1H), the distance to the last 1H swing).
3. ``two_r_trail`` — at +2R with TP2 not hit, trail at 1.0 x ATR(1H).
4. ``time_stop`` — close when neither the stop nor TP1 was hit by the time stop.
5. ``invalidation`` — close when the latest structure report for the instrument says "broken".
6. ``event_reduce`` — with close_before_high_impact_events on, reduce (default halve) a position
   with less than 1R profit inside the lead window before an importance-5 event.
7. ``funding_cost`` — funding against the position above 30 % a year, open >= 8 h and flat: a
   flag plus suggest_close.

Stops only tighten. Every candidate stop is rounded toward the market to tickSz and kept only if
it is strictly tighter than the current stop and still on the safe side of the mark; anything else
is dropped with a reason (``Evaluation.dropped``). Of several stop candidates the tightest wins;
a full close supersedes stop amendments and partial closes.

R is measured on the proposal's original stop: 1R = |entry - original stop|. A position with no
proposal (imported as external) has no R, so rules 1-4 do not run for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS
from atlas_shared.proposals import EvidenceRef, PositionAction, TakeProfit, TradeProposal

Side = Literal["long", "short"]
ActionKind = Literal["amend_stop", "partial_close", "close", "flag", "suggest_close"]
StructureStatus = Literal["intact", "weakening", "broken"]

RULE_ORDER: Final[tuple[str, ...]] = (
    "breakeven",
    "tp1_partial",
    "tp1_trail",
    "two_r_trail",
    "time_stop",
    "invalidation",
    "event_reduce",
    "funding_cost",
)
"""Every rule name an action can carry, in the order they are applied."""

PROPOSAL_KINDS: Final[frozenset[str]] = frozenset({"amend_stop", "partial_close", "close"})
"""Action kinds that map onto a PositionAction proposal; flag and suggest_close are advisory."""

_ZERO: Final = Decimal(0)
_ONE: Final = Decimal(1)
_HUNDRED: Final = Decimal(100)


# --------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionState:
    """An open position as the DB knows it (positions snapshot + its algo orders, ATL-142)."""

    inst_id: str
    side: Side
    entry_price: Decimal
    mark_price: Decimal
    stop: Decimal | None
    """The stop resting on OKX now (None only for an unprotected position)."""
    opened_at: datetime
    tick_sz: Decimal | None = None
    tp1_filled: bool = False
    """OKX already closed the TP1 portion."""
    tp1_resting: bool = False
    """A TP1 order rests on OKX, so the exchange takes the partial itself."""
    tp2_filled: bool = False
    applied_rules: frozenset[str] = frozenset()
    """One-shot rule keys already acted on, e.g. ``event_reduce:<event id>``."""


@dataclass(frozen=True)
class ProposalPlan:
    """The parts of the opening proposal the rules need."""

    stop: Decimal
    """The original stop: it defines 1R."""
    take_profits: tuple[TakeProfit, ...] = ()
    time_stop_minutes: int | None = None
    proposal_id: str | None = None

    @classmethod
    def from_trade_proposal(cls, proposal: TradeProposal, proposal_id: str | None = None) -> ProposalPlan:
        return cls(
            stop=proposal.stop,
            take_profits=tuple(proposal.take_profits),
            time_stop_minutes=proposal.time_stop_minutes,
            proposal_id=proposal_id,
        )


@dataclass(frozen=True)
class StructureReport:
    """The Technical Analyst's latest read of the structure behind the trade."""

    status: StructureStatus
    as_of: datetime
    source_id: str | None = None


@dataclass(frozen=True)
class EventInfo:
    """A scheduled event that affects the instrument (economic_events / earnings_events)."""

    id: str
    name: str
    scheduled_at: datetime
    importance: int


@dataclass(frozen=True)
class FundingInfo:
    """The current funding rate (per settlement, positive = longs pay) and the settlement interval."""

    rate: Decimal
    interval_hours: Decimal = Decimal(8)
    as_of: datetime | None = None


@dataclass(frozen=True)
class RuleSettings:
    """agents.portfolio_manager.rules, with the owner's settings-table overrides applied."""

    breakeven_at_r: Decimal
    fee_rate: Decimal
    tp1_close_pct: Decimal
    tp1_trail_atr_mult: Decimal
    two_r_at: Decimal
    two_r_trail_atr_mult: Decimal
    default_time_stop_minutes: int
    structure_max_age_minutes: int
    close_before_high_impact_events: bool
    event_lead_minutes: int
    event_min_importance: int
    event_max_profit_r: Decimal
    event_close_pct: Decimal
    funding_max_annual_pct: Decimal
    funding_flat_after_hours: Decimal
    funding_flat_r: Decimal

    @classmethod
    def from_config(
        cls,
        overrides: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] = DEFAULTS,
    ) -> RuleSettings:
        """Read the defaults, then apply ``overrides`` (same keys; e.g. the settings-table value of
        close_before_high_impact_events, or the instrument's taker fee as fee_rate)."""
        raw: dict[str, Any] = dict(defaults["agents"]["portfolio_manager"]["rules"])
        unknown = set(overrides or {}) - set(raw)
        if unknown:
            raise KeyError(f"unknown portfolio_manager rule settings: {sorted(unknown)}")
        raw.update(overrides or {})

        def dec(key: str) -> Decimal:
            return Decimal(str(raw[key]))

        return cls(
            breakeven_at_r=dec("breakeven_at_r"),
            fee_rate=dec("fee_rate"),
            tp1_close_pct=dec("tp1_close_pct"),
            tp1_trail_atr_mult=dec("tp1_trail_atr_mult"),
            two_r_at=dec("two_r_at"),
            two_r_trail_atr_mult=dec("two_r_trail_atr_mult"),
            default_time_stop_minutes=int(raw["default_time_stop_minutes"]),
            structure_max_age_minutes=int(raw["structure_max_age_minutes"]),
            close_before_high_impact_events=bool(raw["close_before_high_impact_events"]),
            event_lead_minutes=int(raw["event_lead_minutes"]),
            event_min_importance=int(raw["event_min_importance"]),
            event_max_profit_r=dec("event_max_profit_r"),
            event_close_pct=dec("event_close_pct"),
            funding_max_annual_pct=dec("funding_max_annual_pct"),
            funding_flat_after_hours=dec("funding_flat_after_hours"),
            funding_flat_r=dec("funding_flat_r"),
        )


# --------------------------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleAction:
    """One thing a rule says should happen. ``stop`` for amend_stop, ``close_pct`` for
    partial_close; ``data`` carries the numbers behind it."""

    kind: ActionKind
    rule: str
    inst_id: str
    reason: str
    stop: Decimal | None = None
    close_pct: Decimal | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_position_action(self, evidence: Sequence[EvidenceRef] = ()) -> PositionAction:
        """The PositionAction proposal for amend_stop / partial_close / close (ATL-153 shape)."""
        if self.kind not in PROPOSAL_KINDS:
            raise ValueError(f"{self.kind} is advisory; it is not a position proposal")
        return PositionAction(
            inst_id=self.inst_id,
            stop=self.stop,
            close_pct=self.close_pct,
            rule=self.rule,
            rationale_md=self.reason,
            evidence=list(evidence),
        )


@dataclass(frozen=True)
class DroppedAction:
    """A rule result that was not kept, and why (``widens_stop``, ``crosses_mark``,
    ``superseded_by:<rule>``, ``no_atr`` …)."""

    rule: str
    reason: str
    stop: Decimal | None = None


@dataclass(frozen=True)
class Evaluation:
    actions: tuple[RuleAction, ...]
    dropped: tuple[DroppedAction, ...]
    r_multiple: Decimal | None


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _round_toward_market(price: Decimal, side: Side, tick: Decimal | None) -> Decimal:
    """Round a stop to tickSz on the tighter side (up for a long's stop, down for a short's)."""
    if tick is None or tick <= 0:
        return price
    mode = ROUND_CEILING if side == "long" else ROUND_FLOOR
    return (price / tick).to_integral_value(rounding=mode) * tick


def is_tighter(side: Side, new_stop: Decimal, current: Decimal | None) -> bool:
    """True when ``new_stop`` is strictly closer to the market than ``current``."""
    if current is None:
        return True
    return new_stop > current if side == "long" else new_stop < current


def _on_safe_side(side: Side, stop: Decimal, mark: Decimal) -> bool:
    return stop < mark if side == "long" else stop > mark


def r_multiple(position: PositionState, plan: ProposalPlan | None) -> Decimal | None:
    """Open profit in R at the mark, on the original stop. None without a usable plan."""
    if plan is None:
        return None
    risk = abs(position.entry_price - plan.stop)
    if risk <= 0:
        return None
    move = position.mark_price - position.entry_price
    if position.side == "short":
        move = -move
    return move / risk


def annualised_funding_pct(funding: FundingInfo) -> Decimal:
    """|rate| x settlements per day x 365, in %."""
    if funding.interval_hours <= 0:
        return _ZERO
    return abs(funding.rate) * (Decimal(24) / funding.interval_hours) * Decimal(365) * _HUNDRED


def funding_against(side: Side, funding: FundingInfo) -> bool:
    """Positive funding: longs pay shorts; negative: shorts pay longs."""
    return funding.rate > 0 if side == "long" else funding.rate < 0


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


# --------------------------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------------------------


@dataclass
class _Run:
    position: PositionState
    plan: ProposalPlan | None
    settings: RuleSettings
    r: Decimal | None
    candidates: list[RuleAction] = field(default_factory=list)
    dropped: list[DroppedAction] = field(default_factory=list)

    def stop_candidate(self, rule: str, raw_stop: Decimal, reason: str, data: Mapping[str, Any]) -> None:
        pos = self.position
        stop = _round_toward_market(raw_stop, pos.side, pos.tick_sz)
        if not _on_safe_side(pos.side, stop, pos.mark_price):
            self.dropped.append(DroppedAction(rule, "crosses_mark", stop))
            return
        if pos.stop is not None and stop == pos.stop:
            return  # already there: nothing to do, nothing to report
        if not is_tighter(pos.side, stop, pos.stop):
            self.dropped.append(DroppedAction(rule, "widens_stop", stop))
            return
        self.candidates.append(
            RuleAction("amend_stop", rule, pos.inst_id, reason, stop=stop, data=dict(data))
        )

    def add(self, action: RuleAction) -> None:
        self.candidates.append(action)


def _tp_price(plan: ProposalPlan | None, index: int) -> TakeProfit | None:
    if plan is None or len(plan.take_profits) <= index:
        return None
    return plan.take_profits[index]


def _reached(side: Side, mark: Decimal, price: Decimal) -> bool:
    return mark >= price if side == "long" else mark <= price


def _rule_breakeven(run: _Run) -> None:
    pos, s = run.position, run.settings
    if run.r is None or run.r < s.breakeven_at_r:
        return
    fees = pos.entry_price * s.fee_rate * 2
    target = pos.entry_price + fees if pos.side == "long" else pos.entry_price - fees
    run.stop_candidate(
        "breakeven",
        target,
        f"+{_fmt(run.r.quantize(Decimal('0.01')))}R: stop to breakeven + fees",
        {"r": run.r, "fees_per_unit": fees},
    )


def _trail_stop(side: Side, mark: Decimal, distance: Decimal) -> Decimal:
    return mark - distance if side == "long" else mark + distance


def _rule_tp1(run: _Run, atr_1h: Decimal | None, last_swing: Decimal | None) -> bool:
    """Returns whether TP1 counts as hit (for the time stop)."""
    pos, s = run.position, run.settings
    tp1 = _tp_price(run.plan, 0)
    reached = pos.tp1_filled or (tp1 is not None and _reached(pos.side, pos.mark_price, tp1.price))
    if not reached:
        return False
    if not pos.tp1_filled and not pos.tp1_resting and tp1 is not None:
        pct = tp1.pct if tp1.pct < _HUNDRED else s.tp1_close_pct
        run.add(
            RuleAction(
                "partial_close",
                "tp1_partial",
                pos.inst_id,
                f"TP1 {_fmt(tp1.price)} reached: close {_fmt(pct)}%",
                close_pct=pct,
                data={"tp1": tp1.price},
            )
        )
    if atr_1h is None or atr_1h <= 0:
        run.dropped.append(DroppedAction("tp1_trail", "no_atr"))
        return True
    distance = s.tp1_trail_atr_mult * atr_1h
    swing_ok = last_swing is not None and _on_safe_side(pos.side, last_swing, pos.mark_price)
    if last_swing is not None and swing_ok:
        distance = max(distance, abs(pos.mark_price - last_swing))
    run.stop_candidate(
        "tp1_trail",
        _trail_stop(pos.side, pos.mark_price, distance),
        f"TP1 hit: trail at max({_fmt(s.tp1_trail_atr_mult)} x ATR(1H), last 1H swing)",
        {"atr_1h": atr_1h, "last_swing": last_swing if swing_ok else None, "distance": distance},
    )
    return True


def _rule_two_r(run: _Run, atr_1h: Decimal | None) -> None:
    pos, s = run.position, run.settings
    if run.r is None or run.r < s.two_r_at or pos.tp2_filled:
        return
    if atr_1h is None or atr_1h <= 0:
        run.dropped.append(DroppedAction("two_r_trail", "no_atr"))
        return
    distance = s.two_r_trail_atr_mult * atr_1h
    run.stop_candidate(
        "two_r_trail",
        _trail_stop(pos.side, pos.mark_price, distance),
        f"+{_fmt(run.r.quantize(Decimal('0.01')))}R without TP2: trail at "
        f"{_fmt(s.two_r_trail_atr_mult)} x ATR(1H)",
        {"r": run.r, "atr_1h": atr_1h, "distance": distance},
    )


def _rule_time_stop(run: _Run, tp1_hit: bool, now: datetime) -> None:
    if run.plan is None or tp1_hit:
        return
    minutes = run.plan.time_stop_minutes or run.settings.default_time_stop_minutes
    held = now - run.position.opened_at
    if held < timedelta(minutes=minutes):
        return
    run.add(
        RuleAction(
            "close",
            "time_stop",
            run.position.inst_id,
            f"Time stop: neither the stop nor TP1 hit within {minutes} min",
            data={"time_stop_minutes": minutes, "held_minutes": int(held.total_seconds() // 60)},
        )
    )


def _rule_invalidation(run: _Run, structure: StructureReport | None, now: datetime) -> None:
    if structure is None or structure.status != "broken":
        return
    if now - structure.as_of > timedelta(minutes=run.settings.structure_max_age_minutes):
        run.dropped.append(DroppedAction("invalidation", "structure_report_stale"))
        return
    run.add(
        RuleAction(
            "close",
            "invalidation",
            run.position.inst_id,
            "Invalidation: the Technical Analyst reports the structure broken",
            data={"structure_as_of": structure.as_of, "source_id": structure.source_id},
        )
    )


def _rule_event(run: _Run, events: Iterable[EventInfo], now: datetime) -> None:
    s, pos = run.settings, run.position
    if not s.close_before_high_impact_events:
        return
    if run.r is not None and run.r >= s.event_max_profit_r:
        return
    lead = timedelta(minutes=s.event_lead_minutes)
    upcoming = sorted(
        (
            e
            for e in events
            if e.importance >= s.event_min_importance and now <= e.scheduled_at <= now + lead
        ),
        key=lambda e: (e.scheduled_at, e.id),
    )
    for event in upcoming:
        key = f"event_reduce:{event.id}"
        if key in pos.applied_rules:
            continue
        minutes = int((event.scheduled_at - now).total_seconds() // 60)
        full = s.event_close_pct >= _HUNDRED
        run.add(
            RuleAction(
                "close" if full else "partial_close",
                "event_reduce",
                pos.inst_id,
                f"{event.name} (importance {event.importance}) in {minutes} min with under "
                f"{_fmt(s.event_max_profit_r)}R profit: "
                + ("close" if full else f"reduce {_fmt(s.event_close_pct)}%"),
                close_pct=None if full else s.event_close_pct,
                data={"event_id": event.id, "applied_key": key, "r": run.r},
            )
        )
        return


def _rule_funding(run: _Run, funding: FundingInfo | None, now: datetime) -> None:
    s, pos = run.settings, run.position
    if funding is None or not funding_against(pos.side, funding):
        return
    annual = annualised_funding_pct(funding)
    if annual <= s.funding_max_annual_pct:
        return
    held_hours = Decimal((now - pos.opened_at).total_seconds()) / Decimal(3600)
    if held_hours < s.funding_flat_after_hours:
        return
    if run.r is None or abs(run.r) > s.funding_flat_r:
        return
    data = {"annualised_pct": annual, "held_hours": held_hours, "r": run.r}
    pct = _fmt(annual.quantize(Decimal("0.1")))
    run.add(
        RuleAction(
            "flag",
            "funding_cost",
            pos.inst_id,
            f"Funding against the {pos.side} costs {pct}% a year",
            data=data,
        )
    )
    run.add(
        RuleAction(
            "suggest_close",
            "funding_cost",
            pos.inst_id,
            f"Flat after {int(held_hours)} h while paying {pct}% a year in funding: consider closing",
            data=data,
        )
    )


def _consolidate(run: _Run) -> tuple[RuleAction, ...]:
    """One stop amendment (the tightest), one size reduction (the largest), and a full close
    supersedes both. Advisory actions are always kept."""
    side = run.position.side
    closes = [a for a in run.candidates if a.kind == "close"]
    stops = [a for a in run.candidates if a.kind == "amend_stop"]
    partials = [a for a in run.candidates if a.kind == "partial_close"]
    keep: set[int] = {id(a) for a in run.candidates if a.kind in ("flag", "suggest_close")}

    if closes:
        winner = closes[0]
        keep.add(id(winner))
        for a in [*closes[1:], *stops, *partials]:
            run.dropped.append(DroppedAction(a.rule, f"superseded_by:{winner.rule}", a.stop))
    else:
        if stops:
            best = stops[0]
            for a in stops[1:]:
                assert a.stop is not None and best.stop is not None
                if is_tighter(side, a.stop, best.stop):
                    best = a
            keep.add(id(best))
            for a in stops:
                if a is not best:
                    run.dropped.append(DroppedAction(a.rule, f"superseded_by:{best.rule}", a.stop))
        if partials:
            top = partials[0]
            for a in partials[1:]:
                if (a.close_pct or _ZERO) > (top.close_pct or _ZERO):
                    top = a
            keep.add(id(top))
            for a in partials:
                if a is not top:
                    run.dropped.append(DroppedAction(a.rule, f"superseded_by:{top.rule}"))

    kept = [a for a in run.candidates if id(a) in keep]
    return tuple(sorted(kept, key=lambda a: RULE_ORDER.index(a.rule)))


# --------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------


def evaluate(
    position: PositionState,
    proposal: ProposalPlan | None,
    atr_1h: Decimal | None,
    last_swing: Decimal | None,
    events: Iterable[EventInfo],
    funding: FundingInfo | None,
    now: datetime,
    settings: RuleSettings,
    structure: StructureReport | None = None,
) -> list[RuleAction]:
    """The actions to propose (empty when no rule fires)."""
    return list(
        evaluate_full(position, proposal, atr_1h, last_swing, events, funding, now, settings, structure).actions
    )


def evaluate_full(
    position: PositionState,
    proposal: ProposalPlan | None,
    atr_1h: Decimal | None,
    last_swing: Decimal | None,
    events: Iterable[EventInfo],
    funding: FundingInfo | None,
    now: datetime,
    settings: RuleSettings,
    structure: StructureReport | None = None,
) -> Evaluation:
    """Like :func:`evaluate`, plus what was dropped and the R multiple used."""
    run = _Run(position=position, plan=proposal, settings=settings, r=r_multiple(position, proposal))
    _rule_breakeven(run)
    tp1_hit = _rule_tp1(run, atr_1h, last_swing)
    _rule_two_r(run, atr_1h)
    _rule_time_stop(run, tp1_hit, now)
    _rule_invalidation(run, structure, now)
    _rule_event(run, events, now)
    _rule_funding(run, funding, now)
    actions = _consolidate(run)
    return Evaluation(actions=actions, dropped=tuple(run.dropped), r_multiple=run.r)


__all__ = [
    "PROPOSAL_KINDS",
    "RULE_ORDER",
    "ActionKind",
    "DroppedAction",
    "Evaluation",
    "EventInfo",
    "FundingInfo",
    "PositionState",
    "ProposalPlan",
    "RuleAction",
    "RuleSettings",
    "Side",
    "StructureReport",
    "StructureStatus",
    "annualised_funding_pct",
    "evaluate",
    "evaluate_full",
    "funding_against",
    "is_tighter",
    "r_multiple",
]
