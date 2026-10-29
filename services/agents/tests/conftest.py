"""Shared fixtures for the agent runtime tests (ATL-83).

DB tests need the checkout's test DB migrated: run `cd backend && php artisan test` first. Each test
uses its own agent slug, so nothing here touches another test's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

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
