"""The Derivatives & Sentiment Analyst's run (brief §7.5, ATL-136): one PositioningReport card per
instrument.

A run is for one instrument. :func:`analyse` computes the instrument's positioning features in code
(:func:`load_features`, the same data ``okx_market.get_positioning_features`` returns), gives them
to the model as a tool result, and asks for a short summary ending in one fenced ```json
PositioningReport card. :class:`ReportReviewer` then applies the code rules (:func:`enforce_report`)
before validating:

* ``regime`` is the OI-price quadrant of the first window in
  ``agents.derivatives_sentiment.regime_windows_hours`` that has one ("flat" when none has), and
  ``quadrants`` lists every window's — whatever the model wrote;
* ``basis_bps`` and ``basis_reason`` are the features' (null with a reason when the mark or the
  index is stale or missing); ``underlying_premium_pct`` is null (no underlying reference price is
  stored yet);
* ``squeeze_risk`` values are clamped to 0..1;
* when the features are unreliable (zero OI, zero volume, zero taker volume), ``data_reliable`` is
  false, the reasons are listed, a note says the data is unreliable and ``supports_direction`` is
  neutral for both sides;
* ``data_as_of`` is the features'.

Each override is recorded in ``meta.corrections``. An invalid card gets one retry with the errors;
a second invalid card ends the run ``degraded``. The card is posted as a ``research_report``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS
from atlas_shared.positioning import PositioningReport
from claude_agent_sdk.types import Message
from pydantic import ValidationError

from atlas_agents import numguard
from atlas_agents.derivatives.features import DerivativesFeatures
from atlas_agents.historian import split_card, tool_calls
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult, Trigger
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.positioning import SOURCE as FEATURES_SOURCE
from atlas_agents.tools.positioning import _features
from atlas_agents.tools.warroom import MAIN_THREAD

SLUG: Final = "derivatives_sentiment"
RESEARCH_REPORT: Final = "research_report"
CARD_TYPE: Final = "positioning_report"
CODE_FIELDS: Final = ("meta", "notes", "quadrants", "data_reliable", "unreliable_reasons", "basis_reason")

log = logging.getLogger(__name__)


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["derivatives_sentiment"]
    return cfg


def output_retries(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["output_retries"])


async def load_features(ctx: ToolContext, inst_id: str) -> DerivativesFeatures:
    """The instrument's features as of ``ctx.now()``. Raises ToolError not_found for an unknown one."""
    return await asyncio.to_thread(_features, ctx, inst_id)


# -- the code rules ----------------------------------------------------------------------------------


def quadrants(features: DerivativesFeatures) -> dict[str, str | None]:
    """OI-price quadrant per window, keyed "1h"/"4h"/"24h" (null when the window had no data)."""
    return {f"{w.hours}h": w.quadrant for w in features.open_interest.windows}


def regime_of(features: DerivativesFeatures, defaults: Mapping[str, Any] = DEFAULTS) -> str:
    """The card's regime: the quadrant of the first configured window that has one, else "flat"."""
    by_hours = {w.hours: w.quadrant for w in features.open_interest.windows}
    for hours in config(defaults)["regime_windows_hours"]:
        quadrant = by_hours.get(int(hours))
        if quadrant is not None:
            return str(quadrant)
    return "flat"


def basis_bps(features: DerivativesFeatures) -> Decimal | None:
    value = features.basis.basis_bps
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def unreliable_note(features: DerivativesFeatures) -> str:
    reasons = "; ".join(features.unreliable_reasons.values()) or "abnormal readings"
    return (
        f"**Data unreliable for {features.inst_id}:** {reasons}. Atlas does not read positioning from "
        "this data: neutral for both long and short."
    )


