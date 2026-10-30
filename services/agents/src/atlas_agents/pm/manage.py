"""The Portfolio Manager's position checks (brief §7.8, ATL-169).

**Every 5 minutes** (:func:`tick`), code loads every open position (:mod:`atlas_agents.pm.inputs`) and
runs ATL-168's rules (:func:`atlas_agents.pm.rules.evaluate_full`):

* no rule fires → no model call and no message (the cheap path);
* an amend_stop / partial_close / close fires → one run on the agent's (strong) model with the
  computed action prefilled. The model writes the reasoning and returns the action block; code
  checks it against the computed action (:mod:`atlas_agents.pm.guard`: never a widened stop, never
  anything else) and submits it through ``warroom.submit_proposal``'s own path, naming the rule. A
  refused block gets one retry; a computed action the model leaves out or gets wrong twice is
  submitted by code with the rule's reason (rule compliance is 100 %);
* only an advisory fires (funding flag / suggest_close) → a run at most once per
  ``status_post_interval_minutes`` per position;
* a kind already on its way (a PM proposal of that kind still pending) is not proposed again.

Each saved proposal skips the vote and goes to the Risk Engine, then autonomy
(:func:`atlas_agents.pm.route.route`).

**Immediately** (:func:`trigger`): a news alert on a held instrument, a fill, a circuit breaker
(every held position), and once per event ``pre_event_minutes`` (30) before an event of importance
>= ``pre_event_min_importance`` (4) — each runs the model for the position whether or not a rule
fires, so the PM can say whether the thesis still holds.

**Limits:** every tick warns once per ``limit_warning_interval_minutes`` per limit at
``limit_warning_pct`` % of the daily/weekly loss or open-position limit (:mod:`atlas_agents.pm.limits`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import Coroutine, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.proposals import EvidenceRef, PositionAction
from claude_agent_sdk.types import Message
from pydantic import ValidationError

from atlas_agents.historian import split_card
from atlas_agents.orchestrator.trading_core import TradingCoreClient
from atlas_agents.pm import guard, limits
from atlas_agents.pm import inputs as inp
from atlas_agents.pm.rules import PROPOSAL_KINDS, Evaluation, RuleAction, evaluate_full
from atlas_agents.registry import AgentHost
from atlas_agents.session import Review, RunResult, Trigger
from atlas_agents.tools.catalogue import SubmitProposalInput
from atlas_agents.tools.framework import RunScope, ToolContext, ToolError
from atlas_agents.tools.warroom import message_envelope, submit_entry

log = logging.getLogger(__name__)

SLUG: Final = "portfolio_manager"
WARROOM_STREAM: Final = "atlas.warroom"


def config() -> Mapping[str, Any]:
    return inp.config()


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _d(value: Any) -> str | None:
    return None if value is None else format(value, "f")


# -- process state --------------------------------------------------------------------------------

_tasks: set[asyncio.Task[Any]] = set()
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_advised: dict[tuple[str, str], datetime] = {}
"""(env, inst_id) -> last advisory-only run."""
_pre_event: set[tuple[str, str, str]] = set()
"""(env, inst_id, event id) already given its pre-event run."""
_risk_client: TradingCoreClient | None = None


def set_risk_client(client: TradingCoreClient | None) -> None:
    """Use ``client`` for the risk checks (tests); None restores the default."""
    global _risk_client
    _risk_client = client


def risk_client() -> TradingCoreClient:
    global _risk_client
    if _risk_client is None:
        _risk_client = TradingCoreClient()
    return _risk_client


def reset() -> None:
    """Forget the in-process memory (tests)."""
    _advised.clear()
    _pre_event.clear()
    limits.reset()


def background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("portfolio_manager: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Portfolio Manager check to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _lock(env: str, inst_id: str) -> asyncio.Lock:
    return _locks.setdefault((env, inst_id), asyncio.Lock())


def run_context(host: AgentHost, correlation_id: str | None = None) -> ToolContext:
    """The PM's tool context with the host's publisher and its own run scope."""
    return dataclasses.replace(
        host.tool_context(SLUG), publish=host.publish, correlation_id=correlation_id, run=RunScope()
    )


# -- evaluating -----------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Plan:
    """What the rules say for one position right now."""

    position: inp.HeldPosition
    evaluation: Evaluation
    actions: tuple[RuleAction, ...]
    """amend_stop / partial_close / close to submit (minus kinds already pending)."""
    advisories: tuple[RuleAction, ...]

    @property
    def quiet(self) -> bool:
        return not self.actions and not self.advisories


def plan_for(position: inp.HeldPosition, now: datetime) -> Plan:
    ev = evaluate_full(
        position.state,
        position.plan,
        position.atr_1h,
        position.last_swing,
        position.events,
        position.funding,
        now,
        position.settings,
    )
    actions = tuple(
        a for a in ev.actions if a.kind in PROPOSAL_KINDS and a.kind not in position.pending_kinds
    )
    advisories = tuple(a for a in ev.actions if a.kind not in PROPOSAL_KINDS)
    return Plan(position=position, evaluation=ev, actions=actions, advisories=advisories)


def _upcoming_event(position: inp.HeldPosition, now: datetime, cfg: Mapping[str, Any]) -> Any:
    lead = timedelta(minutes=int(cfg["pre_event_minutes"]))
    floor = int(cfg["pre_event_min_importance"])
    for event in position.events:
        if event.importance >= floor and now <= event.scheduled_at <= now + lead:
            return event
    return None


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
        for kind, model, _action in accepted:
            notes.append(await submit(self.ctx, kind, model))
        if refused:
            notes.append("Refused by code: " + "; ".join(refused))
        status = card.get("status_md")
        body = "\n\n".join(
            part for part in (str(status).strip() if isinstance(status, str) else "", content, *notes) if part
        )
        return Review(
            message={"kind": "position_update", "content_md": body or "Position checked.", "card": None}
        )

    def _follow_up(self, reason: str) -> str:
        wanted = guard.describe(self.expected.actions) or ["no action (empty list)"]
        return (
            f"Atlas did not accept your answer: {reason}\n\nThe only actions you may return are: "
            f"{'; '.join(wanted)}. Send the whole answer again ending with exactly one ```json block "
            f"{_BLOCK}. This is your only retry."
        )


async def submit(ctx: ToolContext, kind: str, model: PositionAction) -> str:
    """Save one checked action through submit_proposal's path; the War Room note for it."""
    args = SubmitProposalInput(
        action="submit",
        kind=kind,
        proposal=model.model_dump(mode="json"),
        summary_md=None,
    )
    try:
        result = await asyncio.to_thread(submit_entry, ctx, args)
    except ToolError as exc:
        return f"Not submitted ({kind} {model.rule}): {exc.code}: {exc}"
    data: Any = result.data
    out = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    return f"Submitted **{kind}** ({model.rule}) on {model.inst_id}: proposal {out['proposal_id']}."


