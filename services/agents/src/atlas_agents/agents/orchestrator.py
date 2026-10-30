"""The Orchestrator's hooks (brief §7.9/§8, ATL-160). Disabled by default (agents.roster): with the
Orchestrator off no analysis cycle runs.

* ``analysis_cycle`` (every ``agents.orchestrator.check_seconds``): starts the cycle of the slot due
  now (:func:`atlas_agents.orchestrator.cycle.due_slot`: every 30 min from 08:10 to 23:00 Baghdad).
  A slot runs once (``cycles`` is unique on env + slot_at), and never while a cycle is running.
* ``orchestrator.cycle_now`` on ``atlas.warroom`` (what ``/cycle now`` publishes, ATL-163): starts a
  ``manual`` cycle at once, unless one is running (then a system line says so).

The cycle itself is code (:mod:`atlas_agents.orchestrator.cycle`); these hooks return no RunResult.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Final

from atlas_shared.events import EventEnvelope

from atlas_agents.orchestrator import cycle
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

SLUG: Final = cycle.SLUG
SCHEDULE_JOB: Final = "analysis_cycle"
CYCLE_NOW_EVENT: Final = "orchestrator.cycle_now"


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


__all__ = ["CYCLE_NOW_EVENT", "SCHEDULE_JOB", "on_cycle_now", "on_schedule"]
