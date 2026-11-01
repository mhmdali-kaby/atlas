"""The Portfolio Manager's prompt and the reviewer that checks and submits its actions (ATL-169).

:func:`run_prompt` gives the model one position with the actions code computed (ATL-168's rules).
:class:`ActionReviewer` reads the model's action block, checks every action against the computed ones
(:mod:`atlas_agents.pm.guard`: never a widened stop, never anything else) and saves each accepted one
through ``warroom.submit_proposal``'s own path (:func:`submit`). One refused block gets one retry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final

from atlas_shared.proposals import EvidenceRef, PositionAction
from claude_agent_sdk.types import Message
from pydantic import ValidationError

from atlas_agents.historian import split_card
from atlas_agents.pm import guard, limits
from atlas_agents.pm.rules import RuleAction
from atlas_agents.session import Review
from atlas_agents.tools.catalogue import SubmitProposalInput
from atlas_agents.tools.framework import ToolContext, ToolError
from atlas_agents.tools.warroom import submit_entry

if TYPE_CHECKING:
    from atlas_agents.pm.manage import Plan


def _d(value: Any) -> str | None:
    return None if value is None else format(value, "f")


# -- the prompt -----------------------------------------------------------------------------------

_BLOCK: Final = (
    '{"actions": [{"kind": "amend_stop"|"partial_close"|"close", "rule": "<rule>", "stop": number|null, '
    '"close_pct": number|null, "rationale_md": "<why, max 80 words>"}], "status_md": "<status line>"}'
)


def _action_view(action: RuleAction) -> dict[str, Any]:
    return {
        "kind": action.kind,
        "rule": guard.rule_label(action),
        "stop": _d(action.stop),
        "close_pct": _d(action.close_pct),
        "reason": action.reason,
    }


def run_prompt(plan: Plan, why: str, now: datetime, limit_usage: Sequence[limits.LimitUsage]) -> str:
    pos = plan.position
    data = {
        "as_of": now.isoformat(),
        "source": "atlas.portfolio_manager.check",
        "data": {
            "position": pos.view(),
            "r_multiple": _d(plan.evaluation.r_multiple),
            "computed_actions": [_action_view(a) for a in plan.actions],
            "advisories": [_action_view(a) for a in plan.advisories],
            "dropped": [
                {"rule": d.rule, "reason": d.reason, "stop": _d(d.stop)} for d in plan.evaluation.dropped
            ],
            "pending_kinds": sorted(pos.pending_kinds),
            "limits": [item.card() for item in limit_usage],
        },
    }
    actions_line = (
        "Code computed the actions in computed_actions from the management rules. Return each of them "
        "exactly (same kind, rule, stop and close_pct); you may not change, add or drop one, and a stop "
        "never moves away from the market. "
        if plan.actions
        else "No management rule fired, so there is nothing to submit: return an empty actions list. "
    )
    return (
        f"Position check on {pos.inst_id} ({pos.side}). Why now: {why}\n\n"
        f"```json\n{json.dumps(data, default=str)}\n```\n\n"
        "Write the status line (instrument, side, PnL in USD and R, distance to stop and TP1 in ATR, time in "
        "trade) and say whether the original thesis is intact, using your read tools for the newest "
        "Technical, Positioning and News items. "
        + actions_line
        + "Atlas submits the actions you return, sends each one to the Risk Engine (no team vote) and then "
        "to the owner as the autonomy setting says. Do not call warroom.submit_proposal yourself.\n"
        f"End with exactly one fenced ```json block:\n{_BLOCK}"
    )


# -- the reviewer ---------------------------------------------------------------------------------


class ActionReviewer:
    """Reads the action block, checks each action against the computed ones and submits it."""

    def __init__(self, ctx: ToolContext, plan: Plan, expected: guard.Expected) -> None:
        self.ctx = ctx
        self.plan = plan
        self.expected = expected
        self.refusals: list[str] = []

    def _evidence(self) -> list[EvidenceRef]:
        pid = self.plan.position.plan.proposal_id if self.plan.position.plan else None
        return [EvidenceRef(type="proposal", id=pid)] if pid else []

    def _model(self, raw: Mapping[str, Any]) -> tuple[str, PositionAction]:
        kind = str(raw.get("kind") or "")
        model = PositionAction.model_validate(
            {
                "inst_id": self.plan.position.inst_id,
                "stop": raw.get("stop"),
                "close_pct": raw.get("close_pct"),
                "rule": str(raw.get("rule") or ""),
                "rationale_md": str(raw.get("rationale_md") or "").strip() or "Management rule fired.",
                "evidence": [e.model_dump() for e in self._evidence()],
            }
        )
        return kind, model

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        raw_actions = card.get("actions") if card is not None else None
        if card is not None and not isinstance(raw_actions, list):
            error = '"actions" must be a list'
        if error is not None or card is None:
            return Review(
                follow_up=self._follow_up(error or "no action block"),
                failure=f"invalid action block after retry: {error}",
            )
        refused: list[str] = []
        accepted: list[tuple[str, PositionAction, RuleAction]] = []
        for raw in raw_actions or []:
            if not isinstance(raw, Mapping):
                refused.append("an action is not an object")
                continue
            try:
                kind, model = self._model(raw)
                action = guard.match(self.expected, kind, model)
            except ValidationError as exc:
                refused.append(f"invalid action: {exc.error_count()} schema error(s)")
                continue
            except ToolError as exc:
                refused.append(f"{exc.code}: {exc}")
                continue
            accepted.append((kind, model, action))
        if refused and attempt == 1:
            self.refusals = refused
            return Review(follow_up=self._follow_up("; ".join(refused)))
        self.refusals = refused
        notes: list[str] = []
        card_actions: list[dict[str, Any]] = []
        for kind, model, _action in accepted:
            note, pid = await submit_detail(self.ctx, kind, model)
            notes.append(note)
            card_actions.append(
                {
                    "kind": kind,
                    "rule": model.rule,
                    "stop": _p(model.stop),
                    "close_pct": _p(model.close_pct),
                    "proposal_id": pid,
                    "status": "submitted" if pid else "not_submitted",
                }
            )
        if refused:
            notes.append("Refused by code: " + "; ".join(refused))
        status = card.get("status_md")
        body = "\n\n".join(
            part for part in (str(status).strip() if isinstance(status, str) else "", content, *notes) if part
        )
        return Review(
            message={
                "kind": "position_update",
                "content_md": body or "Position checked.",
                "card": status_card(self.plan, self.ctx.now(), card_actions),
            }
        )

    def _follow_up(self, reason: str) -> str:
        wanted = guard.describe(self.expected.actions) or ["no action (empty list)"]
        return (
            f"Atlas did not accept your answer: {reason}\n\nThe only actions you may return are: "
            f"{'; '.join(wanted)}. Send the whole answer again ending with exactly one ```json block "
            f"{_BLOCK}. This is your only retry."
        )


async def submit_detail(ctx: ToolContext, kind: str, model: PositionAction) -> tuple[str, str | None]:
    """Save one checked action through submit_proposal's path: (the War Room note, proposal id or
    None when it was not saved)."""
    args = SubmitProposalInput(
        action="submit",
        kind=kind,
        proposal=model.model_dump(mode="json"),
        summary_md=None,
    )
    try:
        result = await asyncio.to_thread(submit_entry, ctx, args)
    except ToolError as exc:
        return f"Not submitted ({kind} {model.rule}): {exc.code}: {exc}", None
    data: Any = result.data
    out = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    pid = str(out["proposal_id"])
    return f"Submitted **{kind}** ({model.rule}) on {model.inst_id}: proposal {pid}.", pid


async def submit(ctx: ToolContext, kind: str, model: PositionAction) -> str:
    """Save one checked action through submit_proposal's path; the War Room note for it."""
    note, _pid = await submit_detail(ctx, kind, model)
    return note


