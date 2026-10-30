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
       This caps both directions: a claim against the idea strong enough to say "contradicts" needs
       the same minimum sample as a claim for it, so n below the floor softens it to
       does_not_support.
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
    elif n < floor and current == "contradicts":
        corrections.append(
            VerdictCorrection(
                rule="small_sample",
                from_verdict=current,
                to_verdict="does_not_support",
                reason=f"n={n} < {floor}: evidence is weak, so the verdict is capped at does_not_support",
            )
        )
        current = "does_not_support"

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


NewsStatus = Literal["CONFIRMED", "REPORTED", "RUMOUR"]


class NewsBriefSource(_Model):
    """One outlet that carried the story."""

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    published_at: AwareDatetime


class PriceMoveAtr(_Model):
    """Move of one linked instrument since the story was first seen, in ATR(14, 1H) units."""

    inst_id: str = Field(min_length=1, max_length=64)
    value: float | None
    reason: str | None = None
    """Why ``value`` is null (e.g. candles missing)."""


class NewsBriefCluster(_Model):
    """One story in a News Intelligence brief (brief §7.2). ``cluster_id`` names the stored story
    cluster: ``status``, ``status_sources``, ``first_seen`` and ``price_move_since_first_seen_atr``
    are overwritten with the news tools' code-computed values when the card is posted (ATL-106,
    decision #247 (4)), whatever the model wrote."""

    cluster_id: str = Field(min_length=1, max_length=64)
    headline: str = Field(min_length=1, max_length=500)
    status: NewsStatus = "RUMOUR"
    status_sources: list[str] = Field(default_factory=list)
    sources: list[NewsBriefSource] = Field(default_factory=list)
    event_type: str | None = None
    importance: int = Field(ge=1, le=5)
    sentiment: float | None = Field(default=None, ge=-1, le=1)
    first_seen: AwareDatetime
    price_move_since_first_seen_atr: list[PriceMoveAtr] = Field(default_factory=list)


class UpcomingEvent(_Model):
    time: AwareDatetime
    name: str = Field(min_length=1, max_length=300)
    forecast: str | None = None
    previous: str | None = None
    sensitive_instruments: list[str] = Field(default_factory=list)


class NewsBrief(_Model):
    """News Intelligence's ``NewsBrief`` (brief §7.2). A ``research_report`` card with
    ``type: "news_brief"`` is validated against this model (``CARD_TYPES``)."""

    type: Literal["news_brief"] = "news_brief"
    inst_ids: list[str] = Field(default_factory=list)
    clusters: list[NewsBriefCluster] = Field(default_factory=list)
    upcoming_events: list[UpcomingEvent] = Field(default_factory=list)
    net_sentiment_24h: float | None = Field(default=None, ge=-1, le=1)
    net_sentiment_72h: float | None = Field(default=None, ge=-1, le=1)
    priced_in: Literal["yes", "partly", "no"]
    what_to_watch: str = Field(min_length=1)
    data_as_of: AwareDatetime
    meta: CardMeta | None = None


class Catalyst(_Model):
    """One reason a pick may move (brief §6.3): what, when, which source, how important (1-5)."""

    name: str = Field(min_length=1, max_length=200)
    time: AwareDatetime | None = Field(default=None, description="When it happens (UTC); null if unscheduled")
    source: str = Field(min_length=1, max_length=500)
    importance: int = Field(ge=1, le=5)


class WatchlistPick(_Model):
    """One Market Scout pick (brief §6.3). ``rank`` 1 is the strongest. ``key_levels`` maps a level
    name (support, resistance, invalidation, ...) to a price."""

    inst_id: str = Field(min_length=1, max_length=64)
    rank: int = Field(ge=1)
    asset_class: str = Field(min_length=1, max_length=32)
    bias: Literal["long", "short", "neutral"]
    conviction: float = Field(ge=0, le=1)
    thesis: str = Field(min_length=1)
    catalysts: list[Catalyst] = Field(default_factory=list)
    key_levels: dict[str, float] = Field(default_factory=dict)
    invalidation: str = Field(min_length=1)
    data_as_of: AwareDatetime


# War Room card registry: card ``type`` -> the one model it must validate against.
CARD_TYPES: dict[str, type[BaseModel]] = {
    "history_verdict": HistoryVerdict,
    "regime_table": RegimeTable,
    "news_brief": NewsBrief,
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
    "Catalyst",
    "HistoryVerdict",
    "HorizonDistribution",
    "NewsBrief",
    "NewsBriefCluster",
    "NewsBriefSource",
    "NewsStatus",
    "PriceMoveAtr",
    "RegimeRow",
    "RegimeSummary",
    "RegimeTable",
    "StopNoiseCheck",
    "UnmatchedNumber",
    "UpcomingEvent",
    "Verdict",
    "VerdictCorrection",
    "VerdictCorrectionMeta",
    "VerdictEvidence",
    "WatchlistPick",
    "card_rule_violations",
    "enforce_verdict_rules",
]
