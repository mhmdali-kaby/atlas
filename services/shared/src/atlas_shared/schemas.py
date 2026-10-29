"""Agent output schemas shared by the Python services (brief §6.3, §7.3).

Each card model carries a ``type`` discriminator so a War Room card says which schema it follows;
``atlas_agents.tools.warroom`` validates a posted card against the model its ``type`` names.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Bar = Literal["1m", "5m", "15m", "1H", "4H", "1D"]
Verdict = Literal["supports", "weakly_supports", "does_not_support", "contradicts"]
MIN_STRONG_N = 20


class _Card(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Distribution(_Card):
    """Forward-return distribution for one horizon (fractions, e.g. 0.012 = +1.2%)."""

    median: float
    p10: float
    p90: float
    pct_positive: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _ordered(self) -> Distribution:
        if not self.p10 <= self.median <= self.p90:
            raise ValueError("p10 <= median <= p90 must hold")
        return self


class RegimeState(_Card):
    label: str = Field(min_length=1, max_length=32)
    probability: float = Field(ge=0, le=1)
    days_in_regime: int = Field(ge=0)


class StopNoiseCheck(_Card):
    stop_distance_atr: float = Field(ge=0)
    median_4h_range_atr: float = Field(ge=0)
    verdict: Literal["ok", "inside_noise"]


class HistoryVerdict(_Card):
    """The Historian's answer (brief §7.3 output schema). With n < 20 the verdict cannot be
    stronger than ``weakly_supports``."""

    type: Literal["history_verdict"] = "history_verdict"
    question: str = Field(min_length=1, max_length=1000)
    inst_id: str = Field(min_length=1, max_length=64)
    bar: Bar
    period: str = Field(min_length=1, max_length=64)
    n: int = Field(ge=0)
    distribution: dict[str, Distribution] = Field(description="horizon (e.g. '4h') -> distribution")
    base_rate: dict[str, Distribution] = Field(description="unconditional distribution, same horizons")
    regime: RegimeState | None = None
    stop_noise_check: StopNoiseCheck | None = None
    overfit_warning: bool = False
    verdict: Verdict
    key_stat: str = Field(min_length=1, max_length=500)
    data_quality_notes: list[str] = Field(default_factory=list)
    data_as_of: AwareDatetime

    @model_validator(mode="after")
    def _weak_sample(self) -> HistoryVerdict:
        if self.n < MIN_STRONG_N and self.verdict == "supports":
            raise ValueError(f"n={self.n} < {MIN_STRONG_N}: verdict cannot be stronger than weakly_supports")
        return self


class RegimeRow(_Card):
    inst_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=32)
    probability: float = Field(ge=0, le=1)
    days_in_regime: int = Field(ge=0)
    data_as_of: AwareDatetime


class RegimeTable(_Card):
    """The Historian's regime table for the shortlist / universe (brief §7.3, §8 07:30)."""

    type: Literal["regime_table"] = "regime_table"
    rows: list[RegimeRow] = Field(min_length=1)


CARD_TYPES: dict[str, type[BaseModel]] = {
    "history_verdict": HistoryVerdict,
    "regime_table": RegimeTable,
}

__all__ = [
    "CARD_TYPES",
    "Distribution",
    "HistoryVerdict",
    "RegimeRow",
    "RegimeState",
    "RegimeTable",
    "StopNoiseCheck",
]
