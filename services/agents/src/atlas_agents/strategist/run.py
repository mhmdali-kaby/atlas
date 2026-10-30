"""One Strategist run for one instrument (brief §7.6, ATL-158).

:func:`plan` gathers the instrument's inputs in code (:mod:`atlas_agents.strategist.inputs`), then:

1. **No TechnicalSetup, a "none" setup, or one older than ``setup_max_age_minutes``: no trade.**
   No model run starts; a ``text`` message "No trade on X" naming the missing input is posted
   (§7.6: "Do not propose without the Technical setup").
2. Otherwise the model gets the inputs (with the sizing guidance, leverage and analog hit rate
   already computed) and answers with its synthesis ending in one fenced ```json block, either
   ``{"decision": "trade", "proposal": {TradeProposal}, "summary_md": "..."}`` or
   ``{"decision": "no_trade", "reasons": ["..."]}``. :class:`PlanReviewer` then:

   * ``no_trade`` — the run's final message is a ``text`` message with the reasons; nothing is
     submitted;
   * ``trade`` — code submits it once through ``warroom.submit_proposal``'s own path
     (:func:`atlas_agents.tools.warroom.submit_entry`), which applies the Strategist rules (size,
     leverage, probability clamp, defaults; :mod:`atlas_agents.strategist.rules`) and the six-item
     checklist, and carries the run's correlation id, so a cycle run's proposal gets its cycle_id.
     A refusal goes back to the model once; refused again, the run posts "no trade" with the
     refusal. An unreadable answer twice ends the run ``degraded``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from typing import Any, Final

from claude_agent_sdk.types import Message

from atlas_agents import events
from atlas_agents import proposals as props
from atlas_agents.historian import message_payload, split_card
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult, Trigger
from atlas_agents.strategist import inputs as inp
from atlas_agents.strategist import sizing
from atlas_agents.tools.catalogue import SubmitProposalInput
from atlas_agents.tools.framework import ToolContext, ToolError
from atlas_agents.tools.warroom import MAIN_THREAD, submit_entry

SLUG: Final = "strategist"
WARROOM_STREAM: Final = "atlas.warroom"
DECISIONS: Final = ("trade", "no_trade")

_PROPOSAL_FIELDS: Final = (
    '{"inst_id", "side": "buy"|"sell", "order_type": "limit"|"post_only"|"market", '
    '"entry": number|null, "entry_zone": [low, high]|null, "stop": number, '
    '"take_profits": [{"price": number, "pct": number}] (1-3), "time_stop_minutes": int|null, '
    '"size_suggestion_pct_equity": number, "leverage_suggestion": int, "expected_r_multiple": number, '
    '"probability_estimate": 0-1, "rationale_md", "invalidation", '
    '"evidence": [{"type": "research_report"|"news"|"stat", "id": "<message id>"}]}'
)


def output_retries() -> int:
    from atlas_agents.technical_analyst import output_retries as retries

    return retries()


def setup_problem(inputs: inp.StrategistInputs) -> str | None:
    """Why there is no usable TechnicalSetup, or None when there is one."""
    item = inputs.item(inp.TECHNICAL)
    if item is None:
        return (
            f"no TechnicalSetup from the Technical Analyst for {inputs.inst_id} "
            "(missing input: technical_setup)"
        )
    if item.card.get("setup_type") == "none":
        reason = item.card.get("no_setup_reason") or ""
        age = item.age_minutes
        return f"the Technical Analyst's newest setup ({age} min old) says no setup. {reason}".strip()
    max_age = int(props.checklist_config()["setup_max_age_minutes"])
    if item.age_minutes >= max_age:
        return (
            f"the newest TechnicalSetup is {item.age_minutes} min old; it must be under {max_age} min "
            "(missing input: a fresh technical_setup)"
        )
    return None


def no_trade_message(
    inst_id: str, reasons: list[str], *, reply_to: str | None, thread_id: str | None
) -> dict[str, Any]:
    lines = "\n".join(f"- {r}" for r in reasons if r.strip())
    overrides: dict[str, Any] = {
        "kind": "text",
        "content_md": f"**No trade on {inst_id}.**\n{lines}".strip(),
        "card": None,
        "reply_to": reply_to,
        "mentions": [],
    }
    if thread_id is not None:
        overrides["thread_id"] = thread_id
    else:
        overrides["thread"] = dict(MAIN_THREAD)
    return overrides


def run_prompt(inputs: inp.StrategistInputs, why: str) -> str:
    rules = "\n".join(f"- {line}" for line in sizing.multipliers_table())
    return (
        f"{why}\n\nPlan {inputs.inst_id} (brief §7.6). Atlas gathered the team's latest research, the "
        "portfolio and the settings for it; here they are as a tool result (each input with its age; "
        "`missing` lists the ones that do not exist):\n\n"
        f"```json\n{json.dumps(inputs.to_json(), default=str)}\n```\n\n"
        "Atlas already computed the size suggestion (base_pct x conviction multiplier x volatility "
        f"multiplier):\n{rules}\nand the lowest leverage that fits. Use `sizing.size_suggestion_pct_equity` "
        "as the ceiling and `leverage_suggestion` as given. You may go below the size (say why, e.g. "
        "correlated exposure); a size above it, or none, becomes the suggestion. Anchor "
        "your probability on `analog_hit_rate` for your side; Atlas clamps it to within the allowed "
        "points of that rate. When you leave time_stop_minutes null or a take-profit pct out, Atlas uses "
        "`default_time_stop_minutes` and `default_take_profit_split_pct`.\n\n"
        "Write the 3-line synthesis and your one-line probability adjustment, then end with exactly "
        "one fenced ```json block, either\n"
        f'{{"decision": "trade", "proposal": {_PROPOSAL_FIELDS}, "summary_md": "max 150 words"}}\n'
        'or {"decision": "no_trade", "reasons": ["..."]}.\n'
        "Do not call warroom.submit_proposal for this answer: Atlas submits the proposal you return, "
        "once, and tells you if the checklist refuses it."
    )


class PlanReviewer:
    """Reads the decision block; submits a trade through submit_proposal's path, or posts no trade."""

    def __init__(
        self,
        ctx: ToolContext,
        inputs: inp.StrategistInputs,
        *,
        reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.inputs = inputs
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.submitted: dict[str, Any] | None = None
        self.refusal: str | None = None

    def _message(self, content: str) -> dict[str, Any]:
        overrides: dict[str, Any] = {
            "kind": "text",
            "content_md": content,
            "reply_to": self.reply_to,
            "mentions": [],
        }
        if self.thread_id is not None:
            overrides["thread_id"] = self.thread_id
        else:
            overrides["thread"] = dict(MAIN_THREAD)
        return overrides

    def _evidence(self) -> list[dict[str, str]]:
        return [
            {"type": "research_report", "id": item.message_id}
            for item in self.inputs.items.values()
            if item is not None and item.message_id
        ]

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        decision = card.get("decision") if card is not None else None
        if card is not None and decision not in DECISIONS:
            error = 'the block needs "decision": "trade" or "no_trade"'
        if error is not None or card is None:
            reason = error or "no decision block"
            return Review(
                follow_up=self._follow_up(reason), failure=f"invalid decision after retry: {reason}"
            )

        if decision == "no_trade":
            raw_reasons = card.get("reasons")
            reasons = [str(r) for r in raw_reasons] if isinstance(raw_reasons, list) else []
            if not reasons:
                reasons = [content or "no reason given"]
            body = no_trade_message(
                self.inputs.inst_id, reasons, reply_to=self.reply_to, thread_id=self.thread_id
            )
            if content:
                body["content_md"] = f"{body['content_md']}\n\n{content}"
            return Review(message=body)

        proposal = card.get("proposal")
        if not isinstance(proposal, dict):
            return Review(
                follow_up=self._follow_up('"proposal" must be the TradeProposal object'),
                failure="invalid decision after retry: no proposal object",
            )
        proposal["inst_id"] = self.inputs.inst_id
        if not proposal.get("evidence"):
            proposal["evidence"] = self._evidence()
        summary = card.get("summary_md")
        args = SubmitProposalInput(
            action="submit",
            kind="entry",
            proposal=proposal,
            summary_md=summary if isinstance(summary, str) and summary.strip() else None,
        )
        try:
            result = await asyncio.to_thread(submit_entry, self.ctx, args)
        except ToolError as exc:
            self.refusal = f"{exc.code}: {exc}"
            if attempt == 1:
                return Review(
                    follow_up=(
                        f"Atlas did not save the proposal: {self.refusal}\n\nFix it and send the whole "
                        "answer again ending with one decision block, or decide no_trade. This is your "
                        "only retry."
                    )
                )
            body = no_trade_message(
                self.inputs.inst_id,
                [f"the proposal was refused: {self.refusal}"],
                reply_to=self.reply_to,
                thread_id=self.thread_id,
            )
            if content:
                body["content_md"] = f"{body['content_md']}\n\n{content}"
            return Review(message=body)
        data = result.data
        out = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        self.submitted = out
        note = f"Proposal **v{out['version']}** submitted for {self.inputs.inst_id} ({out['proposal_id']})."
        return Review(message=self._message(f"{content}\n\n{note}".strip()))

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your answer was not accepted: {reason}\n\nSend the whole answer again ending with exactly one "
            '```json block: {"decision": "trade", "proposal": {...}, "summary_md": "..."} or '
            '{"decision": "no_trade", "reasons": [...]}. This is your only retry.'
        )