# -- the position_update card (ATL-178) -----------------------------------------------------------

CARD_TYPE: Final = "position_update"
_CENT: Final = Decimal("0.01")


def _q(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def _p(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def status_card(plan: Plan, now: datetime, actions: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The figures of a position check, computed by code (never the model's): PnL in USD and R,
    distance to the stop and TP1 in ATR(1H), time in trade, funding paid, and the actions submitted."""
    pos = plan.position
    s = pos.state
    atr = pos.atr_1h if pos.atr_1h is not None and pos.atr_1h > 0 else None
    sign = Decimal(1) if pos.side == "long" else Decimal(-1)
    stop_atr = None
    if s.stop is not None and atr is not None:
        stop_atr = (s.mark_price - s.stop) * sign / atr
    tp1: Decimal | None = None
    if pos.plan is not None and pos.plan.take_profits and not s.tp1_filled:
        tp1 = pos.plan.take_profits[0].price
    tp1_atr = (tp1 - s.mark_price) * sign / atr if tp1 is not None and atr is not None else None
    minutes = max(0, int((now - s.opened_at).total_seconds() // 60))
    return {
        "type": CARD_TYPE,
        "inst_id": pos.inst_id,
        "side": pos.side,
        "size": _p(pos.size),
        "entry": _p(s.entry_price),
        "mark": _p(s.mark_price),
        "stop": _p(s.stop),
        "initial_stop": _p(pos.plan.stop) if pos.plan else None,
        "targets": [_p(t.price) for t in (pos.plan.take_profits if pos.plan else ())],
        "tp1": _p(tp1),
        "tp1_filled": s.tp1_filled,
        "pnl_usd": _q(pos.upl),
        "r": _q(plan.evaluation.r_multiple),
        "atr_1h": _p(atr),
        "stop_distance_atr": _q(stop_atr),
        "tp1_distance_atr": _q(tp1_atr),
        "opened_at": s.opened_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "minutes_in_trade": minutes,
        "funding_paid_usd": _q(pos.funding_paid_usd),
        "opening_proposal_id": pos.plan.proposal_id if pos.plan else None,
        "actions": [dict(a) for a in actions],
        "advisories": [{"rule": guard.rule_label(a), "reason": a.reason} for a in plan.advisories],
        "as_of": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


__all__ = ["CARD_TYPE", "ActionReviewer", "run_prompt", "status_card", "submit", "submit_detail"]
