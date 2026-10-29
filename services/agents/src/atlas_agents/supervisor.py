"""Holds one AgentSession per enabled agent and runs requests without ever crashing (decision #245).

The long-running process (``python -m atlas_agents``, mention consumer and schedule hooks) is built
on this in later tasks; here it is the one place that turns any failure into a logged result.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import Engine

from atlas_agents import events
from atlas_agents.registry import REGISTRY, AgentSpec, enabled_agents
from atlas_agents.session import AgentSession, ClientFactory, RunResult, Trigger, default_client_factory

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
    ) -> None:
        def build(spec: AgentSpec) -> AgentSession:
            return AgentSession(
                spec, env=env, engine=engine, client_factory=client_factory, publisher=publisher
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