def _clamp01(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return value
    try:
        number = float(value)
    except ValueError:
        return value
    return min(1.0, max(0.0, number))


def enforce_report(
    card: dict[str, Any], features: DerivativesFeatures, defaults: Mapping[str, Any] = DEFAULTS
) -> list[dict[str, Any]]:
    """Apply the §7.5 code rules to a raw card in place (before validation). Returns corrections."""
    corrections: list[dict[str, Any]] = []
    for key in CODE_FIELDS:  # the runtime's, never the model's
        card.pop(key, None)
    card["type"] = CARD_TYPE
    card["inst_id"] = features.inst_id
    card["data_as_of"] = features.data_as_of.isoformat()

    regime = regime_of(features, defaults)
    if card.get("regime") != regime:
        corrections.append({"rule": "regime_from_features", "model": card.get("regime"), "code": regime})
    card["regime"] = regime
    card["quadrants"] = quadrants(features)

    basis = basis_bps(features)
    model_basis = card.get("basis_bps")
    if model_basis is not None and (basis is None or str(model_basis) != str(basis)):
        corrections.append({"rule": "basis_from_features", "model": model_basis, "code": _str(basis)})
    card["basis_bps"] = _str(basis)
    card["basis_reason"] = features.basis.reason

    if card.get("underlying_premium_pct") is not None:
        corrections.append(
            {"rule": "no_underlying_reference", "model": card.get("underlying_premium_pct"), "code": None}
        )
    card["underlying_premium_pct"] = None

    squeeze = card.get("squeeze_risk")
    if isinstance(squeeze, Mapping):
        clamped = {k: _clamp01(v) for k, v in squeeze.items()}
        for kind, value in squeeze.items():
            if clamped[kind] != value and not _same_number(clamped[kind], value):
                corrections.append(
                    {"rule": "squeeze_risk_clamped", "kind": kind, "model": value, "code": clamped[kind]}
                )
        card["squeeze_risk"] = clamped

    notes: list[str] = []
    if features.unreliable:
        neutral = {"long": "neutral", "short": "neutral"}
        if card.get("supports_direction") != neutral:
            corrections.append(
                {"rule": "unreliable_data_neutral", "model": card.get("supports_direction"), "code": neutral}
            )
        card["supports_direction"] = neutral
        card["data_reliable"] = False
        card["unreliable_reasons"] = dict(features.unreliable_reasons)
        notes.append(unreliable_note(features))
    card["notes"] = notes
    return corrections


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _same_number(a: Any, b: Any) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


# -- the run ------------------------------------------------------------------------------------------

_CARD_FIELDS: Final = (
    '{"type": "positioning_report", "inst_id": str, "regime": "new_longs"|"short_covering"|'
    '"new_shorts"|"long_liquidation"|"flat" (Atlas sets it from the computed quadrant), '
    '"crowding_score": 0-1 (1 = extremely one-sided), "crowded_side": "long"|"short"|"none", '
    '"squeeze_risk": {"long_squeeze": 0-1, "short_squeeze": 0-1}, "funding_view": str, '
    '"basis_bps": num|null (Atlas sets it), "underlying_premium_pct": null, '
    '"sentiment_score": -1..1, "supports_direction": {"long": "supports"|"neutral"|"opposes", '
    '"short": "supports"|"neutral"|"opposes"}, "key_evidence": [str, each with numbers and as_of] '
    '(at most 10), "data_as_of": ISO-8601 with zone}'
)


def _features_output(features: DerivativesFeatures) -> dict[str, Any]:
    return {
        "as_of": features.data_as_of.isoformat(),
        "source": FEATURES_SOURCE,
        "data": features.model_dump(mode="json"),
    }


def run_prompt(features: DerivativesFeatures, why: str) -> str:
    unreliable = (
        "\n\nThe data is flagged UNRELIABLE ("
        + "; ".join(features.unreliable_reasons.values())
        + "): say so instead of interpreting it, and give neutral for both sides."
        if features.unreliable
        else ""
    )
    return (
        f"{why}\n\nRead the positioning of {features.inst_id} (brief §7.5). Atlas computed its "
        "derivatives features (okx_market.get_positioning_features); here they are as a tool result:"
        f"\n\n```json\n{json.dumps(_features_output(features))}\n```{unreliable}\n\n"
        "Use news.search_news (sentiment fields) and history.event_study if an extreme reading needs "
        "context. Write a summary of at most 100 words, then end with exactly one fenced ```json "
        f"block holding a PositioningReport card with these keys and no others:\n{_CARD_FIELDS}\n"
        "Every number must come from the features or your tools. Do not call the warroom tools for "
        "this answer: Atlas checks the card (regime, basis, ranges) and posts it."
    )


class ReportReviewer:
    """Validates one PositioningReport card after :func:`enforce_report` and the number guard, and
    makes it the run's ``research_report`` message."""

    def __init__(
        self,
        features: DerivativesFeatures,
        *,
        reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.features = features
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.report: PositioningReport | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        report: PositioningReport | None = None
        corrections: list[dict[str, Any]] = []
        if card is not None:
            corrections = enforce_report(card, self.features)
            try:
                report = PositioningReport.model_validate(card)
            except ValidationError as exc:
                error = "PositioningReport card failed validation: " + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
                )
        if report is None:
            reason = error or "no PositioningReport card"
            return Review(follow_up=self._follow_up(reason), failure=f"invalid card after retry: {reason}")

        outputs: list[Mapping[str, Any]] = [c.output for c in tool_calls(messages) if c.output is not None]
        outputs.append(_features_output(self.features))
        if not content:
            content = _summary(report)
        for note in report.notes:
            content = f"{note}\n\n{content}"
        plain = report.model_dump(mode="json", exclude={"meta"})
        meta = {**numguard.check(content, plain, outputs).to_meta(), "corrections": corrections}
        self.report = report.model_copy(update={"meta": meta})
        overrides: dict[str, Any] = {
            "kind": RESEARCH_REPORT,
            "content_md": content,
            "card": self.report.model_dump(mode="json"),
            "reply_to": self.reply_to,
            "mentions": [],
        }
        if self.thread_id is not None:
            overrides["thread_id"] = self.thread_id
        else:
            overrides["thread"] = dict(MAIN_THREAD)
        return Review(message=overrides)

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your PositioningReport card was not accepted: {reason}\n\nSend the whole answer again: "
            "the short summary, then exactly one fenced ```json block with a corrected "
            f"PositioningReport card:\n{_CARD_FIELDS}\nThis is your only retry."
        )


