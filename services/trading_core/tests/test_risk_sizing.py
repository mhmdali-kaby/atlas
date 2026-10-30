"""ATL-126: Risk Engine check 3 (validity) and check 4 (sizing), brief §10 and §4.3."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from trading_core.risk.inputs import InstrumentSpec, ProposalInput, RiskSettings, infer_contract_type
from trading_core.risk.sizing import round_down_to_lot, round_to_tick, size_position
from trading_core.risk.validity import check_validity

BALANCED = RiskSettings.from_settings_data({})  # all defaults: balanced profile


def loose(**changes: object) -> RiskSettings:
    """Settings where only the caps named in ``changes`` can bind."""
    base = replace(BALANCED, max_position_pct=D(100), max_share_per_instrument_pct=D(100),
                   max_usdt_per_trade=D("1e12"), max_new_exposure_per_day_usdt=D("1e12"),
                   max_loss_per_trade_pct=D(1), slippage_bps=D(2), max_leverage=100)
    return replace(base, **changes)  # type: ignore[arg-type]


LINEAR = InstrumentSpec(inst_id="TEST-USDT-SWAP", ct_type="linear", settle_ccy="USDT", quote_ccy="USDT",
                        ct_val=D("0.01"), ct_mult=D(1), lot_sz=D("0.01"), min_sz=D("0.01"),
                        tick_sz=D("0.01"), max_lever=D(100))


def buy(entry: str = "100", stop: str = "98", r: str = "2", lev: int = 3,
        pct: D | None = None) -> ProposalInput:
    return ProposalInput(side="buy", entry=D(entry), stop=D(stop), expected_r=D(r), leverage_requested=lev,
                         size_suggestion_pct_equity=pct)


def size(proposal: ProposalInput, s: RiskSettings, spec: InstrumentSpec = LINEAR, **kw: object):  # type: ignore[no-untyped-def]
    args: dict[str, object] = {"equity": D(100000), "available_usd": D(1000000), "fee_rate": D("0.0005")}
    args.update(kw)
    return size_position(proposal, s, spec, **args)  # type: ignore[arg-type]


# ---- AC1 golden cases ---------------------------------------------------------------------------

def test_golden_notional_by_risk() -> None:
    # 1000 / (0.02 + 2 x 0.0005 + 0.0002) = 1000 / 0.0212 = 47169.81132075...
    r = size(buy(), loose())
    assert r.risk_usd == D(1000)
    assert r.stop_dist_pct == D("0.02")
    assert r.notional_by_risk is not None
    assert r.notional_by_risk.quantize(D("0.00000001")) == D("47169.81132075")
    assert r.binding_cap == "notional_by_risk"
    assert r.notional == r.notional_by_risk
    # 0.01 contract x 100 = 1 USD per contract; 47169.81 contracts -> 47169.81 USD after lotSz.
    assert r.contracts == D("47169.81")
    assert r.final_notional_usd == D("47169.8100")


def test_golden_max_position_pct_binds() -> None:
    r = size(buy(), loose(max_position_pct=D(5)))
    assert r.passed
    assert r.notional == D(5000)
    assert r.binding_cap == "max_position_pct"
    assert r.resized
    assert r.as_check()["binding_cap"] == "max_position_pct"


@pytest.mark.parametrize(("changes", "kw", "pct", "cap"), [
    ({"max_usdt_per_trade": D(1500)}, {}, None, "max_usdt_per_trade"),
    ({"max_new_exposure_per_day_usdt": D(5000)}, {"new_exposure_used_today_usd": D(4000)}, None,
     "max_new_exposure_per_day"),
    ({"max_share_per_instrument_pct": D(25)}, {"instrument_exposure_usd": D(24000)}, None,
     "max_share_per_instrument"),
    ({}, {}, D(1), "strategist_suggestion"),
])
def test_each_cap_can_bind(changes: dict[str, D], kw: dict[str, D], pct: D | None, cap: str) -> None:
    r = size(buy(pct=pct), loose(**changes), **kw)
    assert r.passed
    assert r.binding_cap == cap
    assert r.notional == (D(1500) if cap == "max_usdt_per_trade" else D(1000))
    assert r.resized is (cap != "strategist_suggestion")


def test_exhausted_daily_allowance_rejects_no_capacity() -> None:
    r = size(buy(), loose(max_new_exposure_per_day_usdt=D(5000)), new_exposure_used_today_usd=D(6000))
    assert not r.passed
    assert r.code == "no_capacity"
    assert r.binding_cap == "max_new_exposure_per_day"


def test_below_min_size_rejects() -> None:
    spec = replace(LINEAR, ct_val=D(1), lot_sz=D(1), min_sz=D(1))  # 100 USD per contract
    r = size(buy(), loose(max_usdt_per_trade=D(50)), spec)
    assert not r.passed
    assert r.code == "below_min_size"
    assert r.contracts == 0


def test_settings_from_row_uses_stored_values_and_profile() -> None:
    s = RiskSettings.from_settings_data({"risk_profile": "aggressive", "max_usdt_per_trade": "2500.50",
                                         "max_leverage": 7})
    assert s.max_loss_per_trade_pct == D("2.0")
    assert s.max_usdt_per_trade == D("2500.50")
    assert s.max_leverage == 7
    assert s.min_stop_atr_mult == D("0.8")
    assert s.margin_buffer == D("0.9")


# ---- AC3 validity: one test each -----------------------------------------------------------------

ATR = D("1.5")  # 0.8 x 1.5 = 1.2


def test_stop_missing_rejects() -> None:
    p = ProposalInput(side="buy", entry=D(100), stop=None, expected_r=D(2), leverage_requested=1)
    assert check_validity(p, BALANCED, ATR).code == "stop_missing"


def test_stop_on_wrong_side_rejects() -> None:
    assert check_validity(buy(stop="101"), BALANCED, ATR).code == "stop_wrong_side"
    sell = ProposalInput(side="sell", entry=D(100), stop=D(99), expected_r=D(2), leverage_requested=1)
    r = check_validity(sell, BALANCED, ATR)
    assert not r.passed
    assert r.code == "stop_wrong_side"


def test_stop_closer_than_min_atr_rejects() -> None:
    r = check_validity(buy(stop="99"), BALANCED, ATR)  # 1.0 < 1.2
    assert not r.passed
    assert r.code == "stop_too_tight"
    assert check_validity(buy(stop="98.8"), BALANCED, ATR).passed  # exactly 1.2 passes


def test_stop_wider_than_max_pct_rejects() -> None:
    r = check_validity(buy(stop="91.9"), BALANCED, ATR)  # 8.1 %
    assert not r.passed
    assert r.code == "stop_too_wide"
    assert check_validity(buy(stop="92"), BALANCED, ATR).passed  # exactly 8 % passes


def test_expected_r_below_min_rejects() -> None:
    r = check_validity(buy(r="1.79"), BALANCED, ATR)  # balanced min R 1.8
    assert not r.passed
    assert r.code == "expected_r_below_min"
    assert r.as_check()["code"] == "expected_r_below_min"
    assert check_validity(buy(r="1.8"), BALANCED, ATR).passed


# ---- AC4 margin and leverage -----------------------------------------------------------------------

def test_margin_above_available_buffer_rejects() -> None:
    # 47169.81 USD at 3x = 15723.27 margin; available 17000 x 0.9 = 15300.
    r = size(buy(lev=3), loose(), available_usd=D(17000))
    assert not r.passed
    assert r.code == "margin_insufficient"
    assert r.margin_limit_usd == D("15300.0")
    assert size(buy(lev=3), loose(), available_usd=D(17500)).passed  # 15750 >= 15723.27


def test_leverage_capped_at_instrument_max_lever() -> None:
    spec = replace(LINEAR, max_lever=D(3))
    r = size(buy(lev=20), loose(max_leverage=10), spec)
    assert r.passed
    assert r.leverage == 3
    assert r.leverage_binding == "instrument_max_lever"


def test_leverage_capped_by_settings_and_request() -> None:
    assert size(buy(lev=20), loose(max_leverage=5)).leverage_binding == "max_leverage"
    r = size(buy(lev=2), loose(max_leverage=5))
    assert (r.leverage, r.leverage_binding) == (2, "requested")


def test_spot_is_never_levered() -> None:
    spot = replace(LINEAR, inst_id="BTC-USDT", ct_type="spot", ct_val=D(1), lot_sz=D("0.00001"),
                   min_sz=D("0.00001"), max_lever=None)
    r = size(buy(lev=5), loose(), spot)
    assert (r.leverage, r.leverage_binding) == (1, "spot")


# ---- AC5 floats refused, USDC conversion -----------------------------------------------------------

@pytest.mark.parametrize("build", [
    lambda: ProposalInput(side="buy", entry=100.0, stop=D(98), expected_r=D(2), leverage_requested=1),  # type: ignore[arg-type]
    lambda: ProposalInput(side="buy", entry=D(100), stop=98.0, expected_r=D(2), leverage_requested=1),  # type: ignore[arg-type]
    lambda: ProposalInput(side="buy", entry=D(100), stop=D(98), expected_r=2.0, leverage_requested=1),  # type: ignore[arg-type]
    lambda: ProposalInput(side="buy", entry=D(100), stop=D(98), expected_r=D(2), leverage_requested=1,
                          size_suggestion_pct_equity=1.5),  # type: ignore[arg-type]
    lambda: ProposalInput(side="buy", entry=D(100), stop=D(98), expected_r=D(2), leverage_requested=2.0),  # type: ignore[arg-type]
    lambda: replace(LINEAR, ct_val=0.01),
    lambda: replace(LINEAR, max_lever=100.0),
    lambda: replace(BALANCED, slippage_bps=2.0),
    lambda: RiskSettings.from_settings_data({"max_loss_per_trade_pct": 1.0}),
    lambda: size(buy(), loose(), equity=100000.0),
    lambda: size(buy(), loose(), available_usd=1e6),
    lambda: size(buy(), loose(), fee_rate=0.0005),
    lambda: size(buy(), loose(), new_exposure_used_today_usd=0.0),
    lambda: size(buy(), loose(), instrument_exposure_usd=0.0),
    lambda: size(buy(), loose(), mark_price=100.0),
    lambda: size(buy(), loose(), usdc_usd_rate=1.0),
    lambda: check_validity(buy(), BALANCED, 1.5),  # type: ignore[arg-type]
    lambda: round_to_tick(1.5, D("0.1")),  # type: ignore[arg-type]
    lambda: round_down_to_lot(D(1), 0.1),  # type: ignore[arg-type]
])
def test_float_anywhere_raises_type_error(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TypeError):
        build()


AAPL_USDC = InstrumentSpec(inst_id="AAPL-USDC-SWAP", ct_type="linear", settle_ccy="USDC", quote_ccy="USDC",
                           ct_val=D("0.1"), ct_mult=D(1), lot_sz=D(1), min_sz=D(1), tick_sz=D("0.01"),
                           max_lever=D(10))


def test_aapl_usdc_swap_converts_with_given_rate() -> None:
    p = ProposalInput(side="buy", entry=D("250.00"), stop=D("245.00"), expected_r=D(2), leverage_requested=5)
    r = size(p, loose(max_usdt_per_trade=D(10000)), AAPL_USDC, usdc_usd_rate=D("0.9990"))
    assert r.passed
    # One contract = 0.1 share x 250 USDC x 0.9990 USD/USDC = 24.975 USD.
    assert r.contract_value_usd == D("24.975")
    assert r.contracts == D(400)  # floor(10000 / 24.975) = 400
    assert r.final_notional_usd == D("9990.000")
    with pytest.raises(ValueError, match="usdc_usd_rate"):
        size(p, loose(), AAPL_USDC)


def test_inverse_uses_usd_contract_value_and_mark() -> None:
    inv = InstrumentSpec(inst_id="BTC-USD-SWAP", ct_type="inverse", settle_ccy="BTC", quote_ccy="USD",
                         ct_val=D(100), ct_mult=D(1), lot_sz=D(1), min_sz=D(1), tick_sz=D("0.1"),
                         max_lever=D(100))
    p = ProposalInput(side="sell", entry=D(60000), stop=D(61200), expected_r=D(2), leverage_requested=2)
    r = size(p, loose(max_usdt_per_trade=D(1050)), inv, mark_price=D(60000))
    assert r.passed
    assert r.contract_value_usd == D(100)
    assert r.contracts == D(10)
    assert r.notional_settle_ccy is not None
    assert r.notional_settle_ccy.quantize(D("1e-12")) == D("0.016666666667")
    with pytest.raises(ValueError, match="mark_price"):
        size(p, loose(), inv)
    assert infer_contract_type("SWAP", "BTC", "BTC") == "inverse"
    assert infer_contract_type("SWAP", "BTC", "USDT") == "linear"
    assert infer_contract_type("SPOT", "BTC", None) == "spot"


def test_prices_round_to_tick() -> None:
    assert round_to_tick(D("100.006"), D("0.01")) == D("100.01")
    assert round_to_tick(D("4062.37"), D("0.25")) == D("4062.25")
    r = size(buy(entry="100.004", stop="97.996"), loose())
    assert (r.entry, r.stop) == (D("100.00"), D("97.99"))  # stop rounds away from entry
    assert round_down_to_lot(D("12.349"), D("0.01")) == D("12.34")


# ---- AC2 hypothesis properties --------------------------------------------------------------------

def dec(lo: str, hi: str, places: int) -> st.SearchStrategy[D]:
    return st.decimals(min_value=D(lo), max_value=D(hi), places=places, allow_nan=False, allow_infinity=False)


@st.composite
def cases(draw: st.DrawFn) -> tuple[ProposalInput, RiskSettings, InstrumentSpec, dict[str, D]]:
    tick = draw(st.sampled_from([D("0.0001"), D("0.01"), D("0.1"), D("0.5"), D(1)]))
    lot = draw(st.sampled_from([D("0.0001"), D("0.01"), D("0.1"), D(1), D(10)]))
    min_mult = draw(st.integers(min_value=0, max_value=5))
    spec = InstrumentSpec(
        inst_id="P-USDT-SWAP", ct_type=draw(st.sampled_from(["linear", "spot"])), settle_ccy="USDT",
        quote_ccy=draw(st.sampled_from(["USDT", "USDC"])), ct_val=draw(st.sampled_from([D("0.001"), D("0.01"),
                                                                                     D("0.1"), D(1), D(10)])),
        ct_mult=D(1), lot_sz=lot, min_sz=lot * min_mult, tick_sz=tick,
        max_lever=draw(st.one_of(st.none(), dec("1", "125", 0))))
    entry = draw(dec("0.5", "200000", 4))
    stop_frac = draw(dec("0.0001", "0.5", 6))
    side = draw(st.sampled_from(["buy", "sell"]))
    stop = entry * (1 - stop_frac) if side == "buy" else entry * (1 + stop_frac)
    s = replace(
        BALANCED, max_loss_per_trade_pct=draw(dec("0.01", "5", 2)),
        max_position_pct=draw(dec("0.1", "100", 2)), max_share_per_instrument_pct=draw(dec("1", "100", 2)),
        max_usdt_per_trade=draw(dec("1", "1000000", 2)),
        max_new_exposure_per_day_usdt=draw(dec("1", "5000000", 2)), slippage_bps=draw(dec("0", "50", 2)),
        max_leverage=draw(st.integers(min_value=1, max_value=20)), margin_buffer=draw(dec("0.1", "1", 2)))
    p = ProposalInput(side=side, entry=entry, stop=stop, expected_r=D(2),
                      leverage_requested=draw(st.integers(min_value=1, max_value=50)),
                      size_suggestion_pct_equity=draw(st.one_of(st.none(), dec("0.01", "100", 2))))
    kw = {"equity": draw(dec("100", "10000000", 2)), "available_usd": draw(dec("0", "10000000", 2)),
          "fee_rate": draw(dec("-0.001", "0.002", 5)),
          "new_exposure_used_today_usd": draw(dec("0", "100000", 2)),
          "instrument_exposure_usd": draw(dec("0", "100000", 2))}
    if spec.quote_ccy == "USDC":
        kw["usdc_usd_rate"] = draw(dec("0.95", "1.05", 4))
    return p, s, spec, kw


@settings(max_examples=600, deadline=None)
@given(cases())
def test_property_loss_at_stop_within_risk_and_contracts_on_lot(
        case: tuple[ProposalInput, RiskSettings, InstrumentSpec, dict[str, D]]) -> None:
    p, s, spec, kw = case
    r = size_position(p, s, spec, **kw)  # type: ignore[arg-type]
    if r.code == "stop_wrong_side":  # stop inside one tick of the entry
        assume(False)
    assert r.risk_usd is not None
    if r.contracts is not None:
        assert r.contracts_raw is not None and r.final_notional_usd is not None and r.notional is not None
        # Multiple of lotSz, never above the pre-rounding size.
        assert Fraction(r.contracts) % Fraction(spec.lot_sz) == 0
        assert r.contracts <= r.contracts_raw
        assert r.final_notional_usd <= r.notional
        # Never below minSz unless rejected.
        if r.passed:
            assert r.contracts >= spec.min_sz and r.contracts > 0
        # Exact (rational) loss at the (rounded) stop never exceeds risk_usd.
        assert r.entry is not None and r.stop is not None
        loss = Fraction(r.final_notional_usd) * abs(Fraction(r.entry) - Fraction(r.stop)) / Fraction(r.entry)
        assert loss <= Fraction(r.risk_usd)
        # The pre-rounding notional also respects the risk budget.
        assert Fraction(r.notional) * abs(Fraction(r.entry) - Fraction(r.stop)) / Fraction(r.entry) \
            <= Fraction(r.risk_usd)
    if r.passed:
        assert r.leverage is not None and r.margin_usd is not None and r.margin_limit_usd is not None
        assert r.leverage <= min(p.leverage_requested, s.max_leverage)
        if spec.max_lever is not None:
            assert r.leverage <= spec.max_lever
        assert r.margin_usd <= r.margin_limit_usd
        assert r.binding_cap is not None and r.notional == r.caps[r.binding_cap]
        assert all(r.notional <= v for v in r.caps.values())
