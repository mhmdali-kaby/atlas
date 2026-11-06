"""ATL-252, decision #265 (3): an agent runs on its pinned prompt_versions body.

``agents.<slug>.prompt_version`` = N in the env's settings makes the agent's system prompt come from
stored row N (preamble + role as stored), with templates and generated blocks filled as usual. null
means the current files. A pin to a missing row degrades only that agent and raises an alert.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import shutil
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from atlas_agents import prompts
from atlas_agents import testing as fake
from atlas_agents.registry import REGISTRY, AgentSpec
from atlas_agents.supervisor import AgentUnavailableError, Supervisor
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, PromptVersion
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
HISTORIAN = "historian"
PIN_KEY = f"agents.{HISTORIAN}.prompt_version"
V1_MARK = f"ROLE: Historian, stored version one {uuid.uuid4().hex}. Env {{{{ENV}}}} on {{{{DATE}}}}.\n"
V2_MARK = f"ROLE: Historian, edited file {uuid.uuid4().hex}. Answer with statistics.\n"


class SettingsRow:
    """The demo env's settings row, written as Laravel's SettingsService would (version bumps)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.version = 0
        self.data: dict[str, Any] = {}

    def save(self, **changes: Any) -> EventEnvelope:
        self.data.update(changes)
        self.version += 1
        with self._engine.begin() as conn:
            conn.execute(text("delete from settings where env = :e"), {"e": ENV})
            conn.execute(
                text(
                    "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "values (:id, :env, :v, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
                ),
                {"id": uuid.uuid4(), "env": ENV, "v": self.version, "data": json.dumps(self.data), "t": NOW},
            )
        return EventEnvelope(
            type="system.settings_changed",
            env=ENV,
            source="backend:settings",
            payload={"version": self.version, "changed_fields": sorted(changes), "changed_by": None},
        )


def _wipe(engine: Engine, *agents: str) -> None:
    with engine.begin() as conn:
        for agent in agents:
            for model in (AgentRun, AgentUsageDaily, AgentState):
                conn.execute(delete(model).where(model.agent == agent, model.env == ENV))
            conn.execute(delete(PromptVersion).where(PromptVersion.agent == agent))
        conn.execute(text("delete from settings where env = :e"), {"e": ENV})


@pytest.fixture
def historian(engine: Engine, spec: AgentSpec) -> Iterator[AgentSpec]:
    _wipe(engine, HISTORIAN, spec.slug)
    yield dataclasses.replace(REGISTRY[HISTORIAN], enabled=True)
    _wipe(engine, HISTORIAN, spec.slug)


@pytest.fixture
def settings_row(engine: Engine) -> SettingsRow:
    return SettingsRow(engine)


@pytest.fixture
def folder(tmp_path: Path, spec: AgentSpec) -> Path:
    """Prompt files where historian's role is V1 (recorded as stored version 1), then edited to V2."""
    shutil.copy(prompts.PROMPTS_DIR / prompts.PREAMBLE_FILE, tmp_path / prompts.PREAMBLE_FILE)
    (tmp_path / f"{spec.slug}.md").write_text("ROLE: Test agent. Answer briefly.\n")
    return tmp_path


def _record_v1_then_edit(engine: Engine, folder: Path) -> tuple[int, int]:
    (folder / f"{HISTORIAN}.md").write_text(V1_MARK)
    first = prompts.sync_versions(engine, [HISTORIAN], prompts_dir=folder)[HISTORIAN][1]
    (folder / f"{HISTORIAN}.md").write_text(V2_MARK)
    second = prompts.sync_versions(engine, [HISTORIAN], prompts_dir=folder)[HISTORIAN][1]
    return first, second


def _ok() -> list[Any]:
    return [fake.assistant_text("ok"), fake.result(tokens_in=10, tokens_out=2)]


def _supervisor(
    engine: Engine,
    registry: dict[str, AgentSpec],
    client: fake.FakeClaudeClient,
    recorder: Recorder,
    folder: Path,
) -> Supervisor:
    return Supervisor(
        env=ENV,
        engine=engine,
        registry=registry,
        hooks={},
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=folder,
        clock=_ticking(),
    )


def _ticking() -> Callable[[], datetime]:
    """A clock that moves one second per read, so runs order by created_at."""
    ticks = itertools.count()
    return lambda: NOW + timedelta(seconds=next(ticks))


def _system_prompt(client: fake.FakeClaudeClient) -> str:
    assert client.options is not None
    value = client.options.system_prompt
    assert isinstance(value, str)
    return value


def _last_run(engine: Engine, agent: str) -> AgentRun:
    with Session(engine) as db:
        return db.scalars(
            select(AgentRun)
            .where(AgentRun.agent == agent, AgentRun.env == ENV)
            .order_by(AgentRun.created_at.desc())
        ).first()  # type: ignore[return-value]


def _state(engine: Engine, agent: str) -> AgentState | None:
    with Session(engine) as db:
        return db.scalars(
            select(AgentState).where(AgentState.agent == agent, AgentState.env == ENV)
        ).one_or_none()


async def test_pinned_historian_runs_on_the_stored_body_not_the_changed_file(
    engine: Engine, historian: AgentSpec, settings_row: SettingsRow, recorder: Recorder, folder: Path
) -> None:
    v1, v2 = _record_v1_then_edit(engine, folder)
    assert (v1, v2) == (1, 2)
    settings_row.save(**{PIN_KEY: 1})
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, {HISTORIAN: historian}, client, recorder, folder)

    result = await sup.run(HISTORIAN, "hello")

    assert result.status == "ok"
    system_prompt = _system_prompt(client)
    assert V1_MARK.split(" Env ")[0] in system_prompt
    assert V2_MARK.strip() not in system_prompt
    assert system_prompt.startswith('You are one member of "Atlas"')
    # Criterion 4: every {{TEMPLATE}} of the stored body is filled, generated blocks included.
    assert "{{" not in system_prompt
    assert f"Env {ENV} on 2026-11-06." in system_prompt
    assert "## Current settings" in system_prompt and "## Your last 7 days scorecard" in system_prompt
    assert _last_run(engine, HISTORIAN).prompt_version == 1


