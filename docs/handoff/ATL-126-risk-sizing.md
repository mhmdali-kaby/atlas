# ATL-126 — Risk Engine check 3 (validity) and check 4 (position size)

For ATL-127 (the nine-check pipeline), ATL-129 (risk preview), ATL-156 (bot capital cap), ATL-128.

## Two-minute version

- `trading_core/risk/inputs.py`, `validity.py`, `sizing.py`. Pure functions, no I/O, no DB. Every
  input is a frozen dataclass; a `float` anywhere raises `TypeError`, a NaN/Inf `ValueError`.
- Run `check_validity` first, then `size_position`. Each result has `.passed`, `.code`, `.reason`
  and `.as_check()` → the dict to append to `risk_checks.checks`.
- `size_position(..., equity=...)` takes the **sizing equity** from the caller. Decision #250
  (min(equity, bot capital cap)) is ATL-156's job: compute it before calling. The cap is not in
  `RiskSettings` on purpose.
- `result.binding_cap` names the cap that set the notional; `result.resized` is True when that was
  any cap other than the Strategist's suggestion → use it for `risk_checks.result = resized`.
- Fee and slippage both make the size smaller, never bigger: stop distance rounds up, notionals and
  contracts round down, margin rounds up. The hypothesis test proves loss-at-stop ≤ risk_usd
  exactly (with `fractions.Fraction`).

## Inputs

```python
from decimal import Decimal as D
from trading_core.risk.inputs import RiskSettings, InstrumentSpec, ProposalInput, infer_contract_type
from trading_core.risk.validity import check_validity
from trading_core.risk.sizing import size_position, round_to_tick, round_down_to_lot

settings = RiskSettings.from_settings_data(setting_row.data)   # missing keys -> DEFAULTS, profile preset
spec = InstrumentSpec(inst_id="XAU-USDT-SWAP",
                      ct_type=infer_contract_type(inst.inst_type, inst.base_ccy, inst.settle_ccy),
                      settle_ccy="USDT", quote_ccy="USDT", ct_val=D("0.001"), ct_mult=D(1),
                      lot_sz=D("1"), min_sz=D("1"), tick_sz=D("0.01"), max_lever=D("50"))
p = ProposalInput(side="buy", entry=D("4012.35"), stop=D("3988.10"), expected_r=D("2.1"),
                  leverage_requested=3, size_suggestion_pct_equity=D("2"))

v = check_validity(p, settings, atr_1h=D("18.4"))
if v.passed:
    r = size_position(p, settings, spec, equity=D("10000"), available_usd=D("9000"),
                      fee_rate=D("-0.0005"),                 # sign ignored
                      new_exposure_used_today_usd=D("0"),     # already used today
                      instrument_exposure_usd=D("0"),         # already held in this instrument
                      mark_price=None,                        # required for inverse
                      usdc_usd_rate=None)                     # required when quote_ccy is USDC
```

`ProposalInput.entry` is one price: the limit price, or the mark for a market order. Resolve an
`entry_zone` before calling. `InstrumentSpec.ct_type` is `linear | inverse | spot`; the
instruments table has no ctType column, so use `infer_contract_type` (settled in the base coin =
inverse).

## Codes

- `check_validity`: `stop_missing`, `stop_wrong_side`, `stop_too_tight` (< min_stop_atr_mult ×
  ATR(1H)), `stop_too_wide` (> max_stop_pct % of entry), `expected_r_below_min` (< profile
  min_expected_r). Boundaries are inclusive (exactly 0.8×ATR and exactly 8 % pass).
- `size_position`: `stop_wrong_side` (stop collapses onto the entry after tick rounding),
  `no_capacity` (a cap is 0, e.g. the daily allowance is used up; `binding_cap` says which),
  `below_min_size`, `margin_insufficient` (margin > available × margin_buffer).

Cap names (`binding_cap`, keys of `caps`): `notional_by_risk`, `max_position_pct`,
`max_usdt_per_trade`, `max_new_exposure_per_day`, `max_share_per_instrument`,
`strategist_suggestion` (absent when the proposal has no suggestion). Ties go to the first in
that order. Leverage limit (`leverage_binding`): `requested`, `max_leverage`,
`instrument_max_lever`, `spot` (spot is always 1x).

## Result fields (SizingResult)

`entry`, `stop` (tick-rounded; the stop rounds away from the entry), `risk_usd`, `stop_dist_pct`
(a fraction), `notional_by_risk`, `caps`, `binding_cap`, `notional` (pre-rounding), 
`contract_value_usd`, `contracts_raw`, `contracts` (lotSz multiple → `risk_checks.final_size`),
`final_notional_usd` (→ `risk_checks.final_notional_usd`), `notional_settle_ccy` (inverse only),
`leverage`, `leverage_binding`, `margin_usd`, `margin_limit_usd`.

## Not built here

The `account/max-size` cross-check (needs I/O — ATL-127 can compare `contracts` with it), the
liquidation-price check (check 7), and the bot capital cap (ATL-156).

## Note on the ATL-126 golden value

The task's AC1 said notional_by_risk = 48076.92; the brief's formula gives
1000 / (0.02 + 2×0.0005 + 0.0002) = **47169.81132075**, which is what the code and test use.
48076.92 is 1000/0.0208 and matches no reading of the formula.