# -- one run --------------------------------------------------------------------------------------


async def manage(
    host: AgentHost,
    plan: Plan,
    *,
    why: str,
    trigger: Trigger,
    correlation_id: str,
    limit_usage: Sequence[limits.LimitUsage] = (),
) -> RunResult | None:
    """One model run for one position; the computed actions submitted and risk-routed."""
    pos = plan.position
    ctx = run_context(host, correlation_id)
    expected = guard.Expected(
        inst_id=pos.inst_id,
        side=pos.side,
        current_stop=pos.state.stop,
        mark=pos.state.mark_price,
        actions=plan.actions,
    )
    result: RunResult | None = None
    with guard.expecting(host.env, correlation_id, expected):
        reviewer = ActionReviewer(ctx, plan, expected)
        try:
            result = await host.run(
                SLUG,
                run_prompt(plan, why, ctx.now(), limit_usage),
                trigger=trigger,
                task=f"manage {pos.inst_id}",
                correlation_id=correlation_id,
                reviewer=reviewer,
                max_follow_ups=int(config()["output_retries"]),
            )
        except LookupError:  # AgentUnavailableError: disabled while waiting
            log.info("portfolio_manager: not available; computed actions are still submitted")
        # Rule compliance: whatever the model did, every computed action is proposed once.
        for action in expected.pending():
            model = action.to_position_action(
                [EvidenceRef(type="proposal", id=pos.plan.proposal_id)]
                if pos.plan and pos.plan.proposal_id
                else []
            )
            model = model.model_copy(update={"rule": guard.rule_label(action)})
            note = await submit(ctx, action.kind, model) + " (submitted by code: the rule is mandatory)"
            ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="text", content_md=note))
    from atlas_agents.pm.route import route

    for proposal_id in list(expected.submitted.values()):
        await route(ctx, proposal_id, risk_client())
    return result


