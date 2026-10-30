"""ATL-168: the Portfolio Manager's position management rules (brief §7.8), pure code."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_agents.pm.rules import (
    EventInfo,
    FundingInfo,
    PositionState,
    ProposalPlan,
    RuleSettings,
    Side,
    StructureReport,
    annualised_funding_pct,
    evaluate,
    evaluate_full,
)
from atlas_shared.proposals import TakeProfit
from hypothesis import given, settings
from hypothesis import strategies as st

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
D = Decimal
CFG = RuleSettings.from_config()


def long_position(
    mark: str, *, stop: str = "98", opened_minutes_ago: int = 60, **kw: object
) -> PositionState:
    return PositionState(
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_price=D("100"),
        mark_price=D(mark),
        stop=D(stop),
        opened_at=NOW - timedelta(minutes=opened_minutes_ago),
        **kw,  # type: ignore[arg-type]
    )


def short_position(
    mark: str, *, stop: str = "102", opened_minutes_ago: int = 60, **kw: object
) -> PositionState:
    return PositionState(
        inst_id="ETH-USDT-SWAP",
        side="short",
        entry_price=D("100"),
        mark_price=D(mark),
        stop=D(stop),
        opened_at=NOW - timedelta(minutes=opened_minutes_ago),
        **kw,  # type: ignore[arg-type]
    )


LONG_PLAN = ProposalPlan(
    stop=D("98"),
    take_profits=(TakeProfit(price=D("104"), pct=D("50")), TakeProfit(price=D("106"), pct=D("30"))),
    time_stop_minutes=1440,
)
SHORT_PLAN = ProposalPlan(
    stop=D("102"),
    take_profits=(TakeProfit(price=D("96"), pct=D("50")),),
    time_stop_minutes=1440,
)


def run(position: PositionState, plan: ProposalPlan | None = LONG_PLAN, **kw: object) -> list:  # type: ignore[type-arg]
    args: dict[str, object] = {
        "atr_1h": D("1"),
        "last_swing": None,
        "events": (),
        "funding": None,
        "now": NOW,
        "settings": CFG,
    }
    args.update(kw)
    return evaluate(position, plan, **args)  # type: ignore[arg-type]


def test_thresholds_come_from_defaults() -> None:
    assert CFG.breakeven_at_r == D("1")
    assert CFG.tp1_trail_atr_mult == D("1.5")
    assert CFG.two_r_trail_atr_mult == D("1.0")
    assert CFG.funding_max_annual_pct == D("30")
    assert CFG.close_before_high_impact_events is True
    assert (
        RuleSettings.from_config({"close_before_high_impact_events": False}).close_before_high_impact_events
        is False
    )
    with pytest.raises(KeyError):
        RuleSettings.from_config({"no_such_rule": 1})


# AC1 ---------------------------------------------------------------------------------------


def test_plus_one_r_moves_the_stop_to_breakeven_plus_fees() -> None:
    actions = run(long_position("102.1"))
    assert len(actions) == 1
    (a,) = actions
    assert a.kind == "amend_stop"
    assert a.rule == "breakeven"
    # 100 + 100 x 0.0005 x 2 round-trip fees
    assert a.stop == D("100.1")
    assert isinstance(a.stop, Decimal)
    pa = a.to_position_action()
    assert pa.stop == D("100.1") and pa.rule == "breakeven"


def test_breakeven_uses_the_callers_fee_and_rounds_toward_the_market() -> None:
    cfg = RuleSettings.from_config({"fee_rate": "0.0006"})
    pos = long_position("102.1", tick_sz=D("0.5"))
    (a,) = run(pos, settings=cfg)
    assert a.stop == D("100.5")  # 100.12 rounded up to the 0.5 tick


def test_short_breakeven_is_below_entry() -> None:
    (a,) = run(short_position("97.9"), SHORT_PLAN)
    assert (a.rule, a.stop) == ("breakeven", D("99.9"))


def test_breakeven_already_in_place_fires_nothing() -> None:
    assert run(long_position("102.1", stop="100.1")) == []


def test_below_one_r_fires_nothing() -> None:
    assert run(long_position("101.9")) == []


# Rules 2 and 3 ------------------------------------------------------------------------------


def test_tp1_closes_the_configured_pct_and_trails_at_the_wider_of_atr_and_swing() -> None:
    pos = long_position("104.2", stop="100.1")
    ev = evaluate_full(pos, LONG_PLAN, D("1"), D("102"), (), None, NOW, CFG)
    kinds = [(a.kind, a.rule) for a in ev.actions]
    assert ("partial_close", "tp1_partial") in kinds
    partial = next(a for a in ev.actions if a.kind == "partial_close")
    assert partial.close_pct == D("50")
    stop = next(a for a in ev.actions if a.kind == "amend_stop")
    # +2.1R also runs rule 3 (104.2 - 1.0 ATR = 103.2), which is tighter than the TP1 trail
    # (wider of 1.5 ATR = 102.7 and the swing at 102 → 102): the tightest stop wins.
    assert (stop.rule, stop.stop) == ("two_r_trail", D("103.2"))
    assert any(d.rule == "tp1_trail" and d.reason == "superseded_by:two_r_trail" for d in ev.dropped)


def test_tp1_trail_alone_uses_the_wider_distance() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=(TakeProfit(price=D("103"), pct=D("40")),))
    pos = long_position("103.5", stop="100.1", tp1_filled=True)
    (a,) = run(pos, plan, last_swing=D("101.5"))
    # 1.5 x ATR = 1.5 → 102.0; swing distance 2.0 → 101.5; the wider distance wins.
    assert (a.kind, a.rule, a.stop) == ("amend_stop", "tp1_trail", D("101.5"))


def test_tp1_resting_on_okx_is_not_closed_again() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=(TakeProfit(price=D("103"), pct=D("40")),))
    pos = long_position("103.5", stop="100.1", tp1_resting=True)
    assert all(a.kind != "partial_close" for a in run(pos, plan))


def test_two_r_without_tp2_trails_at_one_atr() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=(TakeProfit(price=D("110"), pct=D("50")),))
    (a,) = run(long_position("104.5", stop="100.1"), plan, atr_1h=D("1.2"))
    assert (a.rule, a.stop) == ("two_r_trail", D("103.3"))


def test_a_looser_trail_is_dropped_with_a_reason() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=(TakeProfit(price=D("110"), pct=D("50")),))
    ev = evaluate_full(long_position("104.5", stop="104"), plan, D("1"), None, (), None, NOW, CFG)
    assert ev.actions == ()
    assert any(d.rule == "two_r_trail" and d.reason == "widens_stop" for d in ev.dropped)


# AC3 ---------------------------------------------------------------------------------------


def test_time_stop_closes() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=LONG_PLAN.take_profits, time_stop_minutes=240)
    (a,) = run(long_position("100.5", opened_minutes_ago=241), plan)
    assert (a.kind, a.rule) == ("close", "time_stop")
    assert run(long_position("100.5", opened_minutes_ago=239), plan) == []


def test_time_stop_does_not_fire_after_tp1() -> None:
    plan = ProposalPlan(stop=D("98"), take_profits=LONG_PLAN.take_profits, time_stop_minutes=240)
    actions = run(long_position("100.5", stop="100.1", opened_minutes_ago=300, tp1_filled=True), plan)
    assert all(a.rule != "time_stop" for a in actions)


def test_invalidation_closes() -> None:
    broken = StructureReport(status="broken", as_of=NOW - timedelta(minutes=5), source_id="m-1")
    (a,) = run(long_position("100.5"), structure=broken)
    assert (a.kind, a.rule) == ("close", "invalidation")
    intact = StructureReport(status="weakening", as_of=NOW)
    assert run(long_position("100.5"), structure=intact) == []
    stale = StructureReport(status="broken", as_of=NOW - timedelta(hours=5))
    assert run(long_position("100.5"), structure=stale) == []


def test_a_close_supersedes_a_stop_amendment() -> None:
    broken = StructureReport(status="broken", as_of=NOW)
    ev = evaluate_full(long_position("102.1"), LONG_PLAN, D("1"), None, (), None, NOW, CFG, broken)
    assert [(a.kind, a.rule) for a in ev.actions] == [("close", "invalidation")]
    assert any(d.rule == "breakeven" and d.reason == "superseded_by:invalidation" for d in ev.dropped)


CPI = EventInfo(id="ev-1", name="US CPI", scheduled_at=NOW + timedelta(minutes=10), importance=5)


def test_event_ten_minutes_away_halves_a_half_r_position() -> None:
    (a,) = run(long_position("101"), events=[CPI])  # +0.5R
    assert (a.kind, a.rule, a.close_pct) == ("partial_close", "event_reduce", D("50"))


def test_event_rule_off_or_far_or_minor_or_done_or_profitable_fires_nothing() -> None:
    off = RuleSettings.from_config({"close_before_high_impact_events": False})
    assert run(long_position("101"), events=[CPI], settings=off) == []
    far = EventInfo(id="ev-2", name="FOMC", scheduled_at=NOW + timedelta(minutes=30), importance=5)
    assert run(long_position("101"), events=[far]) == []
    minor = EventInfo(id="ev-3", name="PMI", scheduled_at=NOW + timedelta(minutes=10), importance=4)
    assert run(long_position("101"), events=[minor]) == []
    done = long_position("101", applied_rules=frozenset({"event_reduce:ev-1"}))
    assert run(done, events=[CPI]) == []
    # +1.05R → breakeven only, no event reduction
    assert [a.rule for a in run(long_position("102.1"), events=[CPI])] == ["breakeven"]


def test_event_close_pct_100_closes() -> None:
    cfg = RuleSettings.from_config({"event_close_pct": "100"})
    (a,) = run(long_position("101"), events=[CPI], settings=cfg)
    assert (a.kind, a.rule, a.close_pct) == ("close", "event_reduce", None)


# AC4 ---------------------------------------------------------------------------------------

RATE_35PCT = D("-0.35") / D("1095")  # negative funding: shorts pay; 3 settlements a day


def test_funding_against_a_flat_short_after_nine_hours_flags_and_suggests_close() -> None:
    funding = FundingInfo(rate=RATE_35PCT, interval_hours=D(8))
    assert annualised_funding_pct(funding).quantize(D("0.01")) == D("35.00")
    pos = short_position("100.1", opened_minutes_ago=9 * 60)
    actions = run(pos, SHORT_PLAN, funding=funding)
    assert [(a.kind, a.rule) for a in actions] == [
        ("flag", "funding_cost"),
        ("suggest_close", "funding_cost"),
    ]
    with pytest.raises(ValueError):
        actions[0].to_position_action()


def test_funding_after_seven_hours_gives_nothing() -> None:
    funding = FundingInfo(rate=RATE_35PCT, interval_hours=D(8))
    assert run(short_position("100.1", opened_minutes_ago=7 * 60), SHORT_PLAN, funding=funding) == []


def test_funding_in_our_favour_or_cheap_or_not_flat_gives_nothing() -> None:
    pos = short_position("100.1", opened_minutes_ago=9 * 60)
    assert run(pos, SHORT_PLAN, funding=FundingInfo(rate=-RATE_35PCT)) == []  # longs pay us
    assert run(pos, SHORT_PLAN, funding=FundingInfo(rate=D("-0.0001"))) == []  # ~11%/yr
    moving = short_position("99", opened_minutes_ago=9 * 60)  # +0.5R, not flat
    assert run(moving, SHORT_PLAN, funding=FundingInfo(rate=RATE_35PCT)) == []


# AC5 ---------------------------------------------------------------------------------------


def test_a_quiet_position_returns_an_empty_list() -> None:
    assert run(long_position("100.5")) == []
    assert run(short_position("99.5"), SHORT_PLAN) == []


def test_an_external_position_without_a_proposal_only_runs_the_non_r_rules() -> None:
    assert run(long_position("150"), None) == []
    (a,) = run(long_position("100.5"), None, events=[CPI])
    assert a.rule == "event_reduce"


# AC2: property --------------------------------------------------------------------------------

prices = st.decimals(
    min_value=D("0.01"), max_value=D("1000"), places=2, allow_nan=False, allow_infinity=False
)
small = st.decimals(min_value=D("0.01"), max_value=D("50"), places=2, allow_nan=False, allow_infinity=False)


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[PositionState, ProposalPlan | None, dict[str, object]]:
    side: Side = draw(st.sampled_from(["long", "short"]))
    entry = draw(prices)
    risk = draw(small)
    sign = D(-1) if side == "long" else D(1)
    orig_stop = entry + sign * risk
    if orig_stop <= 0:
        orig_stop = entry / 2 if side == "long" else entry * 2
    mark = draw(prices)
    current = draw(st.one_of(st.none(), prices))
    tps = draw(
        st.lists(st.tuples(prices, st.decimals(min_value=D("1"), max_value=D("33"), places=0)), max_size=3)
    )
    plan = draw(
        st.one_of(
            st.none(),
            st.just(
                ProposalPlan(
                    stop=orig_stop,
                    take_profits=tuple(TakeProfit(price=p, pct=q) for p, q in tps),
                    time_stop_minutes=draw(st.one_of(st.none(), st.integers(1, 5000))),
                )
            ),
        )
    )
    pos = PositionState(
        inst_id="X-USDT-SWAP",
        side=side,
        entry_price=entry,
        mark_price=mark,
        stop=current,
        opened_at=NOW - timedelta(minutes=draw(st.integers(0, 10_000))),
        tick_sz=draw(st.sampled_from([None, D("0.01"), D("0.1"), D("1")])),
        tp1_filled=draw(st.booleans()),
        tp1_resting=draw(st.booleans()),
        tp2_filled=draw(st.booleans()),
    )
    kw: dict[str, object] = {
        "atr_1h": draw(st.one_of(st.none(), small)),
        "last_swing": draw(st.one_of(st.none(), prices)),
        "events": [
            EventInfo(
                id="e",
                name="E",
                scheduled_at=NOW + timedelta(minutes=draw(st.integers(-30, 30))),
                importance=draw(st.integers(1, 5)),
            )
        ],
        "funding": FundingInfo(rate=draw(st.decimals(min_value=D("-0.01"), max_value=D("0.01"), places=6))),
        "structure": draw(
            st.one_of(
                st.none(),
                st.builds(StructureReport, st.sampled_from(["intact", "weakening", "broken"]), st.just(NOW)),
            )
        ),
        "settings": RuleSettings.from_config(
            {"fee_rate": str(draw(st.decimals(min_value=D("0"), max_value=D("0.01"), places=5)))}
        ),
    }
    return pos, plan, kw


@settings(max_examples=600, deadline=None)
@given(scenarios())
def test_no_amend_stop_ever_moves_the_stop_away_from_the_market(
    scenario: tuple[PositionState, ProposalPlan | None, dict[str, object]],
) -> None:
    pos, plan, kw = scenario
    ev = evaluate_full(
        pos,
        plan,
        kw["atr_1h"],  # type: ignore[arg-type]
        kw["last_swing"],  # type: ignore[arg-type]
        kw["events"],  # type: ignore[arg-type]
        kw["funding"],  # type: ignore[arg-type]
        NOW,
        kw["settings"],  # type: ignore[arg-type]
        kw["structure"],  # type: ignore[arg-type]
    )
    stops = [a for a in ev.actions if a.kind == "amend_stop"]
    assert len(stops) <= 1
    for a in stops:
        assert isinstance(a.stop, Decimal)
        if pos.side == "long":
            assert a.stop < pos.mark_price
            assert pos.stop is None or a.stop > pos.stop
        else:
            assert a.stop > pos.mark_price
            assert pos.stop is None or a.stop < pos.stop
