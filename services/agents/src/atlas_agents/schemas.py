"""Agent output schemas validated in code (brief §6.3, §7).

``HistoryVerdict`` is the Historian's answer (brief §7.3). Its failure rules are enforced here, not
left to the prompt: :func:`enforce_verdict_rules` caps a verdict on a small sample and forces
``does_not_support`` for a backtest-based claim on data with an unrepaired gap.
"""

from __future__ import annotations

from typing import Literal

from atlas_shared.defaults import DEFAULTS
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Verdict = Literal["supports", "weakly_supports", "does_not_support", "contradicts"]

# Strength of support, weakest first. "contradicts" is the strongest claim *against* the idea.
_SUPPORT_RANK: dict[str, int] = {
    "contradicts": 0,
    "does_not_support": 1,
    "weakly_supports": 2,
    "supports": 3,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HorizonDistribution(_Model):
    median: float
    p10: float
    p90: float
    pct_positive: float = Field(ge=0, le=100)
    n: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> HorizonDistribution:
        if not self.p10 <= self.median <= self.p90:
            raise ValueError("p10 <= median <= p90 must hold")
        return self


class RegimeSummary(_Model):
    label: str
    probability: float | None = Field(default=None, ge=0, le=1)
    days_in_regime: float | None = Field(default=None, ge=0)


class StopNoiseCheck(_Model):
    stop_distance_atr: float = Field(ge=0)
    median_4h_range_atr: float = Field(ge=0)
    # inside_noise: the stop is < 0.8 x the median 4h range (brief §7.3 proactive duty).
    verdict: Literal["ok", "inside_noise"]


class UnmatchedNumber(_Model):
    """One number the agent stated that no tool returned (atlas_agents.numguard to_meta())."""

    raw: str
    value: float
    kind: str
    location: Literal["text", "card"]
    span: tuple[int, int] | None = None
    path: str | None = None


class VerdictCorrectionMeta(_Model):
    """A verdict enforce_verdict_rules changed, as the War Room card shows it."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_: Verdict = Field(alias="from")
    to: Verdict
    rule: str = Field(description='"n_below_<min_n>" or "backtest_on_gapped_data"')
    n: int = Field(ge=0)


class CardMeta(_Model):
    """Set by the runtime, never by the agent (ATL-93): the number guard's findings and the verdict
    rules that changed the card."""

    unmatched_numbers: list[UnmatchedNumber] = Field(default_factory=list)
    checked: int = Field(default=0, ge=0)
    verdict_corrections: list[VerdictCorrectionMeta] = Field(default_factory=list)


class HistoryVerdict(_Model):
    """Brief §7.3 output schema. Distributions are keyed by horizon ("1h", "4h", "24h", "72h").

    ``type`` is the War Room card discriminator: a ``research_report`` card with
    ``type: "history_verdict"`` is validated against this model (``CARD_TYPES``)."""

    type: Literal["history_verdict"] = "history_verdict"
    question: str = Field(min_length=1)
    inst_id: str | None
    bar: str | None
    period: str | None = Field(description='e.g. "2024-10-29..2026-10-29"')
    n: int = Field(ge=0)
    distribution: dict[str, HorizonDistribution] = Field(default_factory=dict)
    base_rate: dict[str, HorizonDistribution] = Field(default_factory=dict)
    regime: RegimeSummary | None = None
    stop_noise_check: StopNoiseCheck | None = None
    overfit_warning: bool = False
    verdict: Verdict
    key_stat: str
    data_quality_notes: list[str] = Field(default_factory=list)
    data_as_of: AwareDatetime
    meta: CardMeta | None = None


class VerdictEvidence(_Model):
    """What the tool results behind a verdict say about its reliability.

    ``n``: cases behind the claim (the tool's sample.n); None falls back to ``verdict.n``.
    ``backtest_based``: the claim rests on a backtest_rule result.
    ``unrepaired_gap``: any series used has an interior gap (sample.unrepaired_gap / data_gap).
    """

    n: int | None = Field(default=None, ge=0)
    backtest_based: bool = False
    unrepaired_gap: bool = False
    gap_note: str | None = None


class VerdictCorrection(_Model):
    rule: Literal["small_sample", "backtest_on_gapped_data"]
    from_verdict: Verdict
    to_verdict: Verdict
    reason: str


def _min_n() -> int:
    return int(DEFAULTS["agents"]["history"]["verdict"]["min_n"])


def enforce_verdict_rules(
    verdict: HistoryVerdict, evidence: VerdictEvidence | None = None
) -> tuple[HistoryVerdict, list[VerdictCorrection]]:
    """Apply the Historian's failure rules (brief §7.3) and return the corrected verdict with what
    changed. The input is never modified.

    1. n < agents.history.verdict.min_n (20): the verdict cannot be stronger than weakly_supports.
    2. A backtest-based claim on data with an unrepaired gap is does_not_support, whatever it said,
       and a data_quality_note says why.
    """
    ev = evidence or VerdictEvidence()
    corrections: list[VerdictCorrection] = []
    current: Verdict = verdict.verdict
    notes = list(verdict.data_quality_notes)
    n = ev.n if ev.n is not None else verdict.n
    floor = _min_n()

    if n < floor and _SUPPORT_RANK[current] > _SUPPORT_RANK["weakly_supports"]:
        corrections.append(
            VerdictCorrection(
                rule="small_sample",
                from_verdict=current,
                to_verdict="weakly_supports",
                reason=f"n={n} < {floor}: evidence is weak, so the verdict is capped at weakly_supports",
            )
        )
        current = "weakly_supports"

    if ev.backtest_based and ev.unrepaired_gap and current != "does_not_support":
        reason = "backtest on data with an unrepaired gap: claims from it are does_not_support"
        corrections.append(
            VerdictCorrection(
                rule="backtest_on_gapped_data",
                from_verdict=current,
                to_verdict="does_not_support",
                reason=reason,
            )
        )
        current = "does_not_support"
        notes.append(f"{reason}{f' ({ev.gap_note})' if ev.gap_note else ''}")

    if not corrections:
        return verdict, []
    return verdict.model_copy(update={"verdict": current, "data_quality_notes": notes}), corrections


class RegimeRow(_Model):
    """``label`` is detect_regime's label, or ``no_data`` / ``insufficient_data`` (then ``method``,
    ``probability`` and ``days_in_regime`` are null). ``method``: hmm, or the ADX + vol fallback."""

    inst_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=32)
    probability: float | None = Field(default=None, ge=0, le=1)
    days_in_regime: float | None = Field(default=None, ge=0)
    method: Literal["hmm", "fallback"] | None = None


class RegimeTable(_Model):
    """The Historian's regime table (brief §7.3, §8 07:30): the top instruments by 24h quote volume,
    in that order. ``data_as_of`` is the newest bar close behind any row."""

    type: Literal["regime_table"] = "regime_table"
    rows: list[RegimeRow] = Field(min_length=1)
    data_as_of: AwareDatetime
    meta: CardMeta | None = None


# War Room card registry: card ``type`` -> the one model it must validate against.
CARD_TYPES: dict[str, type[BaseModel]] = {
    "history_verdict": HistoryVerdict,
    "regime_table": RegimeTable,
}


def card_rule_violations(card: BaseModel) -> list[str]:
    """Rules a validated card breaks that the schema alone cannot express. A HistoryVerdict card must
    already obey :func:`enforce_verdict_rules` (same config floor), so a ``supports`` on n < min_n is
    refused rather than posted."""
    if isinstance(card, HistoryVerdict):
        _, corrections = enforce_verdict_rules(card)
        return [c.reason for c in corrections]
    return []


__all__ = [
    "CARD_TYPES",
    "CardMeta",
    "HistoryVerdict",
    "HorizonDistribution",
    "RegimeRow",
    "RegimeSummary",
    "RegimeTable",
    "StopNoiseCheck",
    "UnmatchedNumber",
    "Verdict",
    "VerdictCorrection",
    "VerdictCorrectionMeta",
    "VerdictEvidence",
    "card_rule_violations",
    "enforce_verdict_rules",
]
