"""ATL-114: the supervisor applies the owner's agent settings without a restart.

* config/defaults.yaml specs merged with ``settings.data`` (flat dotted keys, ATL-113) at start and
  on every ``system.settings_changed``; the version is re-checked before every mention and run.
* Disabling an agent stops new runs and reports it ``disabled``; enabling starts a session.
* Lowering ``daily_turns`` below today's usage refuses the next run as over budget.
* ``agents.web.deny_domains`` from settings makes news.fetch_article refuse the host, no request.
* A model change applies at the next connect, never mid-run.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from atlas_agents import agent_settings
from atlas_agents import testing as fake
from atlas_agents.mentions import MentionRequest
from atlas_agents.registry import REGISTRY, AgentHost, AgentSpec, HookRegistry
from atlas_agents.session import RunResult
from atlas_agents.supervisor import AgentUnavailableError, Supervisor
from atlas_agents.tools import fq_name
from atlas_agents.tools import framework as fw
from atlas_agents.tools import news as news_tools
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, add_usage, usage_day
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
SCOUT = "market_scout"


# -- helpers ------------------------------------------------------------------------------------------


class SettingsRow:
    """The demo env's settings row, written as Laravel's SettingsService would (version bumps)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.version = 0
        self.data: dict[str, Any] = {}

    def save(self, **changes: Any) -> EventEnvelope:
        """Apply ``changes`` (dotted keys) and return the system.settings_changed envelope."""
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


@pytest.fixture
def settings_row(engine: Engine) -> Iterator[SettingsRow]:
    row = SettingsRow(engine)
    yield row
    with engine.begin() as conn:
        conn.execute(text("delete from settings where env = :e"), {"e": ENV})


@pytest.fixture
def scout(engine: Engine) -> Iterator[AgentSpec]:
    """Market Scout's real spec, enabled by default here so settings can turn it off."""
    _wipe(engine, SCOUT)
    yield dataclasses.replace(REGISTRY[SCOUT], enabled=True)
    _wipe(engine, SCOUT)


def _wipe(engine: Engine, agent: str) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == agent, model.env == ENV))


def _mention_hooks(slug: str) -> HookRegistry:
    hooks = HookRegistry()

    @hooks.mention(slug)
    async def answer(host: AgentHost, request: MentionRequest) -> RunResult:
        return await host.run(
            slug, request.content_md, trigger="mention", correlation_id=request.correlation_id
        )

    return hooks


def _owner_mention(slug: str, handle: str) -> EventEnvelope:
    return EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="human:owner",
        payload={
            "thread": {"kind": "main", "key": "main"},
            "thread_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "author_type": "human",
            "author": "Owner",
            "kind": "text",
            "content_md": f"@{handle} what do we watch today?",
            "reply_to": None,
            "mentions": [slug],
        },
    )


def _ok() -> list[Any]:
    return [fake.assistant_text("ok"), fake.result(tokens_in=10, tokens_out=2)]


def _supervisor(
    engine: Engine,
    spec: AgentSpec,
    client: fake.FakeClaudeClient,
    recorder: Recorder,
    tmp_path: Path,
    hooks: HookRegistry | None = None,
) -> Supervisor:
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={spec.slug: spec},
        hooks=(hooks or _mention_hooks(spec.slug)).hooks(),
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=tmp_path,
        clock=lambda: NOW,
    )


def _runs(engine: Engine, agent: str) -> list[AgentRun]:
    with Session(engine) as db:
        return list(db.scalars(select(AgentRun).where(AgentRun.agent == agent, AgentRun.env == ENV)).all())


# -- merging --------------------------------------------------------------------------------------------