# -- tick and triggers ----------------------------------------------------------------------------


def _load(
    host: AgentHost, inst_ids: set[str] | None = None
) -> tuple[list[inp.HeldPosition], list[limits.LimitUsage]]:
    ctx = run_context(host)
    with ctx.session_factory() as session:
        positions = inp.gather_all(session, ctx)
        usage = limits.usage(session, ctx)
    if inst_ids is not None:
        positions = [p for p in positions if p.inst_id in inst_ids]
    return positions, usage


def _limit_warnings(host: AgentHost) -> list[limits.LimitUsage]:
    ctx = run_context(host)
    with ctx.session_factory() as session:
        return limits.due_warnings(session, ctx, config())


async def tick(host: AgentHost, *, correlation_prefix: str = "pm") -> list[RunResult]:
    """The 5-minute check of every open position (see the module doc)."""
    cfg = config()
    ctx = run_context(host)
    now = ctx.now()
    for item in await asyncio.to_thread(_limit_warnings, host):
        if not host.replaying:
            ctx.publish(
                WARROOM_STREAM,
                message_envelope(ctx, kind="text", content_md=limits.warning_text(item), card=item.card()),
            )
    positions, usage = await asyncio.to_thread(_load, host)
    results: list[RunResult] = []
    interval = timedelta(minutes=int(cfg["status_post_interval_minutes"]))
    for position in positions:
        try:
            plan = plan_for(position, now)
        except Exception:
            log.exception("portfolio_manager: rules failed on %s", position.inst_id)
            continue
        key = (host.env, position.inst_id)
        event = _upcoming_event(position, now, cfg)
        event_key = (host.env, position.inst_id, event.id) if event is not None else None
        if plan.actions:
            why = "; ".join(f"{a.rule}: {a.reason}" for a in plan.actions)
            trigger: Trigger = "schedule"
            corr = f"{correlation_prefix}:{position.inst_id}:rule:{_stamp(now)}"
        elif event_key is not None and event_key not in _pre_event:
            _pre_event.add(event_key)
            minutes = int((event.scheduled_at - now).total_seconds() // 60)
            why = f"{event.name} (importance {event.importance}) is in {minutes} min"
            trigger = "alert"
            corr = f"{correlation_prefix}:{position.inst_id}:event:{event.id}"
        elif plan.advisories and (key not in _advised or now - _advised[key] >= interval):
            _advised[key] = now
            why = "; ".join(f"{a.rule}: {a.reason}" for a in plan.advisories)
            trigger = "schedule"
            corr = f"{correlation_prefix}:{position.inst_id}:advisory:{_stamp(now)}"
        else:
            continue  # the cheap path: no rule fired, no model call, no message
        async with _lock(host.env, position.inst_id):
            result = await manage(
                host, plan, why=why, trigger=trigger, correlation_id=corr, limit_usage=usage
            )
        if result is not None:
            results.append(result)
    return results


async def trigger(host: AgentHost, inst_ids: set[str] | None, why: str, *, source: str) -> list[RunResult]:
    """An immediate check of the held positions in ``inst_ids`` (None: every held position)."""
    positions, usage = await asyncio.to_thread(_load, host, inst_ids)
    results: list[RunResult] = []
    for position in positions:
        now = run_context(host).now()
        plan = plan_for(position, now)
        corr = f"pm:{position.inst_id}:{source}:{_stamp(now)}"
        async with _lock(host.env, position.inst_id):
            result = await manage(
                host, plan, why=why, trigger="alert", correlation_id=corr, limit_usage=usage
            )
        if result is not None:
            results.append(result)
    return results


def held(host: AgentHost) -> set[str]:
    ctx = run_context(host)
    with ctx.session_factory() as session:
        return inp.held_inst_ids(session, ctx)


__all__ = [
    "ActionReviewer",
    "Plan",
    "background",
    "held",
    "manage",
    "plan_for",
    "reset",
    "run_prompt",
    "set_risk_client",
    "settled",
    "submit",
    "tick",
    "trigger",
]
