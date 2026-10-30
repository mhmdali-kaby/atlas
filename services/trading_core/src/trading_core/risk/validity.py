"""Brief §10 check 3: is the proposal valid? Pure, Decimal only.

Hard failures, each with its own code, in this order: ``stop_missing``, ``stop_wrong_side``,
``stop_too_tight`` (distance < min_stop_atr_mult x ATR(1H)), ``stop_too_wide`` (distance >
max_stop_pct of entry), ``expected_r_below_min`` (expected R < the profile's min_expected_r).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from trading_core.risk.inputs import ProposalInput, RiskSettings, require_decimal

ValidityCode = Literal[
    "stop_missing", "stop_wrong_side", "stop_too_tight", "stop_too_wide", "expected_r_below_min"
]

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class ValidityResult:
    passed: bool
    code: ValidityCode | None
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_check(self) -> dict[str, Any]:
        """The risk_checks.checks entry for check 3."""
        return {"check": "proposal_valid", "passed": self.passed, "code": self.code,
                "detail": self.reason, "values": {k: str(v) for k, v in self.detail.items()}}


def check_validity(proposal: ProposalInput, settings: RiskSettings, atr_1h: Decimal) -> ValidityResult:
    require_decimal("atr_1h", atr_1h)
    if atr_1h < 0:
        raise ValueError("atr_1h must be >= 0")

    entry, stop = proposal.entry, proposal.stop
    if stop is None:
        return ValidityResult(False, "stop_missing", "The proposal has no stop-loss.")
    if (proposal.side == "buy" and stop >= entry) or (proposal.side == "sell" and stop <= entry):
        where = "below" if proposal.side == "buy" else "above"
        return ValidityResult(False, "stop_wrong_side", f"A {proposal.side} stop must be {where} the entry.",
                              {"entry": entry, "stop": stop})

    distance = abs(entry - stop)
    min_distance = settings.min_stop_atr_mult * atr_1h
    dist_pct = distance / entry * HUNDRED
    detail: dict[str, Any] = {"stop_distance": distance, "min_distance": min_distance,
                              "stop_distance_pct": dist_pct, "max_stop_pct": settings.max_stop_pct}
    if distance < min_distance:
        return ValidityResult(
            False, "stop_too_tight",
            f"Stop distance {distance} is below {settings.min_stop_atr_mult} x ATR(1H) = {min_distance}.",
            detail)
    if dist_pct > settings.max_stop_pct:
        return ValidityResult(
            False, "stop_too_wide",
            f"Stop distance is {dist_pct:.4f}% of entry, above {settings.max_stop_pct}%.", detail)
    detail.update(expected_r=proposal.expected_r, min_expected_r=settings.min_expected_r)
    if proposal.expected_r < settings.min_expected_r:
        return ValidityResult(
            False, "expected_r_below_min",
            f"Expected R {proposal.expected_r} is below the minimum {settings.min_expected_r}.", detail)
    return ValidityResult(True, None, "Stop and expected R are valid.", detail)