def test_merge_applies_valid_values_and_ignores_bad_ones(spec: AgentSpec) -> None:
    data = {
        f"agents.{spec.slug}.enabled": False,
        f"agents.{spec.slug}.model": "routine",
        f"agents.{spec.slug}.daily_tokens": 500,
        f"agents.{spec.slug}.daily_turns": "7",  # a string: Laravel never stores one; ignored
        "agents.web.deny_domains": ["evil.test"],
        "agents.web.allow_domains": "coindesk.com",  # not a list; ignored
    }
    merged = agent_settings.merge({spec.slug: spec}, 4, data)
    got = merged.specs[spec.slug]
    assert (got.enabled, got.model, got.daily_tokens, got.daily_turns) == (
        False,
        "haiku",
        500,
        spec.daily_turns,
    )
    assert got.routine_model == spec.routine_model and got.max_turns_per_run == spec.max_turns_per_run
    assert merged.web == agent_settings.WebPolicy(allow_domains=(), deny_domains=("evil.test",))
    assert merged.version == 4


def test_missing_keys_keep_the_defaults(spec: AgentSpec) -> None:
    merged = agent_settings.merge({spec.slug: spec}, None, {"risk_profile": "balanced"})
    assert merged.specs[spec.slug] == spec
    assert merged.web == agent_settings.default_web_policy()


@pytest.mark.parametrize("value", [0, -1, 1_000_000_001, True, 1.5])
def test_out_of_bounds_budgets_are_ignored(spec: AgentSpec, value: Any) -> None:
    merged = agent_settings.merge({spec.slug: spec}, 1, {f"agents.{spec.slug}.daily_tokens": value})
    assert merged.specs[spec.slug].daily_tokens == spec.daily_tokens


# -- AC1: disabling Market Scout ----------------------------------------------------------------------


async def test_disabling_market_scout_stops_mentions_and_reports_it_disabled(
    engine: Engine, scout: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save()
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, scout, client, recorder, tmp_path)
    (first,) = await sup.handle_envelope(_owner_mention(SCOUT, "Scout"))
    assert first.status == "ok" and client.connects == 1

    changed = settings_row.save(**{"agents.market_scout.enabled": False})
    assert await sup.handle_system_envelope(changed) is True
    await sup.settled()

    assert await sup.handle_envelope(_owner_mention(SCOUT, "Scout")) == []
    assert len(_runs(engine, SCOUT)) == 1 and len(client.queries) == 1  # no new run
    assert client.disconnects == 1  # its session was closed
    assert sup.is_enabled(SCOUT) is False and SCOUT not in sup.sessions
    with pytest.raises(AgentUnavailableError):
        await sup.run(SCOUT, "direct call")
    with Session(engine) as db:
        state = db.scalars(select(AgentState).where(AgentState.agent == SCOUT, AgentState.env == ENV)).one()
        assert state.state == "disabled"
    (event,) = [e for e in recorder.of("system.agent_state") if e.payload["state"] == "disabled"]
    assert event.payload["agent"] == SCOUT and event.payload["reason"] == "settings_disabled"
    assert event.payload["previous_state"] == "idle"
    assert event.payload["settings_version"] == settings_row.version
    assert event.source == f"agent:{SCOUT}"

    # Enabling it again starts a fresh session.
    client.add_run(_ok())
    settings_row.save(**{"agents.market_scout.enabled": True})
    (again,) = await sup.handle_envelope(_owner_mention(SCOUT, "Scout"))  # version re-checked first
    assert again.status == "ok" and client.connects == 2
    assert any(e.payload["reason"] == "settings_enabled" for e in recorder.of("system.agent_state"))


