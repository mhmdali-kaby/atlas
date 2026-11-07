"""The Historian's two triggers (brief §7.3, §8, ATL-93).

1. **@Historian mentions.** :func:`atlas_agents.mentions.mention_from_envelope` turns a
   ``warroom.message`` that mentions ``historian`` into a ``MentionRequest``. The run's prompt
   (:func:`mention_prompt`) asks for a short answer ending in one fenced ```json HistoryVerdict
   card. :class:`VerdictReviewer` validates that card (one retry with the validation errors, then
   the run is ``degraded``), applies :func:`~atlas_agents.schemas.enforce_verdict_rules`, runs the
   number guard on the text and the card, and turns the run's final message into a
   ``research_report`` reply to the mention. That reply is the run's only message.
2. **The 07:30 Asia/Baghdad regime table.** :func:`build_regime_table` computes, in code, the
   current regime of the top ``agents.regime_table.top_n`` instruments by 24h quote volume (the
   same ``history.detect_regime`` the agent can call). The Historian writes the commentary
   (:func:`regime_prompt`) and :class:`RegimeTableReviewer` publishes it as kind ``regime_table``
   with the table as the card. :func:`fallback_regime_message` is the message when the run fails,
   so the table is still posted.

Both are registered with the supervisor in :mod:`atlas_agents.agents.historian` (ATL-101).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
from claude_agent_sdk.types import Message
from pydantic import ValidationError

from atlas_agents import numguard
from atlas_agents.mentions import MentionRequest as MentionRequest
from atlas_agents.mentions import mention_from_envelope as mention_from_envelope
from atlas_agents.mentions import resolve_parent as resolve_parent
from atlas_agents.schemas import (
    CardMeta,
    HistoryVerdict,
    RegimeRow,
    RegimeTable,
    VerdictCorrection,
    VerdictCorrectionMeta,
    VerdictEvidence,
    enforce_verdict_rules,
)
from atlas_agents.session import Review
from atlas_agents.tools import ToolContext
from atlas_agents.tools.catalogue import HistorySeriesInput, ListInstrumentsInput
from atlas_agents.tools.history import RegimeOut, detect_regime_tool
from atlas_agents.tools.market import ListInstrumentsOut, list_instruments
from atlas_agents.tools.warroom import MAIN_THREAD

SLUG: Final = "historian"
RESEARCH_REPORT: Final = "research_report"
REGIME_TABLE: Final = "regime_table"
REGIME_JOB: Final = "historian_regime_table"
REGIME_SOURCE: Final = "atlas.regime_table"

_FENCED_JSON: Final = re.compile(r"```(?:json|JSON)?[ \t]*\n(.*?)\n?```", re.DOTALL)


def output_retries(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["output_retries"])


def _min_n(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["history"]["verdict"]["min_n"])


# -- mentions ---------------------------------------------------------------------------------------


# MentionRequest, mention_from_envelope and resolve_parent live in atlas_agents.mentions (ATL-101).


_CARD_FIELDS: Final = (
    '{"type": "history_verdict", "question": str, "inst_id": str|null, "bar": str|null, '
    '"period": "YYYY-MM-DD..YYYY-MM-DD"|null, "n": int, '
    '"distribution": {"<horizon e.g. 24h>": {"median": num, "p10": num, "p90": num, '
    '"pct_positive": 0-100, "n": int}}, "base_rate": {same shape}, '
    '"regime": {"label": str, "probability": 0-1|null, "days_in_regime": num|null}|null, '
    '"stop_noise_check": {"stop_distance_atr": num, "median_4h_range_atr": num, '
    '"verdict": "ok"|"inside_noise"}|null, "overfit_warning": bool, '
    '"verdict": "supports"|"weakly_supports"|"does_not_support"|"contradicts", '
    '"key_stat": str, "data_quality_notes": [str], "data_as_of": ISO-8601 with zone}'
)


def mention_prompt(request: MentionRequest) -> str:
    who = "the owner" if request.author_type == "human" else request.author
    return (
        f"War Room message from {who} ({request.author_type}), id {request.event_id}:\n\n"
        f"> {request.content_md.strip()}\n\n"
        "Answer it with statistics from your history tools. Write a short answer (at most ~180 "
        "words) for the War Room, then end with exactly one fenced ```json block holding a "
        "HistoryVerdict card with these keys and no others:\n"
        f"{_CARD_FIELDS}\n"
        "Every number in the answer and the card must come from a tool result. Do not call the "
        "warroom tools for this answer: Atlas posts your answer as the reply to this message."
    )


def _validation_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in exc.errors()
    )


def split_card(text: str) -> tuple[str, dict[str, Any] | None, str | None]:
    """(content_md, card, error): the last fenced JSON block is the card, the rest is the text."""
    blocks = list(_FENCED_JSON.finditer(text))
    if not blocks:
        stripped = text.strip()
        if stripped.startswith("{"):
            blocks_text, content = stripped, ""
        else:
            return text.strip(), None, "no ```json card block found at the end of the answer"
    else:
        last = blocks[-1]
        blocks_text = last.group(1)
        content = (text[: last.start()] + text[last.end() :]).strip()
    try:
        card = json.loads(blocks_text)
    except ValueError as exc:
        return content, None, f"the card is not valid JSON: {exc}"
    if not isinstance(card, dict):
        return content, None, "the card must be a JSON object"
    return content, card, None


@dataclass
class _ToolCall:
    name: str
    output: dict[str, Any] | None


def tool_calls(messages: Sequence[Message]) -> list[_ToolCall]:
    """Every tool call of the run with its parsed Atlas envelope ({as_of, source, data}) output."""
    names: dict[str, str] = {}
    calls: list[_ToolCall] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    names[block.id] = block.name
        elif isinstance(message, UserMessage) and not isinstance(message.content, str):
            for item in message.content:
                if not isinstance(item, ToolResultBlock):
                    continue
                content = item.content
                raw = (
                    content
                    if isinstance(content, str)
                    else "\n".join(str(c.get("text", "")) for c in content or [] if isinstance(c, Mapping))
                )
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                calls.append(
                    _ToolCall(
                        name=names.get(item.tool_use_id, "unknown"),
                        output=parsed if isinstance(parsed, dict) else None,
                    )
                )
    return calls


def verdict_evidence(calls: Sequence[_ToolCall]) -> VerdictEvidence:
    """Rule 2 of enforce_verdict_rules: a backtest_rule result whose sample has an unrepaired gap.
    The sample size rule uses the card's own ``n``."""
    backtests = [c for c in calls if c.name.endswith("backtest_rule") and c.output is not None]
    gapped = []
    for call in backtests:
        data = (call.output or {}).get("data")
        sample = data.get("sample") if isinstance(data, Mapping) else None
        if isinstance(sample, Mapping) and sample.get("unrepaired_gap"):
            gapped.append(call)
    return VerdictEvidence(
        backtest_based=bool(backtests),
        unrepaired_gap=bool(gapped),
        gap_note="backtest_rule sample.unrepaired_gap" if gapped else None,
    )


