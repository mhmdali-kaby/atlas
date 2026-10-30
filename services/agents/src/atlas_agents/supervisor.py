"""Holds one AgentSession per enabled agent and runs requests without ever crashing (decision #245).

It is also the long-running process's brain (``python -m atlas_agents``, ATL-93):

* :meth:`Supervisor.handle_envelope` takes every ``atlas.warroom`` envelope; a ``warroom.message``
  that mentions an enabled agent with a mention handler (today: the Historian) starts that agent's
  run. :meth:`Supervisor.consume_warroom` feeds it from Redis with XREAD.
* :meth:`Supervisor.tick` runs the daily jobs (``agents.schedule``, Asia/Baghdad wall clock) once
  per Baghdad day, at the scheduled time or at the first tick after it. "Done today" is read from
  ``agent_runs`` (trigger ``schedule``, correlation_id ``<job>:<YYYY-MM-DD>``), so a restart later
  the same day does not run a job again. :meth:`Supervisor.serve` loops both.

Runs of one agent never overlap: each agent has its own lock.
"""

from __future__ import annotations

import asyncio
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

from atlas_agents import access, events, historian, prompts, streaming
from atlas_agents.registry import REGISTRY, AgentSpec, enabled_agents
from atlas_agents.session import AgentSession, ClientFactory, RunResult, Trigger, default_client_factory
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

SessionBuilder = Callable[[AgentSpec], AgentSession]
Clock = Callable[[], datetime]
WARROOM_STREAM: Final = "atlas.warroom"


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

    def tool_context(self, agent: str) -> ToolContext:
        return ToolContext(
            env=self.env,
            session_factory=self._session_factory,
            agent=agent,
            replay_as_of=self._replay_as_of,
        )

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        """Run ``prompt`` on ``agent``. Raises only AgentUnavailableError; every other failure is a
        RunResult with status ``error``."""
        session = self.sessions.get(agent)
        if session is None:
            raise AgentUnavailableError(agent)
        trigger: Trigger = kwargs.pop("trigger", "manual")
        async with self._locks[agent]:
            try:
                return await session.run(prompt, trigger=trigger, **kwargs)
            except Exception as exc:  # e.g. Postgres down: log it, keep the process alive
                log.exception("agent %s: run failed outside the session", agent)
                await session.close()
                return RunResult(run_id=uuid.uuid4(), status="error", error=f"{type(exc).__name__}: {exc}")

    # -- mentions ---------------------------------------------------------------------------------

    async def handle_envelope(self, envelope: EventEnvelope) -> list[RunResult]:
        """Start the run of every enabled agent with a mention handler that ``envelope`` mentions."""
        results: list[RunResult] = []
        if historian.SLUG in self.sessions:
            request = historian.mention_from_envelope(envelope, historian.SLUG, self.env)
            if request is not None:
                results.append(await self.answer_historian_mention(request))
        return results

    async def answer_historian_mention(self, request: historian.MentionRequest) -> RunResult:
        """One @Historian answer: a research_report reply whose card validates as HistoryVerdict."""
        reply_to, thread_id = await asyncio.to_thread(
            historian.resolve_parent, self.tool_context(historian.SLUG), request
        )
        reviewer = historian.VerdictReviewer(request, reply_to=reply_to, thread_id=thread_id)
        return await self.run(
            historian.SLUG,
            historian.mention_prompt(request),
            trigger="mention",
            task=f"answer {request.author}",
            correlation_id=request.correlation_id,
            reviewer=reviewer,
            max_follow_ups=historian.output_retries(),
        )

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

    def due(self, job: str, now: datetime) -> date | None:
        """The Baghdad day ``job`` is due for at ``now``, or None (not yet time today)."""
        local = now.astimezone(owner_zone())
        if local.time() < job_time(job):
            return None
        return local.date()

    async def tick(self, now: datetime | None = None) -> list[RunResult]:
        """Run every daily job that is due and not yet done today."""
        at = now or self._clock()
        results: list[RunResult] = []
        if historian.SLUG in self.sessions:
            day = self.due(historian.REGIME_JOB, at)
            if day is not None and not await asyncio.to_thread(
                self._job_done, historian.SLUG, historian.REGIME_JOB, day
            ):
                result = await self.post_regime_table(day)
                if result is not None:
                    results.append(result)
        return results

    async def post_regime_table(self, day: date) -> RunResult | None:
        """The 07:30 regime table: computed in code, commented by the Historian, posted as kind
        regime_table. If the run fails the table is still posted, without commentary."""
        table = await historian.build_regime_table(self.tool_context(historian.SLUG))
        if table is None:
            log.warning("historian: no instrument with 24h volume in %s; no regime table %s", self.env, day)
            return None
        result = await self.run(
            historian.SLUG,
            historian.regime_prompt(table),
            trigger="schedule",
            task="regime table",
            correlation_id=job_correlation_id(historian.REGIME_JOB, day),
            reviewer=historian.RegimeTableReviewer(table),
            max_follow_ups=0,
        )
        if result.status != "ok" and self._replay_as_of is None:
            payload = historian.message_payload(
                historian.SLUG, historian.fallback_regime_message(table, result.status), str(result.run_id)
            )
            envelope = events.envelope(
                type_="warroom.message",
                env=self.env,
                agent=historian.SLUG,
                payload=payload,
                correlation_id=job_correlation_id(historian.REGIME_JOB, day),
            )
            try:
                self._publish(WARROOM_STREAM, envelope)
            except Exception:
                log.warning("historian: could not publish the fallback regime table", exc_info=True)
        return result

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
