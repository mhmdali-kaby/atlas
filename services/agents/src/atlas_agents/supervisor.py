"""Holds one AgentSession per enabled agent and runs requests without ever crashing (decision #245).

The long-running process (``python -m atlas_agents``, mention consumer and schedule hooks) is built
on this in later tasks; here it is the one place that turns any failure into a logged result.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from atlas_agents import access, events, streaming
from atlas_agents.registry import REGISTRY, AgentSpec, enabled_agents
from atlas_agents.session import AgentSession, ClientFactory, RunResult, Trigger, default_client_factory
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

SessionBuilder = Callable[[AgentSpec], AgentSession]


class AgentUnavailableError(LookupError):
    """The agent is unknown or not enabled in this supervisor."""


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
    ) -> None:
        sessions = sessionmaker(bind=engine)

        def build(spec: AgentSpec) -> AgentSession:
            # Tools come only from the §7.10 matrix (atlas_agents.access); replay bounds every query.
            ctx = ToolContext(env=env, session_factory=sessions, agent=spec.slug, replay_as_of=replay_as_of)
            options = access.options_for(spec.slug, ctx)
            observe = None
            if replay_as_of is None:
                # Live runs stream warroom.thinking (partial messages give the deltas) and post their
                # final message; a replayed day (evals) never writes to the War Room.
                options = dataclasses.replace(options, include_partial_messages=True)
                observe = streaming.observer_factory(agent=spec.slug, env=env, publish=publisher)
            return AgentSession(
                spec,
                env=env,
                engine=engine,
                options=options,
                client_factory=client_factory,
                publisher=publisher,
                observer_factory=observe,
            )

        make = session_builder or build
        self.env = env
        self.sessions: dict[str, AgentSession] = {spec.slug: make(spec) for spec in enabled_agents(registry)}

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        """Run ``prompt`` on ``agent``. Raises only AgentUnavailableError; every other failure is a
        RunResult with status ``error``."""
        session = self.sessions.get(agent)
        if session is None:
            raise AgentUnavailableError(agent)
        trigger: Trigger = kwargs.pop("trigger", "manual")
        try:
            return await session.run(prompt, trigger=trigger, **kwargs)
        except Exception as exc:  # e.g. Postgres down: log it, keep the process alive
            log.exception("agent %s: run failed outside the session", agent)
            await session.close()
            return RunResult(run_id=uuid.uuid4(), status="error", error=f"{type(exc).__name__}: {exc}")

    async def close(self) -> None:
        for session in self.sessions.values():
            await session.close()