def correction_meta(corrections: Sequence[VerdictCorrection], n: int) -> list[VerdictCorrectionMeta]:
    rules = {"small_sample": f"n_below_{_min_n()}"}
    return [
        VerdictCorrectionMeta.model_validate(
            {"from": c.from_verdict, "to": c.to_verdict, "rule": rules.get(c.rule, c.rule), "n": n}
        )
        for c in corrections
    ]


def _guard_meta(text: str, card: Mapping[str, Any], outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return numguard.check(text, card, outputs).to_meta()


class VerdictReviewer:
    """Reviews one @Historian answer: the card must validate as HistoryVerdict (the follow-up
    carries the errors), then the verdict rules cap it and the number guard checks it."""

    def __init__(self, request: MentionRequest, *, reply_to: str | None, thread_id: str | None) -> None:
        self.request = request
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.verdict: HistoryVerdict | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        verdict: HistoryVerdict | None = None
        if card is not None:
            card.pop("meta", None)  # meta is the runtime's, never the agent's
            card.setdefault("type", "history_verdict")
            try:
                verdict = HistoryVerdict.model_validate(card)
            except ValidationError as exc:
                error = f"HistoryVerdict card failed validation: {_validation_errors(exc)}"
        if verdict is None:
            reason = error or "no HistoryVerdict card"
            return Review(follow_up=self._follow_up(reason), failure=f"invalid card after retry: {reason}")

        calls = tool_calls(messages)
        evidence = verdict_evidence(calls)
        corrected, corrections = enforce_verdict_rules(verdict, evidence)
        outputs: list[Mapping[str, Any]] = [c.output for c in calls if c.output is not None]
        # The asker's own numbers ("a 3-day 10% drop") are quoted, not invented.
        asked = {"content_md": self.request.content_md}
        outputs.append({"as_of": None, "source": "warroom.message", "data": asked})
        if not content:
            content = f"**{corrected.verdict.replace('_', ' ')}**: {corrected.key_stat}"
        plain = corrected.model_dump(mode="json", exclude={"meta"})
        guard = _guard_meta(content, plain, outputs)
        meta = CardMeta.model_validate(
            {
                **guard,
                "verdict_corrections": correction_meta(
                    corrections, evidence.n if evidence.n is not None else corrected.n
                ),
            }
        )
        final = corrected.model_copy(update={"meta": meta})
        self.verdict = final
        overrides: dict[str, Any] = {
            "kind": RESEARCH_REPORT,
            "content_md": content,
            "card": final.model_dump(mode="json", by_alias=True),
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
            f"Your HistoryVerdict card was not accepted: {reason}\n\n"
            "Send the whole answer again: the short answer, then exactly one fenced ```json block "
            f"with a corrected HistoryVerdict card:\n{_CARD_FIELDS}\n"
            "This is your only retry."
        )


# -- the 07:30 regime table ------------------------------------------------------------------------


def regime_top_n(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["regime_table"]["top_n"])


async def build_regime_table(ctx: ToolContext, top_n: int | None = None) -> RegimeTable | None:
    """The regime of each of the top ``top_n`` live instruments by 24h quote volume, in that order
    (instruments with no volume are left out). None when no instrument has volume."""
    limit = top_n if top_n is not None else regime_top_n()
    listed = await list_instruments(ctx, ListInstrumentsInput(state="live", limit=1000))
    data = listed.data
    assert isinstance(data, ListInstrumentsOut)
    ranked = [r for r in data.instruments if r.volume_24h_quote is not None and r.volume_24h_quote > 0]
    ranked = ranked[:limit]
    rows: list[RegimeRow] = []
    stamps: list[datetime] = []
    for inst in ranked:
        out = await detect_regime_tool(ctx, HistorySeriesInput(inst_id=inst.inst_id))
        regime = out.data
        assert isinstance(regime, RegimeOut)
        if out.as_of is not None:
            stamps.append(out.as_of)
        if regime.status == "ok" and regime.result is not None:
            r = regime.result
            # HMM posteriors can overshoot 1 by float error (1.0000000000000022).
            p = None if r.probability is None else round(min(max(r.probability, 0.0), 1.0), 4)
            rows.append(
                RegimeRow(
                    inst_id=inst.inst_id,
                    label=r.label,
                    probability=p,
                    days_in_regime=round(r.days_in_regime, 2),
                    method=r.method,
                )
            )
        else:
            rows.append(RegimeRow(inst_id=inst.inst_id, label=regime.status))
    if not rows:
        return None
    return RegimeTable(rows=rows, data_as_of=max(stamps) if stamps else ctx.now())


def _table_json(table: RegimeTable) -> dict[str, Any]:
    return table.model_dump(mode="json", exclude={"meta"})


def regime_prompt(table: RegimeTable) -> str:
    result = {"as_of": table.data_as_of.isoformat(), "source": REGIME_SOURCE, "data": _table_json(table)}
    return (
        "It is 07:30 Asia/Baghdad: time for the daily regime table (brief §8). Atlas computed the "
        f"current regime of the top {len(table.rows)} instruments by 24h quote volume with "
        "history.detect_regime; here it is as a tool result:\n\n"
        f"```json\n{json.dumps(result)}\n```\n\n"
        "Write the commentary that goes above the table (at most ~150 words): the overall picture, "
        "the regimes that just changed, and the instruments whose labels are low-confidence or "
        "missing data. Use only numbers from the table or your tools. Do not repeat the table and "
        "do not call the warroom tools: Atlas posts the table with your text."
    )


def _regime_overrides(table: RegimeTable, content: str) -> dict[str, Any]:
    plain = _table_json(table)
    outputs = [{"as_of": table.data_as_of.isoformat(), "source": REGIME_SOURCE, "data": plain}]
    meta = CardMeta.model_validate(_guard_meta(content, plain, outputs))
    card = table.model_copy(update={"meta": meta}).model_dump(mode="json", by_alias=True)
    return {
        "kind": REGIME_TABLE,
        "thread": dict(MAIN_THREAD),
        "content_md": content,
        "card": card,
        "reply_to": None,
        "mentions": [],
    }


class RegimeTableReviewer:
    """Accepts the Historian's commentary and publishes it with the code-computed table."""

    def __init__(self, table: RegimeTable) -> None:
        self.table = table

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content = text.strip() or f"Regime table: top {len(self.table.rows)} instruments by 24h quote volume."
        return Review(message=_regime_overrides(self.table, content))


def fallback_regime_message(table: RegimeTable, status: str) -> dict[str, Any]:
    """The table's message when the Historian's run did not finish (no commentary)."""
    content = (
        f"Regime table: top {len(table.rows)} instruments by 24h quote volume. "
        f"No commentary today (Historian run {status})."
    )
    return _regime_overrides(table, content)


def message_payload(agent: str, overrides: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    """A complete warroom.message payload for a message the runtime posts outside a run's stream."""
    payload: dict[str, Any] = {
        "author_type": "agent",
        "author": agent,
        "reasoning_md": None,
        "tool_calls": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
        "run_id": run_id,
    }
    payload.update(overrides)
    return payload
