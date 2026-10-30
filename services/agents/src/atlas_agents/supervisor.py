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
* Any other envelope type on ``atlas.warroom`` goes only to the agents that registered that type
  with ``HOOKS.event`` (ATL-107: ``news.alert`` starts News Intelligence and nobody else). A
  ``warroom.message`` is also offered under ``warroom.message.<kind>`` (ATL-135: the Technical
  Analyst reacts to Market Scout's ``watchlist`` card).
* ``atlas.market`` envelopes (:meth:`Supervisor.consume_market`) go only to the agents that
  registered their type (ATL-135: ``candle.closed``); ticks and every other type are dropped.
  ``atlas.proposals`` envelopes (:meth:`Supervisor.consume_proposals`) are handled the same way
  (ATL-159: ``proposal.opened`` starts the Devil's Advocate).

Runs of one agent never overlap: each agent has its own lock.

The owner's agent settings (ATL-114) are applied by :mod:`atlas_agents.settings_applier`, on
``system.settings_changed`` (:meth:`Supervisor.consume_system`) and re-checked before every
mention, tick and run.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
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
from atlas_agents.agent_settings import AgentSettings
from atlas_agents.registry import (
    REGISTRY,
    AgentHooks,
    AgentSpec,
    ScheduleFire,
    ScheduleHook,
    discover_hooks,
)
from atlas_agents.session import AgentSession, ClientFactory, RunResult, Trigger, default_client_factory
from atlas_agents.settings_applier import SettingsApplier
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import RunScope

log = logging.getLogger(__name__)

SessionBuilder = Callable[[AgentSpec], AgentSession]
Clock = Callable[[], datetime]
WARROOM_STREAM: Final = "atlas.warroom"
MARKET_STREAM: Final = "atlas.market"
PROPOSALS_STREAM: Final = "atlas.proposals"
SYSTEM_STREAM: Final = events.SYSTEM_STREAM

# The mention depth of the run a mention handler starts (0 outside a handler).
_MENTION_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("atlas_mention_depth", default=0)


async def _consume_stream(
    client: redis.Redis,
    stream: str,
    handler: Callable[[EventEnvelope], Awaitable[object]],
    *,
    stop: asyncio.Event,
    start_id: str = "$",
    block_ms: int | None = None,
) -> str:
    """Read ``stream`` from ``start_id`` (default: only new entries) until ``stop`` is set, handing
    each envelope to ``handler``. An unreadable entry or a failing handler is logged and skipped.
    Returns the last entry id read."""
    config: Mapping[str, Any] = DEFAULTS["agents"]["mentions"]
    block = int(config["block_ms"]) if block_ms is None else block_ms
    last = start_id
    if last == "$":
        # Pin "$" to a concrete id so entries arriving between two reads are not skipped.
        latest: Any = await asyncio.to_thread(client.xrevrange, stream, "+", "-", 1)
        last = str(latest[0][0]) if latest else "0-0"
    while not stop.is_set():
        batches: Any = await asyncio.to_thread(
            client.xread, {stream: last}, count=int(config["batch"]), block=block
        )
        for _stream, entries in batches or []:
            for entry_id, fields in entries:
                last = str(entry_id)
                try:
                    envelope = EventEnvelope.model_validate_json(fields[bus.FIELD])
                except Exception:
                    log.warning("agents: unreadable %s entry %s", stream, entry_id, exc_info=True)
                    continue
                try:
                    await handler(envelope)
                except Exception:  # one bad event must not stop the consumer
                    log.exception("agents: handling %s entry %s failed", stream, entry_id)
    return last


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
        self.env = env
        self._engine = engine
        self._replay_as_of = replay_as_of
        self._session_factory = sessionmaker(bind=engine)
        self._publish = publisher
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        # The owner's settings merged over the default specs, and the sessions they enable (ATL-114).
        self._applier = SettingsApplier(
            env=env,
            engine=engine,
            base=registry,
            replaying=replay_as_of is not None,
            prompts_dir=prompts_dir,
            publish=publisher,
            clock=self._clock,
        )
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

        self._applier.start(session_builder or build)
        self._locks = self._applier.locks

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
            web_policy=self._applier.web_policy,
        )

    # -- settings (ATL-114, atlas_agents.settings_applier) ----------------------------------------

    @property
    def settings(self) -> AgentSettings:
        return self._applier.settings

    @property
    def sessions(self) -> dict[str, AgentSession]:
        return self._applier.sessions

    @property
    def prompts(self) -> dict[str, tuple[prompts.PromptSource, int]]:
        return self._applier.prompts

    def is_enabled(self, agent: str) -> bool:
        return self._applier.is_enabled(agent)

    async def refresh_settings(self, *, force: bool = False) -> bool:
        """Re-read the settings version and apply the row if it changed (or ``force``)."""
        return await self._applier.refresh(force=force)

    async def handle_system_envelope(self, envelope: EventEnvelope) -> bool:
        """Apply settings on this env's ``system.settings_changed``; ignore every other event."""
        return await self._applier.handle_system_envelope(envelope)

    async def settled(self) -> None:
        """Wait until every disabled agent's session has closed."""
        await self._applier.settled()

    async def consume_system(
        self,
        client: redis.Redis,
        *,
        stop: asyncio.Event,
        start_id: str = "$",
        block_ms: int | None = None,
    ) -> str:
        """Read ``atlas.system`` from ``start_id`` until ``stop`` is set, handing each envelope to
        :meth:`handle_system_envelope`. Returns the last entry id read."""
        return await _consume_stream(
            client,
            SYSTEM_STREAM,
            self.handle_system_envelope,
            stop=stop,
            start_id=start_id,
            block_ms=block_ms,
        )

    def _scope(self, agent: str) -> RunScope:
        """The one RunScope every ToolContext of ``agent`` shares; :meth:`run` sets it."""
        return self._scopes.setdefault(agent, RunScope())

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        """Run ``prompt`` on ``agent``. Raises only AgentUnavailableError; every other failure is a
        RunResult with status ``error``. Started from a mention handler, the run takes the mention's
        depth, so a ``warroom.mention`` it writes is one deeper (see :mod:`atlas_agents.mentions`)."""
        await self.refresh_settings()
        session = self.sessions.get(agent)
        if session is None:
            raise AgentUnavailableError(agent)
        trigger: Trigger = kwargs.pop("trigger", "manual")
        scope = self._scope(agent)
        async with self._locks[agent]:
            if self.sessions.get(agent) is not session:  # disabled while this run waited
                raise AgentUnavailableError(agent)
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
        await self.refresh_settings()
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
        if envelope.type != "warroom.message":
            results.extend(await self._dispatch_event(envelope))
        else:
            kind = envelope.payload.get("kind")
            if isinstance(kind, str) and kind:
                results.extend(await self._dispatch_event(envelope, key=f"warroom.message.{kind}"))
        return results

    async def _dispatch_event(self, envelope: EventEnvelope, *, key: str | None = None) -> list[RunResult]:
        """Hand an envelope of this env to every enabled agent that registered ``key`` (default the
        envelope's type) with ``HOOKS.event`` (ATL-107); no other agent sees it."""
        results: list[RunResult] = []
        if envelope.env != self.env:
            return results
        wanted = key or envelope.type
        for slug, hooks in self.hooks.items():
            handler = hooks.on_event.get(wanted)
            if handler is None or slug not in self.sessions:
                continue
            result = await handler(self, envelope)
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
        return await _consume_stream(
            client, WARROOM_STREAM, self.handle_envelope, stop=stop, start_id=start_id, block_ms=block_ms
        )

    async def handle_market_envelope(self, envelope: EventEnvelope) -> list[RunResult]:
        """An ``atlas.market`` envelope goes to the agents that registered its type; every other one
        (ticks, stale/fresh flags) is dropped."""
        if not any(envelope.type in hooks.on_event for hooks in self.hooks.values()):
            return []
        await self.refresh_settings()
        return await self._dispatch_event(envelope)

    async def consume_market(
        self,
        client: redis.Redis,
        *,
        stop: asyncio.Event,
        start_id: str = "$",
        block_ms: int | None = None,
    ) -> str:
        """Read ``atlas.market`` (only new entries by default) until ``stop`` is set, handing each
        envelope to :meth:`handle_market_envelope`. Returns the last entry id read."""
        return await _consume_stream(
            client,
            MARKET_STREAM,
            self.handle_market_envelope,
            stop=stop,
            start_id=start_id,
            block_ms=block_ms,
        )

    async def handle_proposals_envelope(self, envelope: EventEnvelope) -> list[RunResult]:
        """An ``atlas.proposals`` envelope goes to the agents that registered its type (ATL-159:
        ``proposal.opened`` starts the Devil's Advocate); every other one is dropped."""
        return await self.handle_market_envelope(envelope)

    async def consume_proposals(
        self,
        client: redis.Redis,
        *,
        stop: asyncio.Event,
        start_id: str = "$",
        block_ms: int | None = None,
    ) -> str:
        """Read ``atlas.proposals`` (only new entries by default) until ``stop`` is set, handing each
        envelope to :meth:`handle_proposals_envelope`. Returns the last entry id read."""
        return await _consume_stream(
            client,
            PROPOSALS_STREAM,
            self.handle_proposals_envelope,
            stop=stop,
            start_id=start_id,
            block_ms=block_ms,
        )

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
        await self.refresh_settings()
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

        await asyncio.gather(
            self.consume_warroom(client, stop=stop),
            self.consume_system(client, stop=stop),
            self.consume_market(client, stop=stop),
            self.consume_proposals(client, stop=stop),
            schedule(),
        )

    async def close(self) -> None:
        await self.settled()
        for session in self.sessions.values():
            await session.close()