async def test_clearing_the_pin_returns_to_the_file_version_on_settings_changed(
    engine: Engine, historian: AgentSpec, settings_row: SettingsRow, recorder: Recorder, folder: Path
) -> None:
    _v1, v2 = _record_v1_then_edit(engine, folder)
    settings_row.save(**{PIN_KEY: 1})
    client = fake.FakeClaudeClient([_ok(), _ok()])
    sup = _supervisor(engine, {HISTORIAN: historian}, client, recorder, folder)

    assert (await sup.run(HISTORIAN, "first")).status == "ok"
    assert _last_run(engine, HISTORIAN).prompt_version == 1
    assert V2_MARK.strip() not in _system_prompt(client)

    applied = await sup.handle_system_envelope(settings_row.save(**{PIN_KEY: None}))
    assert applied is True
    assert (await sup.run(HISTORIAN, "second")).status == "ok"

    assert _last_run(engine, HISTORIAN).prompt_version == v2
    assert V2_MARK.strip() in _system_prompt(client)
    assert client.connects == 2  # the session reconnected to take the file's prompt


async def test_pinning_on_settings_changed_switches_a_running_session(
    engine: Engine, historian: AgentSpec, settings_row: SettingsRow, recorder: Recorder, folder: Path
) -> None:
    _v1, v2 = _record_v1_then_edit(engine, folder)
    settings_row.save(**{PIN_KEY: None})
    client = fake.FakeClaudeClient([_ok(), _ok()])
    sup = _supervisor(engine, {HISTORIAN: historian}, client, recorder, folder)

    assert (await sup.run(HISTORIAN, "first")).status == "ok"
    assert _last_run(engine, HISTORIAN).prompt_version == v2

    await sup.handle_system_envelope(settings_row.save(**{PIN_KEY: 1}))
    assert (await sup.run(HISTORIAN, "second")).status == "ok"

    assert _last_run(engine, HISTORIAN).prompt_version == 1
    assert V2_MARK.strip() not in _system_prompt(client)


async def test_a_pin_to_a_missing_version_degrades_only_that_agent_and_alerts(
    engine: Engine,
    historian: AgentSpec,
    spec: AgentSpec,
    settings_row: SettingsRow,
    recorder: Recorder,
    folder: Path,
) -> None:
    _record_v1_then_edit(engine, folder)
    settings_row.save(**{PIN_KEY: 99})
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, {HISTORIAN: historian, spec.slug: spec}, client, recorder, folder)

    with pytest.raises(AgentUnavailableError):
        await sup.run(HISTORIAN, "hello")
    other = await sup.run(spec.slug, "hello")

    assert other.status == "ok"
    assert _last_run(engine, spec.slug) is not None
    hist_state = _state(engine, HISTORIAN)
    assert hist_state is not None and hist_state.state == "degraded"
    assert hist_state.last_error is not None and "version 99" in hist_state.last_error
    other_state = _state(engine, spec.slug)
    assert other_state is not None and other_state.state != "degraded"

    alerts = recorder.of("system.alert")
    assert len(alerts) == 1
    assert alerts[0].payload["agent"] == HISTORIAN and alerts[0].payload["prompt_version"] == 99
    room = [e for s, e in recorder.events if s == "atlas.warroom" and e.payload.get("kind") == "system_alert"]
    assert len(room) == 1 and "version 99" in room[0].payload["content_md"]

    # The same settings again raise no second alert; fixing the pin brings the agent back.
    await sup.refresh_settings(force=True)
    assert len(recorder.of("system.alert")) == 1
    client.add_run(_ok())
    await sup.handle_system_envelope(settings_row.save(**{PIN_KEY: 1}))
    assert (await sup.run(HISTORIAN, "again")).status == "ok"
    assert _last_run(engine, HISTORIAN).prompt_version == 1
    fixed = _state(engine, HISTORIAN)
    assert fixed is not None and fixed.state != "degraded"


async def test_a_pin_that_goes_missing_on_settings_changed_stops_the_live_session(
    engine: Engine, historian: AgentSpec, settings_row: SettingsRow, recorder: Recorder, folder: Path
) -> None:
    _record_v1_then_edit(engine, folder)
    settings_row.save(**{PIN_KEY: None})
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, {HISTORIAN: historian}, client, recorder, folder)
    assert (await sup.run(HISTORIAN, "first")).status == "ok"

    await sup.handle_system_envelope(settings_row.save(**{PIN_KEY: 42}))
    await sup.settled()

    with pytest.raises(AgentUnavailableError):
        await sup.run(HISTORIAN, "second")
    state = _state(engine, HISTORIAN)
    assert state is not None and state.state == "degraded"
    assert len(recorder.of("system.alert")) == 1


def test_bad_pin_values_are_ignored() -> None:
    from atlas_agents import agent_settings

    base = {HISTORIAN: REGISTRY[HISTORIAN]}
    for bad in (0, -1, "1", True, 1.5):
        assert agent_settings.merge_pins(base, {PIN_KEY: bad}) == {}
    assert agent_settings.merge_pins(base, {PIN_KEY: None}) == {}
    assert agent_settings.merge_pins(base, {PIN_KEY: 3}) == {HISTORIAN: 3}
