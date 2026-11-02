"""ATL-237 (decision #255 (4)): the agents supervisor follows system_state.active_env.

On ``system.env_changed`` it reloads env, settings and prompts: the next run's preamble says
``Environment: live`` and its tools read the live settings row. A run already in flight finishes in
its old env. With no system_state row, ATLAS_ENV decides at startup.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from atlas_agents import prompts
from atlas_agents import testing as fake
from atlas_agents.__main__ import startup_env
from atlas_agents.registry import AgentSpec
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.account import load_settings
from atlas_shared.active_env import ENV_CHANGED
from atlas_shared.db import AgentRun, PromptVersion
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
VERSIONS = {"demo": 3, "live": 7}


def set_active_env(engine: Engine, env: str | None) -> None:
    with engine.begin() as c:
        c.execute(text("DELETE FROM system_state"))
        if env is not None:
            c.execute(
                text(
                    "INSERT INTO system_state"
                    " (id, active_env, preflight_attestations, created_at, updated_at)"
                    " VALUES (1, :env, '{}'::jsonb, now(), now())"
                ),
                {"env": env},
            )


def _write_settings(engine: Engine) -> None:
    """One settings row per env, with different versions and limits."""
    with engine.begin() as conn:
        for env, version in VERSIONS.items():
            conn.execute(text("delete from settings where env = :e"), {"e": env})
            data = {"max_open_positions": 4 if env == "demo" else 2}
            conn.execute(
                text(
                    "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "values (:id, :env, :v, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
                ),
                {"id": uuid.uuid4(), "env": env, "v": version, "data": json.dumps(data), "t": NOW},
            )


@pytest.fixture
def world(engine: Engine, spec: AgentSpec) -> Iterator[Engine]:
    set_active_env(engine, None)
    _write_settings(engine)
    yield engine
    set_active_env(engine, None)
    with engine.begin() as conn:
        for env in VERSIONS:
            conn.execute(text("delete from settings where env = :e"), {"e": env})
        conn.execute(delete(PromptVersion).where(PromptVersion.agent == spec.slug))


def _prompts(tmp_path: Path, slug: str) -> Path:
    shutil.copy(prompts.PROMPTS_DIR / "_preamble.md", tmp_path / "_preamble.md")
    (tmp_path / f"{slug}.md").write_text("ROLE: Test agent for the env switch.\n")
    return tmp_path


def _ok() -> list[Any]:
    return [fake.assistant_text("ok"), fake.result(tokens_in=10, tokens_out=2)]


def _env_changed(frm: str, to: str) -> EventEnvelope:
    return EventEnvelope(
        type=ENV_CHANGED,
        env=to,
        source="api:environment",
        payload={"from": frm, "to": to, "at": NOW.isoformat()},
    )


def _supervisor(
    engine: Engine, spec: AgentSpec, client: fake.FakeClaudeClient, recorder: Recorder, folder: Path
) -> Supervisor:
    return Supervisor(
        env="demo",
        engine=engine,
        registry={spec.slug: spec},
        hooks={},
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=folder,
        clock=lambda: NOW,
    )


def _run_envs(engine: Engine, agent: str) -> list[str]:
    with Session(engine) as db:
        rows = db.scalars(select(AgentRun).where(AgentRun.agent == agent).order_by(AgentRun.started_at)).all()
        return [r.env for r in rows]


def _system_prompt(client: fake.FakeClaudeClient) -> str:
    prompt = client.options.system_prompt if client.options else None
    assert isinstance(prompt, str)
    return prompt


# --- AC3: the next run says "Environment: live" and its tools read the live settings row -----------


async def test_env_changed_reloads_env_settings_and_prompt_preamble(
    world: Engine, spec: AgentSpec, recorder: Recorder, tmp_path: Path
) -> None:
    engine = world
    client = fake.FakeClaudeClient([_ok(), _ok()])
    sup = _supervisor(engine, spec, client, recorder, _prompts(tmp_path, spec.slug))

    first = await sup.run(spec.slug, "before")
    assert first.status == "ok"
    assert "Environment: demo (demo|live)" in _system_prompt(client)
    with Session(engine) as db:
        assert load_settings(db, sup.tool_context(spec.slug))[0] == VERSIONS["demo"]

    set_active_env(engine, "live")
    assert await sup.handle_system_envelope(_env_changed("demo", "live")) is True
    await sup.settled()
    assert sup.env == "live" and sup.settings.version == VERSIONS["live"]

    second = await sup.run(spec.slug, "after")
    assert second.status == "ok"
    prompt = _system_prompt(client)
    assert "Environment: live (demo|live)" in prompt and "Environment: demo" not in prompt
    ctx = sup.tool_context(spec.slug)
    assert ctx.env == "live"
    with Session(engine) as db:
        version, values, _as_of = load_settings(db, ctx)
    assert version == VERSIONS["live"] and values.get("max_open_positions") == 2
    assert sup.sessions[spec.slug].env == "live"
    assert _run_envs(engine, spec.slug) == ["demo", "live"]
    assert client.disconnects >= 1  # the demo session was closed


async def test_a_run_in_flight_finishes_in_its_old_env(
    world: Engine, spec: AgentSpec, recorder: Recorder, tmp_path: Path
) -> None:
    engine = world
    gate = asyncio.Event()

    class SlowClient(fake.FakeClaudeClient):
        async def query(self, prompt: str, session_id: str = "default") -> None:
            await gate.wait()
            await super().query(prompt, session_id)

    client = SlowClient([_ok(), _ok()])
    sup = _supervisor(engine, spec, client, recorder, _prompts(tmp_path, spec.slug))
    running = asyncio.create_task(sup.run(spec.slug, "long job"))
    await asyncio.sleep(0.05)

    set_active_env(engine, "live")
    assert await sup.handle_system_envelope(_env_changed("demo", "live")) is True
    assert client.disconnects == 0  # the demo session stays open for its in-flight run
    gate.set()
    assert (await running).status == "ok"
    await sup.settled()
    assert client.disconnects == 1

    assert (await sup.run(spec.slug, "next")).status == "ok"
    assert _run_envs(engine, spec.slug) == ["demo", "live"]


async def test_no_row_or_the_same_env_changes_nothing(
    world: Engine, spec: AgentSpec, recorder: Recorder, tmp_path: Path
) -> None:
    client = fake.FakeClaudeClient([])
    sup = _supervisor(world, spec, client, recorder, _prompts(tmp_path, spec.slug))
    assert await sup.follow_stored_env() is False  # no row
    set_active_env(world, "demo")
    assert await sup.handle_system_envelope(_env_changed("live", "demo")) is False
    assert sup.env == "demo" and client.connects == 0


# --- AC4: with no system_state row, ATLAS_ENV decides ----------------------------------------------


def test_startup_env_falls_back_to_atlas_env_without_a_row(
    world: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_ENV", "live")
    assert startup_env(world, None) == "live"
    monkeypatch.setenv("ATLAS_ENV", "demo")
    assert startup_env(world, None) == "demo"


def test_startup_env_reads_the_stored_row(world: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "demo")
    set_active_env(world, "live")
    assert startup_env(world, None) == "live"
    assert startup_env(world, "demo") == "demo"  # an explicit --env still wins
