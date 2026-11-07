"""Brief §10 check 4 (position size) and §4.3 instrument handling. Pure, Decimal only, no I/O.

    risk_usd         = equity x max_loss_per_trade_pct
    stop_dist_pct    = |entry - stop| / entry
    notional_by_risk = risk_usd / (stop_dist_pct + 2 x fee_rate + slippage_bps / 10000)
    notional         = min(notional_by_risk, equity x max_position_pct, max_usdt_per_trade,
                           remaining daily new exposure, remaining per-instrument share,
                           strategist suggestion)
    leverage         = min(requested, settings max_leverage, instrument maxLever)
    margin           = final notional / leverage   must be <= available x margin_buffer

Contracts = notional / contract value in USD, rounded DOWN to lotSz; below minSz rejects. Every
rounding goes the safe way: the stop distance rounds up, notionals and contracts round down,
margin rounds up, so the loss at the stop never exceeds risk_usd. The result records which cap
bound the notional and which limit bound the leverage.

``equity`` is the sizing equity the caller decides (decision #250: min(equity, bot capital cap)).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal
from typing import Any, Literal, cast

from trading_core.risk.inputs import (
    USD_LIKE,
    InstrumentSpec,
    ProposalInput,
    RiskSettings,
    require_decimal,
)

CapName = Literal[
    "notional_by_risk", "max_position_pct", "max_usdt_per_trade", "max_new_exposure_per_day",
    "max_share_per_instrument", "strategist_suggestion",
]
LeverageLimit = Literal["requested", "max_leverage", "instrument_max_lever", "spot"]
SizingCode = Literal["stop_wrong_side", "no_capacity", "below_min_size", "margin_insufficient"]

_PREC = 60
_FLOOR = Context(prec=_PREC, rounding=ROUND_FLOOR)
_CEIL = Context(prec=_PREC, rounding=ROUND_CEILING)
_HUNDRED = Decimal(100)
_BPS = Decimal(10000)
_ZERO = Decimal(0)


def round_to_tick(price: Decimal, tick_sz: Decimal, rounding: str = ROUND_HALF_EVEN) -> Decimal:
    """Round a price to a multiple of tickSz (nearest by default; ROUND_FLOOR / ROUND_CEILING allowed)."""
    require_decimal("price", price)
    require_decimal("tick_sz", tick_sz)
    if tick_sz <= 0:
        raise ValueError("tick_sz must be > 0")
    ctx = Context(prec=_PREC, rounding=rounding)
    steps = ctx.divide(price, tick_sz).quantize(Decimal(1), rounding=rounding)
    return _FLOOR.multiply(steps, tick_sz)


def round_down_to_lot(size: Decimal, lot_sz: Decimal) -> Decimal:
    """Round a size DOWN to a multiple of lotSz (never above the input)."""
    require_decimal("size", size)
    require_decimal("lot_sz", lot_sz)
    if lot_sz <= 0:
        raise ValueError("lot_sz must be > 0")
    if size <= 0:
        return _ZERO
    lots = _FLOOR.divide(size, lot_sz).to_integral_value(rounding=ROUND_FLOOR)
    return _FLOOR.multiply(lots, lot_sz)


def contract_value_usd(spec: InstrumentSpec, price: Decimal, usdc_usd_rate: Decimal | None) -> Decimal:
    """USD value of one contract (one unit of base for spot) at ``price``.

    Linear and spot: ctVal x ctMult x price, in the quote currency, then to USD (USDT = USD; USDC
    uses the rate passed in). Inverse: ctVal is already in USD, so ctVal x ctMult.
    """
    if spec.ct_type == "inverse":
        return _FLOOR.multiply(spec.ct_val, spec.ct_mult)
    value = _FLOOR.multiply(_FLOOR.multiply(spec.ct_val, spec.ct_mult), price)
    quote = spec.quote_ccy.upper()
    if quote in USD_LIKE:
        return value
    if quote == "USDC":
        if usdc_usd_rate is None:
            raise ValueError(f"{spec.inst_id} is USDC-quoted: pass usdc_usd_rate")
        require_decimal("usdc_usd_rate", usdc_usd_rate)
        if usdc_usd_rate <= 0:
            raise ValueError("usdc_usd_rate must be > 0")
        return _FLOOR.multiply(value, usdc_usd_rate)
    raise ValueError(f"{spec.inst_id}: quote currency {spec.quote_ccy} has no USD conversion")


@dataclass(frozen=True, slots=True)
class SizingResult:
    passed: bool
    code: SizingCode | None
    reason: str
    entry: Decimal | None = None          # tick-rounded
    stop: Decimal | None = None           # tick-rounded, away from entry
    risk_usd: Decimal | None = None
    stop_dist_pct: Decimal | None = None  # fraction, not percent
    notional_by_risk: Decimal | None = None
    caps: dict[str, Decimal] = field(default_factory=dict)
    binding_cap: CapName | None = None
    notional: Decimal | None = None       # the min of the caps, before contract rounding
    contract_value_usd: Decimal | None = None
    contracts_raw: Decimal | None = None
    contracts: Decimal | None = None      # rounded down to lotSz
    final_notional_usd: Decimal | None = None
    notional_settle_ccy: Decimal | None = None  # inverse only: final notional in coin at mark
    leverage: int | None = None
    leverage_binding: LeverageLimit | None = None
    margin_usd: Decimal | None = None
    margin_limit_usd: Decimal | None = None

    @property
    def resized(self) -> bool:
        """True when a cap other than the strategist's own suggestion set the size."""
        return self.passed and self.binding_cap not in (None, "strategist_suggestion")

    def as_check(self) -> dict[str, Any]:
        """The risk_checks.checks entry for check 4."""
        values: dict[str, Any] = {}
        for name in ("entry", "stop", "risk_usd", "stop_dist_pct", "notional_by_risk", "notional",
                     "contract_value_usd", "contracts_raw", "contracts", "final_notional_usd",
                     "notional_settle_ccy", "margin_usd", "margin_limit_usd"):
            v = getattr(self, name)
            if v is not None:
                values[name] = str(v)
        values["caps"] = {k: str(v) for k, v in self.caps.items()}
        values["leverage"] = self.leverage
        return {"check": "position_size", "passed": self.passed, "code": self.code, "detail": self.reason,
                "binding_cap": self.binding_cap, "leverage_binding": self.leverage_binding, "values": values}


