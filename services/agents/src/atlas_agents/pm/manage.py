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
import logging
from collections.abc import Coroutine, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from atlas_shared.proposals import EvidenceRef

from atlas_agents.orchestrator.trading_core import TradingCoreClient
from atlas_agents.pm import guard, limits
from atlas_agents.pm import inputs as inp
from atlas_agents.pm.review import ActionReviewer, run_prompt, submit
from atlas_agents.pm.route import route
from atlas_agents.pm.rules import PROPOSAL_KINDS, Evaluation, RuleAction, evaluate_full
from atlas_agents.registry import AgentHost
from atlas_agents.session import RunResult, Trigger
from atlas_agents.tools.framework import RunScope, ToolContext
from atlas_agents.tools.warroom import message_envelope

log = logging.getLogger(__name__)

SLUG: Final = "portfolio_manager"
WARROOM_STREAM: Final = "atlas.warroom"


def config() -> Mapping[str, Any]:
    return inp.config()


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- process state --------------------------------------------------------------------------------

_tasks: set[asyncio.Task[Any]] = set()
_locks: dict[tuple[str, str], asyncio.Lock] = {}
_advised: dict[tuple[str, str], datetime] = {}
"""(env, inst_id) -> last advisory-only run."""
_pre_event: dict[tuple[str, str, str], datetime] = {}
"""(env, inst_id, event id) already given its pre-event run -> the event's scheduled time."""
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


def _prune(env: str, held_ids: set[str], now: datetime) -> None:
    """Forget positions no longer held and events already past, so the memory stays bounded."""
    for gone in [k for k in _advised if k[0] == env and k[1] not in held_ids]:
        del _advised[gone]
    for seen, at in list(_pre_event.items()):
        if seen[0] == env and (seen[1] not in held_ids or at < now):
            del _pre_event[seen]


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
    _prune(host.env, {p.inst_id for p in positions}, now)
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
            _pre_event[event_key] = event.scheduled_at
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