def _summary(report: PositioningReport) -> str:
    regime = report.regime.replace("_", " ")
    return (
        f"**{report.inst_id}: {regime}.** Crowding {report.crowding_score:.2f} ({report.crowded_side}); "
        f"long {report.supports_direction['long']}, short {report.supports_direction['short']}."
    )


async def analyse(
    host: AgentHost,
    inst_id: str,
    *,
    trigger: Trigger,
    correlation_id: str | None,
    why: str,
    reply_to: str | None = None,
    thread_id: str | None = None,
) -> RunResult | None:
    """One instrument: one run whose final message is the checked PositioningReport card. None when
    nothing ran (unknown instrument)."""
    ctx = host.tool_context(SLUG)
    try:
        features = await load_features(ctx, inst_id)
    except ToolError as exc:
        log.warning("derivatives_sentiment: %s skipped: %s: %s", inst_id, exc.code, exc)
        return None
    reviewer = ReportReviewer(features, reply_to=reply_to, thread_id=thread_id)
    return await host.run(
        SLUG,
        run_prompt(features, why),
        trigger=trigger,
        task=f"positioning {inst_id}",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


__all__ = [
    "CARD_TYPE",
    "RESEARCH_REPORT",
    "SLUG",
    "ReportReviewer",
    "analyse",
    "basis_bps",
    "config",
    "enforce_report",
    "load_features",
    "quadrants",
    "regime_of",
    "run_prompt",
    "unreliable_note",
]