def _publish_no_trade(host: AgentHost, overrides: Mapping[str, Any], correlation_id: str | None) -> None:
    if host.replaying:
        return
    payload = message_payload(SLUG, overrides, "")
    payload["run_id"] = None  # code's decision, not the model's
    host.publish(
        WARROOM_STREAM,
        events.envelope(
            type_="warroom.message", env=host.env, agent=SLUG, payload=payload, correlation_id=correlation_id
        ),
    )


def run_context(host: AgentHost) -> ToolContext:
    """The Strategist's tool context with the host's publisher (tests record it; live it is the bus)."""
    return dataclasses.replace(host.tool_context(SLUG), publish=host.publish)


async def plan(
    host: AgentHost,
    inst_id: str,
    *,
    trigger: Trigger,
    correlation_id: str | None,
    why: str,
    reply_to: str | None = None,
    thread_id: str | None = None,
) -> RunResult | None:
    """One instrument: "no trade" in code without a usable TechnicalSetup (None: no run), else one
    run whose answer is submitted as a proposal or posted as no trade."""
    ctx = run_context(host)

    def load() -> inp.StrategistInputs:
        with ctx.session_factory() as session:
            return inp.gather(session, ctx, inst_id)

    inputs = await asyncio.to_thread(load)
    problem = setup_problem(inputs)
    if problem is not None:
        _publish_no_trade(
            host, no_trade_message(inst_id, [problem], reply_to=reply_to, thread_id=thread_id), correlation_id
        )
        return None
    reviewer = PlanReviewer(ctx, inputs, reply_to=reply_to, thread_id=thread_id)
    return await host.run(
        SLUG,
        run_prompt(inputs, why),
        trigger=trigger,
        task=f"plan {inst_id}",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


def cycle_correlation_id(cycle_id: str) -> str:
    """The correlation id form submit_proposal reads a cycle_id from (ATL-153)."""
    return f"cycle-{cycle_id}"