async def test_disabling_waits_for_the_in_flight_run_before_closing(
    engine: Engine, scout: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save()
    gate = asyncio.Event()

    class SlowClient(fake.FakeClaudeClient):
        async def query(self, prompt: str, session_id: str = "default") -> None:
            await gate.wait()
            await super().query(prompt, session_id)

    client = SlowClient([_ok()])
    sup = _supervisor(engine, scout, client, recorder, tmp_path)
    running = asyncio.create_task(sup.run(SCOUT, "long job"))
    await asyncio.sleep(0.05)
    assert client.connects == 1

    await sup.handle_system_envelope(settings_row.save(**{"agents.market_scout.enabled": False}))
    await asyncio.sleep(0.05)
    assert client.disconnects == 0  # never closed under a running query

    gate.set()
    result = await running
    await sup.settled()
    assert result.status == "ok" and client.disconnects == 1


async def test_settings_are_read_at_start(
    engine: Engine, scout: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save(**{"agents.market_scout.enabled": False})
    sup = _supervisor(engine, scout, fake.FakeClaudeClient(), recorder, tmp_path)
    assert SCOUT not in sup.sessions and sup.settings.version == settings_row.version


async def test_other_envs_and_other_events_are_ignored(
    engine: Engine, scout: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save()
    sup = _supervisor(engine, scout, fake.FakeClaudeClient(), recorder, tmp_path)
    live = EventEnvelope(type="system.settings_changed", env="live", source="backend:settings", payload={})
    other = EventEnvelope(type="system.agent_state", env=ENV, source="agent:x", payload={})
    assert await sup.handle_system_envelope(live) is False
    assert await sup.handle_system_envelope(other) is False


# -- AC2: lowering daily_turns below today's usage ------------------------------------------------------


async def test_lowering_daily_turns_below_usage_stops_the_next_run_as_over_budget(
    engine: Engine, spec: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save()
    with Session(engine) as db:
        add_usage(
            db, agent=spec.slug, env=ENV, day=usage_day(NOW), tokens_in=100, tokens_out=0, turns=20, runs=4
        )
        db.commit()
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, spec, client, recorder, tmp_path)

    await sup.handle_system_envelope(settings_row.save(**{f"agents.{spec.slug}.daily_turns": 10}))
    result = await sup.run(spec.slug, "one more")

    assert result.status == "over_budget"
    assert result.error is not None and "turns" in result.error and "20/10 turns" in result.error
    assert client.queries == [] and client.connects == 0  # the CLI was never called
    with Session(engine) as db:
        state = db.scalars(
            select(AgentState).where(AgentState.agent == spec.slug, AgentState.env == ENV)
        ).one()
        assert state.state == "degraded"


async def test_a_budget_change_is_picked_up_without_the_event(
    engine: Engine, spec: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    """The version is re-checked before each run, so a missed settings_changed still applies."""
    settings_row.save()
    with Session(engine) as db:
        add_usage(
            db, agent=spec.slug, env=ENV, day=usage_day(NOW), tokens_in=0, tokens_out=0, turns=5, runs=1
        )
        db.commit()
    client = fake.FakeClaudeClient([_ok()])
    sup = _supervisor(engine, spec, client, recorder, tmp_path)
    settings_row.save(**{f"agents.{spec.slug}.daily_turns": 5})
    assert (await sup.run(spec.slug, "go")).status == "over_budget"


# -- AC3: deny_domains from settings ------------------------------------------------------------------


async def test_a_deny_domains_entry_in_settings_makes_fetch_article_refuse_the_host(
    engine: Engine,
    spec: AgentSpec,
    recorder: Recorder,
    settings_row: SettingsRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_row.save()
    fetched: list[str] = []
    redirect_checks: list[bool] = []

    async def recording_fetch(url: str) -> str | None:
        fetched.append(url)
        # What guarded_fetch's Extractor.host_check sees on a redirect hop inside the same call.
        redirect_checks.append(news_tools.domain_allowed("blocked-by-owner.example"))
        return "article text"

    monkeypatch.setattr(news_tools, "_fetcher", recording_fetch)
    sup = _supervisor(engine, spec, fake.FakeClaudeClient(), recorder, tmp_path)
    ctx = sup.tool_context(spec.slug)
    handler = fw.make_handler(fw.get_tool(fq_name("news", "fetch_article")), ctx)

    async def call(url: str) -> dict[str, Any]:
        result = await handler({"url": url})
        body: dict[str, Any] = json.loads(result["content"][0]["text"])
        return body

    url = f"https://news.blocked-by-owner.example/{uuid.uuid4().hex}"
    before = await call(url)
    assert "error" not in before and fetched == [url]
    assert redirect_checks == [True]

    await sup.handle_system_envelope(
        settings_row.save(**{"agents.web.deny_domains": ["blocked-by-owner.example"]})
    )
    denied = await call(url)

    assert denied["error"] == "domain_denied"
    assert fetched == [url]  # no request was made for the denied host
    assert news_tools.domain_allowed("blocked-by-owner.example") is True  # outside a call: defaults

    # A redirect hop to the denied host is refused too while the policy is in force.
    other = f"https://fine.example/{uuid.uuid4().hex}"
    assert "error" not in await call(other)
    assert redirect_checks[-1] is False


async def test_an_allow_list_in_settings_excludes_every_other_host(
    engine: Engine,
    spec: AgentSpec,
    recorder: Recorder,
    settings_row: SettingsRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_row.save(**{"agents.web.allow_domains": ["coindesk.com"]})

    async def must_not_fetch(url: str) -> str | None:
        raise AssertionError("fetched a host outside the allow list")

    monkeypatch.setattr(news_tools, "_fetcher", must_not_fetch)
    sup = _supervisor(engine, spec, fake.FakeClaudeClient(), recorder, tmp_path)
    handler = fw.make_handler(fw.get_tool(fq_name("news", "fetch_article")), sup.tool_context(spec.slug))
    result = await handler({"url": f"https://other.example/{uuid.uuid4().hex}"})
    assert json.loads(result["content"][0]["text"])["error"] == "domain_denied"


# -- AC4: a model change applies at the next connect ----------------------------------------------------


async def test_a_model_change_applies_at_the_next_connect_not_mid_run(
    engine: Engine, spec: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save()
    gate = asyncio.Event()
    connected_with: list[str | None] = []

    class SlowClient(fake.FakeClaudeClient):
        async def connect(self, prompt: str | None = None) -> None:
            await super().connect(prompt)
            connected_with.append(self.options.model if self.options else None)

        async def query(self, prompt: str, session_id: str = "default") -> None:
            await super().query(prompt, session_id)
            if prompt == "first":
                await gate.wait()

    client = SlowClient([_ok(), _ok(), _ok()])
    sup = _supervisor(engine, spec, client, recorder, tmp_path)
    running = asyncio.create_task(sup.run(spec.slug, "first"))
    await asyncio.sleep(0.05)

    await sup.handle_system_envelope(settings_row.save(**{f"agents.{spec.slug}.model": "routine"}))
    gate.set()
    first = await running

    # The in-flight run kept its model and its connection.
    assert first.status == "ok"
    assert connected_with == ["opus"] and client.disconnects == 0 and client.models == []
    with Session(engine) as db:
        run = db.get(AgentRun, first.run_id)
        assert run is not None and run.model == "opus"

    second = await sup.run(spec.slug, "second")
    assert second.status == "ok"
    assert client.disconnects == 1 and connected_with == ["opus", "haiku"]
    assert client.models == []  # a reconnect, not set_model on the old connection
    with Session(engine) as db:
        run = db.get(AgentRun, second.run_id)
        assert run is not None and run.model == "haiku"

    third = await sup.run(spec.slug, "third")
    assert third.status == "ok" and client.connects == 2  # stays connected on the new model


async def test_a_replayed_day_ignores_live_settings(
    engine: Engine, spec: AgentSpec, recorder: Recorder, settings_row: SettingsRow, tmp_path: Path
) -> None:
    settings_row.save(**{f"agents.{spec.slug}.enabled": False})
    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={spec.slug: spec},
        hooks={},
        client_factory=fake.factory(fake.FakeClaudeClient()),
        publisher=recorder,
        prompts_dir=tmp_path,
        replay_as_of=NOW - timedelta(days=1),
    )
    assert spec.slug in sup.sessions
    assert await sup.refresh_settings(force=True) is False
