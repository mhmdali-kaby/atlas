"""The Technical Analyst's output schema (brief §7.4, ATL-135), shared so the Strategist, the Devil's
Advocate and the Portfolio Manager read the same card the Technical Analyst writes.

Prices are Decimal. ``meta``, ``notes`` and ``no_setup_reason`` are written by code after the model
(atlas_agents.technical_analyst.enforce_setup), never trusted from the model.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SetupType = Literal[
    "breakout",
    "pullback_to_support",
    "breakdown",
    "rally_to_resistance",
    "range_fade",
    "squeeze_expansion",
    "reversal_divergence",
    "none",
]
Direction = Literal["long", "short", "none"]
TimeframeBias = Literal["up", "down", "range"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetupTarget(_Model):
    """One take-profit of a setup: the price, its R multiple from the entry-zone midpoint, the level
    it sits at, and optionally the % of the position to close there."""

    price: Decimal = Field(gt=0)
    r_multiple: Decimal | None = None
    reason: str = Field(min_length=1, max_length=300)
    close_pct: Decimal | None = Field(default=None, gt=0, le=100)


class LevelRef(_Model):
    """A computed level the plan rests on: a zone id from get_ta_table (e.g. ``1H:z3``) or a named
    level (``prior_day.high``, ``poc``, ``round:4000``)."""

    id: str = Field(min_length=1, max_length=64)
    price: Decimal | None = None


class TechnicalSetup(_Model):
    """Brief §7.4 ``TechnicalSetup``."""

    type: Literal["technical_setup"] = "technical_setup"
    inst_id: str = Field(min_length=1, max_length=64)
    timeframe_bias: dict[Literal["1D", "4H", "1H", "15m"], TimeframeBias] = Field(default_factory=dict)
    setup_type: SetupType
    direction: Direction
    entry_zone: tuple[Decimal, Decimal] | None = None
    trigger: str = Field(default="", max_length=500)
    stop: Decimal | None = Field(default=None, gt=0)
    targets: list[SetupTarget] = Field(default_factory=list, max_length=3)
    expected_r: Decimal | None = None
    invalidation: str = Field(default="", max_length=500)
    quality_score: float = Field(ge=0, le=1)
    est_slippage_bps: Decimal | None = None
    levels_used: list[LevelRef] = Field(default_factory=list)
    data_as_of: AwareDatetime
    no_setup_reason: str | None = None
    """Why there is no setup: the model's own condition, or the code rule that removed the setup."""
    notes: list[str] = Field(default_factory=list)
    """Code-written notes, e.g. the thin-liquidity warning."""
    meta: dict[str, Any] | None = None
    """Set by the runtime: number-guard findings and the code corrections applied."""

    @model_validator(mode="after")
    def _consistent(self) -> TechnicalSetup:
        none_type = self.setup_type == "none"
        none_dir = self.direction == "none"
        if none_type != none_dir:
            raise ValueError('setup_type "none" and direction "none" go together')
        if not none_type:
            if self.entry_zone is None or self.stop is None or not self.targets:
                raise ValueError("a setup needs entry_zone, stop and at least one target")
            low, high = self.entry_zone
            if low <= 0 or high < low:
                raise ValueError("entry_zone must be (low, high) with 0 < low <= high")
        return self

    @property
    def is_setup(self) -> bool:
        return self.setup_type != "none"


__all__ = ["Direction", "LevelRef", "SetupTarget", "SetupType", "TechnicalSetup", "TimeframeBias"]
