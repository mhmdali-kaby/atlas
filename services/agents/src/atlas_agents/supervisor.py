"""Holds one AgentSession per enabled agent and runs requests without ever crashing (decision #245).

It is also the long-running process's brain (``python -m atlas_agents``, ATL-93):

* Agents are not named here. Each module in ``atlas_agents.agents`` registers a mention handler
  and schedule hooks with ``atlas_agents.registry.HOOKS``; :func:`discover_hooks` finds them
  (ATL-101, decision #247).
* :meth:`Supervisor.handle_envelope` takes every ``atlas.warroom`` envelope; a ``warroom.message``
  that mentions an enabled agent with a mention handler starts that agent's run, whether the owner,
  the system or another agent wrote it. Chains stop at ``agents.mentions.max_depth`` (see
  :mod:`atlas_agents.mentions`). :meth:`Supervisor.consume_warroom` feeds it from Redis with XREAD.
* :meth:`Supervisor.tick` fires the schedule hooks. A daily hook (Asia/Baghdad wall clock) runs once
  per Baghdad day, at its time or at the first tick after it. "Done today" is read from
  ``agent_runs`` (trigger ``schedule``, correlation_id ``<job>:<YYYY-MM-DD>``), so a restart later
  the same day does not run a job again. An interval hook runs every ``every_seconds`` (tracked in
  memory, so also at the first tick). :meth:`Supervisor.serve` loops both.

Runs of one agent never overlap: each agent has its own lock.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import redis
from atlas_shared import bus
from atlas_shared.db import AgentRun
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from atlas_agents import access, events, mentions, prompts, streaming
from atlas_agents.registry import (
    REGISTRY,
    AgentHooks,
    AgentSpec,
    ScheduleFire,
    ScheduleHook,
    discover_hooks,
    enabled_agents,
)
from atlas_agents.session import AgentSession, ClientFactory, RunResult, Trigger, default_client_factory
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import RunScope

log = logging.getLogger(__name__)

SessionBuilder = Callable[[AgentSpec], AgentSession]
Clock = Callable[[], datetime]
WARROOM_STREAM: Final = "atlas.warroom"

# The mention depth of the run a mention handler starts (0 outside a handler).
_MENTION_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("atlas_mention_depth", default=0)


class AgentUnavailableError(LookupError):
    """The agent is unknown or not enabled in this supervisor."""


def owner_zone(defaults: Mapping[str, Any] = DEFAULTS) -> ZoneInfo:
    return ZoneInfo(str(defaults["timezone"]["display"]))


def schedule_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["schedule"]
    return config


def job_time(job: str, defaults: Mapping[str, Any] = DEFAULTS) -> time:
    """``agents.schedule.<job>`` ("HH:MM", Asia/Baghdad) as a time."""
    hours, minutes = str(schedule_config(defaults)[job]).split(":")
    return time(int(hours), int(minutes))


def job_correlation_id(job: str, day: date) -> str:
    return f"{job}:{day.isoformat()}"


class Supervisor:
    def __init__(
        self,
        *,
        env: str,
        engine: Engine,
        registry: Mapping[str, AgentSpec] = REGISTRY,
        client_factory: ClientFactory = default_client_factory,
        publisher: events.Publisher = events.bus_publisher,
        session_builder: SessionBuilder | None = None,
        replay_as_of: datetime | None = None,
        prompts_dir: Path | None = None,
        clock: Clock | None = None,
        hooks: Mapping[str, AgentHooks] | None = None,
    ) -> None:
        sessions = sessionmaker(bind=engine)
        enabled = enabled_agents(registry)
        # Every enabled agent's current prompt version is recorded once, at start (ATL-90).
        self.prompts = prompts.sync_versions(engine, [spec.slug for spec in enabled], prompts_dir=prompts_dir)
        self._engine = engine
        self._session_factory = sessions
        self._publish = publisher
        self._replay_as_of = replay_as_of
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        # Mention handlers and schedule hooks by slug (atlas_agents.agents.*, ATL-101).
        self.hooks: dict[str, AgentHooks] = dict(hooks if hooks is not None else discover_hooks())
        self._scopes: dict[str, RunScope] = {}
        self._last_fired: dict[tuple[str, str], datetime] = {}

        def build(spec: AgentSpec) -> AgentSession:
            # Tools come only from the §7.10 matrix (atlas_agents.access); replay bounds every query.
            ctx = self.tool_context(spec.slug)
            options = access.options_for(spec.slug, ctx)
            observe = None
            if replay_as_of is None:
                # Live runs stream warroom.thinking (partial messages give the deltas) and post their
                # final message; a replayed day (evals) never writes to the War Room.
                options = dataclasses.replace(options, include_partial_messages=True)
                observe = streaming.observer_factory(agent=spec.slug, env=env, publish=publisher)
            prompt = self.prompts.get(spec.slug)
            return AgentSession(
                spec,
                env=env,
                engine=engine,
                options=options,
                client_factory=client_factory,
                publisher=publisher,
                observer_factory=observe,
                prompt_version=prompt[1] if prompt else None,
                system_prompt=prompts.renderer(ctx, prompt[0]) if prompt else None,
            )

        make = session_builder or build
        self.env = env
        self.sessions: dict[str, AgentSession] = {spec.slug: make(spec) for spec in enabled}
        self._locks: dict[str, asyncio.Lock] = {slug: asyncio.Lock() for slug in self.sessions}

    @property
    def replaying(self) -> bool:
        return self._replay_as_of is not None

    def publish(self, stream: str, envelope: EventEnvelope) -> None:
        self._publish(stream, envelope)

    def tool_context(self, agent: str) -> ToolContext:
        return ToolContext(
            env=self.env,
            session_factory=self._session_factory,
            agent=agent,
            replay_as_of=self._replay_as_of,
            clock=self._clock,
            run=self._scope(agent),
        )

    def _scope(self, agent: str) -> RunScope:
        """The one RunScope every ToolContext of ``agent`` shares; :meth:`run` sets it."""
        return self._scopes.setdefault(agent, RunScope())

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        """Run ``prompt`` on ``agent``. Raises only AgentUnavailableError; every other failure is a
        RunResult with status ``error``. Started from a mention handler, the run takes the mention's
        depth, so a ``warroom.mention`` it writes is one deeper (see :mod:`atlas_agents.mentions`)."""
        session = self.sessions.get(agent)
        if session is None:
            raise AgentUnavailableError(agent)
        trigger: Trigger = kwargs.pop("trigger", "manual")
        scope = self._scope(agent)
        async with self._locks[agent]:
            scope.correlation_id = kwargs.get("correlation_id")
            scope.mention_depth = _MENTION_DEPTH.get()
            try:
                return await session.run(prompt, trigger=trigger, **kwargs)
            except Exception as exc:  # e.g. Postgres down: log it, keep the process alive
                log.exception("agent %s: run failed outside the session", agent)
                await session.close()
                return RunResult(run_id=uuid.uuid4(), status="error", error=f"{type(exc).__name__}: {exc}")
            finally:
                scope.correlation_id = None
                scope.mention_depth = 0

    # -- mentions ---------------------------------------------------------------------------------

    async def handle_envelope(self, envelope: EventEnvelope) -> list[RunResult]:
        """Start the run of every enabled agent with a mention handler that ``envelope`` mentions.
        A mention at depth ``agents.mentions.max_depth`` or deeper starts no run and is logged."""
        results: list[RunResult] = []
        limit = mentions.max_depth()
        for slug, hooks in self.hooks.items():
            if hooks.on_mention is None or slug not in self.sessions:
                continue
            request = mentions.mention_from_envelope(envelope, slug, self.env)
            if request is None:
                continue
            if request.depth >= limit:
                log.warning(
                    "agents: mention chain stopped at depth %d (max_depth %d): %s -> %s, event %s, corr %s",
                    request.depth,
                    limit,
                    request.author,
                    slug,
                    request.event_id,
                    request.correlation_id,
                )
                continue
            token = _MENTION_DEPTH.set(request.depth)
            try:
                result = await hooks.on_mention(self, request)
            finally:
                _MENTION_DEPTH.reset(token)
            if result is not None:
                results.append(result)
        return results

    async def consume_warroom(
        self,
        client: redis.Redis,
        *,
        stop: asyncio.Event,
        start_id: str = "$",
        block_ms: int | None = None,
    ) -> str:
        """Read ``atlas.warroom`` from ``start_id`` (default: only new entries) until ``stop`` is
        set, handing each envelope to :meth:`handle_envelope`. Returns the last entry id read."""
        config: Mapping[str, Any] = DEFAULTS["agents"]["mentions"]
        block = int(config["block_ms"]) if block_ms is None else block_ms
        last = start_id
        if last == "$":
            # Pin "$" to a concrete id so entries arriving between two reads are not skipped.
            latest: Any = await asyncio.to_thread(client.xrevrange, WARROOM_STREAM, "+", "-", 1)
            last = str(latest[0][0]) if latest else "0-0"
        while not stop.is_set():
            batches: Any = await asyncio.to_thread(
                client.xread, {WARROOM_STREAM: last}, count=int(config["batch"]), block=block
            )
            for _stream, entries in batches or []:
                for entry_id, fields in entries:
                    last = str(entry_id)
                    try:
                        envelope = EventEnvelope.model_validate_json(fields[bus.FIELD])
                    except Exception:
                        log.warning("agents: unreadable atlas.warroom entry %s", entry_id, exc_info=True)
                        continue
                    try:
                        await self.handle_envelope(envelope)
                    except Exception:  # one bad message must not stop the consumer
                        log.exception("agents: handling atlas.warroom entry %s failed", entry_id)
        return last

    # -- schedule ---------------------------------------------------------------------------------

    def _job_done(self, agent: str, job: str, day: date) -> bool:
        with Session(self._engine) as db:
            found = db.scalars(
                select(AgentRun.id)
                .where(
                    AgentRun.agent == agent,
                    AgentRun.env == self.env,
                    AgentRun.trigger == "schedule",
                    AgentRun.correlation_id == job_correlation_id(job, day),
                )
                .limit(1)
            ).first()
        return found is not None

    def due(self, job: str, now: datetime, at: time | None = None) -> date | None:
        """The Baghdad day daily ``job`` is due for at ``now``, or None (not yet time today). ``at``
        defaults to ``agents.schedule.<job>``."""
        local = now.astimezone(owner_zone())
        if local.time() < (at if at is not None else job_time(job)):
            return None
        return local.date()

    async def tick(self, now: datetime | None = None) -> list[RunResult]:
        """Fire every schedule hook of an enabled agent that is due."""
        at = now or self._clock()
        results: list[RunResult] = []
        for slug, hooks in self.hooks.items():
            if slug not in self.sessions:
                continue
            for hook in hooks.schedule:
                fire = await self._fire_if_due(slug, hook, at)
                if fire is None:
                    continue
                if not hook.daily:
                    self._last_fired[(slug, hook.name)] = at
                try:
                    result = await hook.run(self, fire)
                except Exception:  # one broken hook must not stop the others
                    log.exception("agents: schedule hook %s of %s failed", hook.name, slug)
                    continue
                if result is not None:
                    results.append(result)
        return results

    async def _fire_if_due(self, slug: str, hook: ScheduleHook, at: datetime) -> ScheduleFire | None:
        if hook.every_seconds is None:
            day = self.due(hook.name, at, hook.time_of_day())
            if day is None or await asyncio.to_thread(self._job_done, slug, hook.name, day):
                return None
            correlation_id = job_correlation_id(hook.name, day)
            return ScheduleFire(name=hook.name, at=at, day=day, correlation_id=correlation_id)
        last = self._last_fired.get((slug, hook.name))
        if last is not None and (at - last).total_seconds() < hook.every_seconds:
            return None
        stamp = at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        day = at.astimezone(owner_zone()).date()
        return ScheduleFire(name=hook.name, at=at, day=day, correlation_id=f"{hook.name}:{stamp}")

    async def serve(self, client: redis.Redis, *, stop: asyncio.Event) -> None:
        """The process loop: the mention consumer plus a schedule tick every tick_seconds."""
        interval = float(schedule_config()["tick_seconds"])

        async def schedule() -> None:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("agents: schedule tick failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except TimeoutError:
                    pass

        await asyncio.gather(self.consume_warroom(client, stop=stop), schedule())

    async def close(self) -> None:
        for session in self.sessions.values():
            await session.close()
