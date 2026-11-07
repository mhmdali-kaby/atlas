"""The Derivatives & Sentiment Analyst's output schema (brief §7.5, ATL-136), shared so the Strategist,
the Devil's Advocate and the Portfolio Manager read the same card the analyst writes.

Code owns several fields and the model cannot override them
(atlas_agents.derivatives.report.enforce_report): ``regime`` is the computed OI-price quadrant,
``basis_bps``/``basis_reason`` come from the features, ``quadrants``, ``data_reliable``,
``unreliable_reasons``, ``notes`` and ``meta`` are written after the model. When the data is
unreliable, ``supports_direction`` is neutral for both sides.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Regime = Literal["new_longs", "short_covering", "new_shorts", "long_liquidation", "flat"]
Side = Literal["long", "short"]
Support = Literal["supports", "neutral", "opposes"]
SqueezeKind = Literal["long_squeeze", "short_squeeze"]


class PositioningReport(BaseModel):
    """Brief §7.5 ``PositioningReport`` plus the code-written fields above."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["positioning_report"] = "positioning_report"
    inst_id: str = Field(min_length=1, max_length=64)
    regime: Regime
    crowding_score: float = Field(ge=0, le=1)
    crowded_side: Literal["long", "short", "none"]
    squeeze_risk: dict[SqueezeKind, float] = Field(default_factory=dict)
    funding_view: str = Field(default="", max_length=600)
    basis_bps: Decimal | None = None
    underlying_premium_pct: Decimal | None = None
    sentiment_score: float = Field(ge=-1, le=1)
    supports_direction: dict[Side, Support]
    key_evidence: list[str] = Field(default_factory=list, max_length=10)
    data_as_of: AwareDatetime

    # Written by code only.
    basis_reason: str | None = None
    quadrants: dict[str, Regime | None] = Field(default_factory=dict)
    """OI-price quadrant per window ("1h", "4h", "24h"); null where the window had no data."""
    data_reliable: bool = True
    unreliable_reasons: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> PositioningReport:
        for kind, value in self.squeeze_risk.items():
            if not 0 <= value <= 1:
                raise ValueError(f"squeeze_risk.{kind} must be within 0..1")
        if set(self.supports_direction) != {"long", "short"}:
            raise ValueError("supports_direction needs exactly the keys long and short")
        if not self.data_reliable and set(self.supports_direction.values()) != {"neutral"}:
            raise ValueError("unreliable data: supports_direction must be neutral for both sides")
        return self


__all__ = ["PositioningReport", "Regime", "Side", "SqueezeKind", "Support"]
