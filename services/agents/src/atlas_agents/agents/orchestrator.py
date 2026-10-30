"""The Orchestrator's hooks (brief §7.9/§8). Enabled by default since ATL-161 (agents.roster).

The cycle (ATL-160):

* ``analysis_cycle`` (every ``agents.orchestrator.check_seconds``): starts the cycle of the slot due
  now (:func:`atlas_agents.orchestrator.cycle.due_slot`: every 30 min from 08:10 to 23:00 Baghdad).
  A slot runs once (``cycles`` is unique on env + slot_at), and never while a cycle is running.
* ``orchestrator.cycle_now`` on ``atlas.warroom`` (what ``/cycle now`` publishes, ATL-163, and the
  ``orchestration.start_cycle`` tool): starts a ``manual`` cycle at once, unless one is running
  (then a system line says so).

The cycle itself is code (:mod:`atlas_agents.orchestrator.cycle`); these hooks return no RunResult.

The agent (ATL-161, brief §7.9):

* ``warroom.message.text`` from the owner with no @mention, or an owner @mention of the
  Orchestrator: classify, acknowledge and route (:mod:`atlas_agents.orchestrator.owner`). ``/why
  <id>`` gets the explained answer; every other slash command is left to its own handler. The
  model runs (routing, /why) happen in the background; :func:`settled` waits for them.
* Every agent reply (``warroom.message.text`` / ``research_report``) feeds the loop detector
  (:mod:`atlas_agents.orchestrator.loops`): more than ``loop_max_replies`` alternating replies
  between two agents get one summary and an early vote.
* ``where_we_are`` (every ``check_seconds``): the "Where we are" card at each due slot
  (:mod:`atlas_agents.orchestrator.status`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from typing import Any, Final

from atlas_shared.events import EventEnvelope

from atlas_agents.mentions import MentionRequest
from atlas_agents.orchestrator import classify, cycle, loops, owner, status, why
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

log = logging.getLogger(__name__)

SLUG: Final = cycle.SLUG
SCHEDULE_JOB: Final = "analysis_cycle"
STATUS_JOB: Final = "where_we_are"
CYCLE_NOW_EVENT: Final = "orchestrator.cycle_now"
TEXT_EVENT: Final = "warroom.message.text"
REPORT_EVENT: Final = "warroom.message.research_report"

_tasks: set[asyncio.Task[Any]] = set()


def _background(work: Coroutine[Any, Any, Any], what: str) -> None:
    async def go() -> None:
        try:
            await work
        except Exception:
            log.exception("orchestrator: %s failed", what)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def settled() -> None:
    """Wait for every background Orchestrator run (routing, /why) to finish."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


# -- the cycle (ATL-160) ------------------------------------------------------------------------------


async def _start_slot(host: AgentHost, fire: ScheduleFire) -> uuid.UUID | None:
    slot = cycle.due_slot(fire.at)
    if slot is None or host.replaying or cycle.running(host.env):
        return None
    runner = cycle.CycleRunner(host)
    exists = await asyncio.to_thread(runner.slot_taken, slot)
    if exists:
        return None
    return cycle.start(host, "scheduled", slot_at=slot, runner=runner)


@HOOKS.schedule(SLUG, SCHEDULE_JOB, every_seconds=int(cycle.config()["check_seconds"]))
async def on_schedule(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await _start_slot(host, fire)
    return None


@HOOKS.event(SLUG, CYCLE_NOW_EVENT)
async def on_cycle_now(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    if host.replaying:
        return None
    if cycle.running(host.env):
        runner = cycle.CycleRunner(host)
        runner.system_line(None, "busy", "A cycle is already running; /cycle now was not started.")
        return None
    cycle.start(host, "manual")
    return None


# -- the owner (ATL-161) ------------------------------------------------------------------------------


async def take_owner_message(host: AgentHost, message: owner.OwnerMessage) -> classify.Classification | None:
    """Acknowledge now; start the routing or /why run in the background. None for a slash command
    (``/why`` runs in the background; the others are not the Orchestrator's)."""
    if host.replaying:
        return None
    target = why.why_target(message.content)
    if target is not None:
        _background(why.answer_why(host, message, target), f"/why {target}")
        return None
    if message.content.lstrip().startswith("/"):
        return None  # /pause, /cycle now, ... have their own handlers (ATL-163)
    guess = await owner.handle_owner_message(host, message)
    if guess.kind not in classify.DECISIVE:
        _background(owner.route(host, message, guess), "routing an owner message")
    return guess


@HOOKS.event(SLUG, TEXT_EVENT)
async def on_text(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    if payload.get("author_type") == "human":
        loops.observe(envelope)  # an owner message restarts the thread's count
        if payload.get("mentions"):
            return None  # an @mention goes to the agents it names (the Orchestrator: on_mention)
        message = owner.OwnerMessage.from_envelope(envelope)
        if message is not None and message.env == host.env:
            await take_owner_message(host, message)
        return None
    hit = loops.observe(envelope)
    if hit is not None and not host.replaying:
        await loops.stop(host, hit)
    return None


@HOOKS.event(SLUG, REPORT_EVENT)
async def on_report(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    hit = loops.observe(envelope)
    if hit is not None and not host.replaying:
        await loops.stop(host, hit)
    return None


@HOOKS.mention(SLUG)
async def on_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    """An owner's @Orch message is handled like any owner message; agents' mentions of the
    Orchestrator start nothing (it assigns work; it is not asked for analysis)."""
    if request.author_type != "human":
        return None
    message = owner.OwnerMessage(
        event_id=request.event_id,
        env=request.env,
        content=request.content_md,
        ts=request.ts,
        row_id=request.row_id,
        thread_id=request.thread_id,
        correlation_id=request.correlation_id,
    )
    await take_owner_message(host, message)
    return None


@HOOKS.schedule(SLUG, STATUS_JOB, every_seconds=int(cycle.config()["check_seconds"]))
async def on_status(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await status.post_if_due(host, fire.at)
    return None


__all__ = [
    "CYCLE_NOW_EVENT",
    "REPORT_EVENT",
    "SCHEDULE_JOB",
    "STATUS_JOB",
    "TEXT_EVENT",
    "on_cycle_now",
    "on_mention",
    "on_report",
    "on_schedule",
    "on_status",
    "on_text",
    "settled",
    "take_owner_message",
]
