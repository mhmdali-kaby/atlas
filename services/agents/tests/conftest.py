"""Shared fixtures for the agent runtime tests (ATL-83).

DB tests need the checkout's test DB migrated: run `cd backend && php artisan test` first. Each test
uses its own agent slug, so nothing here touches another test's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import claude_agent_sdk
import pytest
from atlas_agents.registry import AgentSpec
from atlas_shared import testing
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, make_engine
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete


@dataclass
class Recorder:
    """A publisher that records instead of writing to Redis."""

    events: list[tuple[str, EventEnvelope]] = field(default_factory=list)

    def __call__(self, stream: str, envelope: EventEnvelope) -> None:
        self.events.append((stream, envelope))

    def of(self, type_: str) -> list[EventEnvelope]:
        return [e for _s, e in self.events if e.type == type_]


class RealClaudeClientConstructedError(AssertionError):
    """A test reached the real Claude CLI client (decision #245: no pytest calls the CLI)."""


@pytest.fixture(autouse=True)
def _no_real_claude_client(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every agents test fails if anything constructs claude_agent_sdk.ClaudeSDKClient, except a
    test marked ``stand_in_cli``, which points the client at a stand-in script, never at `claude`."""
    marker = request.node.get_closest_marker("stand_in_cli")
    if marker is not None:
        return

    def refuse(self: object, *args: object, **kwargs: object) -> None:
        raise RealClaudeClientConstructedError("a test constructed the real ClaudeSDKClient")

    monkeypatch.setattr(claude_agent_sdk.ClaudeSDKClient, "__init__", refuse)


@pytest.fixture(scope="session")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def slug(engine: Engine) -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:10]}"
    yield name
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == name))


@pytest.fixture
def spec(slug: str) -> AgentSpec:
    return AgentSpec(
        slug=slug,
        name="Test Agent",
        model="opus",
        routine_model="haiku",
        enabled=True,
        daily_tokens=10_000,
        daily_turns=100,
        max_turns_per_run=5,
    )


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()