def _pct_of(base: Decimal, pct: Decimal) -> Decimal:
    return _FLOOR.divide(_FLOOR.multiply(base, pct), _HUNDRED)


def size_position(
    proposal: ProposalInput,
    settings: RiskSettings,
    spec: InstrumentSpec,
    *,
    equity: Decimal,
    available_usd: Decimal,
    fee_rate: Decimal,
    new_exposure_used_today_usd: Decimal = _ZERO,
    instrument_exposure_usd: Decimal = _ZERO,
    mark_price: Decimal | None = None,
    usdc_usd_rate: Decimal | None = None,
) -> SizingResult:
    """Size a valid proposal (run :func:`check_validity` first; a missing stop raises ValueError).

    ``fee_rate`` is the taker fee as a fraction; its sign is ignored (OKX reports fees negative).
    ``new_exposure_used_today_usd`` and ``instrument_exposure_usd`` are what is already used, so
    the remaining daily allowance and per-instrument share are computed here.
    """
    for name, value in (("equity", equity), ("available_usd", available_usd), ("fee_rate", fee_rate),
                        ("new_exposure_used_today_usd", new_exposure_used_today_usd),
                        ("instrument_exposure_usd", instrument_exposure_usd)):
        require_decimal(name, value)
    require_decimal("mark_price", mark_price, optional=True)
    require_decimal("usdc_usd_rate", usdc_usd_rate, optional=True)
    if proposal.stop is None:
        raise ValueError("size_position needs a stop; run check_validity first")
    if spec.ct_type == "inverse" and (mark_price is None or mark_price <= 0):
        raise ValueError(f"{spec.inst_id} is inverse: pass a positive mark_price")

    fee = abs(fee_rate)
    # Prices to tickSz: entry to the nearest tick, the stop away from the entry (a wider rounded
    # stop is used for the sizing, so rounding can only shrink the size).
    entry = round_to_tick(proposal.entry, spec.tick_sz)
    stop = round_to_tick(proposal.stop, spec.tick_sz,
                         ROUND_FLOOR if proposal.side == "buy" else ROUND_CEILING)
    distance = entry - stop if proposal.side == "buy" else stop - entry
    if entry <= 0 or distance <= 0:
        return SizingResult(False, "stop_wrong_side",
                            "After rounding to tickSz the stop is not on the losing side of the entry.",
                            entry=entry, stop=stop)

    risk_usd = _pct_of(equity, settings.max_loss_per_trade_pct)
    stop_dist_pct = _CEIL.divide(distance, entry)
    denominator = _CEIL.add(_CEIL.add(stop_dist_pct, _CEIL.multiply(Decimal(2), fee)),
                            _CEIL.divide(settings.slippage_bps, _BPS))
    by_risk = _FLOOR.divide(risk_usd, denominator)

    caps: dict[str, Decimal] = {
        "notional_by_risk": by_risk,
        "max_position_pct": _pct_of(equity, settings.max_position_pct),
        "max_usdt_per_trade": settings.max_usdt_per_trade,
        "max_new_exposure_per_day": max(_ZERO, settings.max_new_exposure_per_day_usdt
                                        - new_exposure_used_today_usd),
        "max_share_per_instrument": max(_ZERO, _pct_of(equity, settings.max_share_per_instrument_pct)
                                        - instrument_exposure_usd),
    }
    if proposal.size_suggestion_pct_equity is not None:
        caps["strategist_suggestion"] = _pct_of(equity, proposal.size_suggestion_pct_equity)
    # Ties go to the first cap in brief order, so the reported binding cap is stable.
    binding = cast(CapName, min(caps, key=caps.__getitem__))
    notional = max(_ZERO, caps[binding])
    base = SizingResult(False, None, "", entry=entry, stop=stop, risk_usd=risk_usd,
                        stop_dist_pct=stop_dist_pct, notional_by_risk=by_risk, caps=caps,
                        binding_cap=binding, notional=notional)
    if notional <= 0:
        return _with(base, code="no_capacity", reason=f"No room left: {binding} allows 0 USD.")

    cv = contract_value_usd(spec, entry, usdc_usd_rate)
    contracts_raw = _FLOOR.divide(notional, cv)
    contracts = round_down_to_lot(contracts_raw, spec.lot_sz)
    final_notional = _FLOOR.multiply(contracts, cv)
    base = _with(base, contract_value_usd=cv, contracts_raw=contracts_raw, contracts=contracts,
                 final_notional_usd=final_notional)
    if contracts <= 0 or contracts < spec.min_sz:
        return _with(base, code="below_min_size",
                     reason=f"{contracts} is below the minimum size {spec.min_sz} for {spec.inst_id}.")

    if spec.ct_type == "spot":
        leverage, lev_binding = 1, "spot"
    else:
        options: list[tuple[int, LeverageLimit]] = [(proposal.leverage_requested, "requested"),
                                                    (settings.max_leverage, "max_leverage")]
        if spec.max_lever is not None:
            options.append((max(1, int(spec.max_lever.to_integral_value(rounding=ROUND_FLOOR))),
                            "instrument_max_lever"))
        leverage, lev_binding = min(options, key=lambda o: o[0])
    margin = _CEIL.divide(final_notional, Decimal(leverage))
    margin_limit = _FLOOR.multiply(max(_ZERO, available_usd), settings.margin_buffer)
    settle_amount = None
    if spec.ct_type == "inverse" and mark_price is not None:
        settle_amount = _FLOOR.divide(final_notional, mark_price)
    base = _with(base, leverage=leverage, leverage_binding=lev_binding, margin_usd=margin,
                 margin_limit_usd=margin_limit, notional_settle_ccy=settle_amount)
    if margin > margin_limit:
        return _with(base, code="margin_insufficient",
                     reason=f"Margin {margin} USD at {leverage}x is above available x "
                            f"{settings.margin_buffer} = {margin_limit} USD.")
    return _with(base, passed=True,
                 reason=f"{contracts} contracts, {final_notional} USD at {leverage}x; bound by {binding}.")


def _with(result: SizingResult, **changes: Any) -> SizingResult:
    return replace(result, **changes)
