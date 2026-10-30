"""ATL-101: the supervisor runs any registered agent, and agent-written mentions start runs.

* supervisor.py names no agent; agents register hooks in ``atlas_agents.agents.*``.
* A fake agent registered in this module gets its mention answered and its schedule hooks fired.
* An agent-authored ``warroom.mention`` starts the mentioned agent's run with the same
  thread_id/reply_to rules as an owner mention.
* A chain stops at ``agents.mentions.max_depth`` and the stop is logged.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from atlas_agents import mentions
from atlas_agents import supervisor as supervisor_module
from atlas_agents import testing as fake
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import (
    REGISTRY,
    AgentHost,
    AgentSpec,
    HookRegistry,
    ScheduleFire,
    discover_hooks,
)
from atlas_agents.session import RunResult
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext, fq_name
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import RunScope
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
NOW = datetime(2026, 10, 30, 4, 30, tzinfo=UTC)  # 07:30 Asia/Baghdad


def _owner_message(mention: str, text_md: str = "please look") -> EventEnvelope:
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
            "content_md": f"@{mention} {text_md}",
            "reply_to": None,
            "mentions": [mention],
        },
    )


def _agent_mention(
    author: str, target: str, *, depth: int | None = 1, correlation: str = "c-1"
) -> EventEnvelope:
    """The envelope the warroom.mention tool publishes (no thread_id/message_id in the payload)."""
    payload: dict[str, Any] = {
        "thread": {"kind": "main", "key": "main"},
        "author_type": "agent",
        "author": author,
        "kind": "text",
        "content_md": f"@{target} regime check on BTC-USDT-SWAP?",
        "card": None,
        "reply_to": None,
        "mentions": [target],
    }
    if depth is not None:
        payload["mention_depth"] = depth
    return EventEnvelope(
        type="warroom.message", env=ENV, source=f"agent:{author}", correlation_id=correlation, payload=payload
    )


# -- the supervisor names no agent -------------------------------------------------------------------


def test_supervisor_source_names_no_agent() -> None:
    source = Path(supervisor_module.__file__).read_text(encoding="utf-8").lower()
    for slug in REGISTRY:
        assert slug not in source, f"supervisor.py names the agent {slug}"


def test_the_historian_registers_itself_through_discovery() -> None:
    hooks = discover_hooks()
    historian = hooks["historian"]
    assert historian.on_mention is not None
    assert [hook.name for hook in historian.schedule] == ["historian_regime_table"]
    assert historian.schedule[0].time_of_day().isoformat() == "07:30:00"


def test_a_new_agent_module_is_discovered_without_editing_the_supervisor(tmp_path: Path) -> None:
    package = f"atl101_agents_{uuid.uuid4().hex[:8]}"
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "scout_like.py").write_text(
        "from atlas_agents.registry import HOOKS\n"
        "\n"
        f"@HOOKS.mention('{package}_agent')\n"
        "async def answer(host, request):\n"
        "    return None\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        hooks = discover_hooks(package)
    finally:
        sys.path.remove(str(tmp_path))
    assert hooks[f"{package}_agent"].on_mention is not None
    assert "historian" in hooks  # the real agents stay registered


# -- a fake agent registered in this module --------------------------------------------------------


@pytest.fixture
def fake_hooks(spec: AgentSpec) -> tuple[HookRegistry, list[Any]]:
    """A HookRegistry with a mention handler, a daily hook and an interval hook for ``spec``."""
    registry = HookRegistry()
    seen: list[Any] = []

    @registry.mention(spec.slug)
    async def answer(host: AgentHost, request: MentionRequest) -> RunResult:
        seen.append(request)
        reply_to, thread_id = resolve_parent(host.tool_context(spec.slug), request)
        return await host.run(
            spec.slug,
            f"answer: {request.content_md}",
            trigger="mention",
            correlation_id=request.correlation_id,
            thread_id=thread_id,
            reply_to=reply_to,
        )

    @registry.schedule(spec.slug, "fake_daily", at="07:30")
    async def daily(host: AgentHost, fire: ScheduleFire) -> RunResult:
        seen.append(fire)
        return await host.run(spec.slug, "daily job", trigger="schedule", correlation_id=fire.correlation_id)

    @registry.schedule(spec.slug, "fake_every", every_seconds=600)
    async def every(host: AgentHost, fire: ScheduleFire) -> RunResult:
        seen.append(fire)
        return await host.run(
            spec.slug, "interval job", trigger="schedule", correlation_id=fire.correlation_id
        )

    return registry, seen


def _ok() -> list[Any]:
    return [fake.assistant_text("ok"), fake.result(tokens_in=10, tokens_out=2)]


async def test_the_supervisor_answers_a_registered_fake_agents_mention(
    engine: Engine,
    spec: AgentSpec,
    recorder: Recorder,
    fake_hooks: tuple[HookRegistry, list[Any]],
    tmp_path: Path,
) -> None:
    registry, seen = fake_hooks
    client = fake.FakeClaudeClient([_ok()])
    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={spec.slug: spec},
        hooks=registry.hooks(),
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=tmp_path,
        clock=lambda: NOW,
    )
    owner = _owner_message(spec.slug)

    (result,) = await sup.handle_envelope(owner)

    assert result.status == "ok"
    (request,) = seen
    assert isinstance(request, MentionRequest) and request.depth == 0
    assert client.queries == [f"answer: @{spec.slug} please look"]
    with Session(engine) as db:
        run = db.get(AgentRun, result.run_id)
        assert run is not None and run.trigger == "mention"
    end = [e for e in recorder.of("warroom.thinking") if e.payload["delta_type"] == "run_end"]
    assert end and end[0].payload["thread_id"] == owner.payload["thread_id"]
    assert end[0].payload["reply_to"] == owner.payload["message_id"]


async def test_the_supervisor_fires_a_registered_fake_agents_schedule_hooks(
    engine: Engine,
    spec: AgentSpec,
    recorder: Recorder,
    fake_hooks: tuple[HookRegistry, list[Any]],
    tmp_path: Path,
) -> None:
    registry, seen = fake_hooks
    client = fake.FakeClaudeClient([_ok() for _ in range(4)])

    def supervisor(now: datetime) -> Supervisor:
        return Supervisor(
            env=ENV,
            engine=engine,
            registry={spec.slug: spec},
            hooks=registry.hooks(),
            client_factory=fake.factory(client),
            publisher=recorder,
            prompts_dir=tmp_path,
            clock=lambda: now,
        )

    early = supervisor(NOW - timedelta(minutes=1))
    (interval_only,) = await early.tick()  # 07:29: the daily hook is not due, the interval one is
    assert interval_only.status == "ok" and [f.name for f in seen] == ["fake_every"]

    sup = supervisor(NOW)
    results = await sup.tick()
    assert [r.status for r in results] == ["ok", "ok"]
    assert [f.name for f in seen[1:]] == ["fake_daily", "fake_every"]
    assert seen[1].correlation_id == "fake_daily:2026-10-30"

    assert await sup.tick(NOW + timedelta(minutes=5)) == []  # daily done today, interval not due
    (again,) = await sup.tick(NOW + timedelta(minutes=10))
    assert again.status == "ok" and seen[-1].name == "fake_every"
    with Session(engine) as db:
        runs = db.scalars(select(AgentRun.correlation_id).where(AgentRun.agent == spec.slug)).all()
    assert sorted(c or "" for c in runs).count("fake_daily:2026-10-30") == 1


# -- an agent-authored mention starts the Historian's run --------------------------------------------


def _wipe_historian(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == "historian", model.env == ENV))


@pytest.fixture
def stored_agent_mention(engine: Engine) -> Iterator[tuple[EventEnvelope, str, str]]:
    """An agent's @Historian mention as atlas:bridge stored it: (envelope, thread id, row id)."""
    _wipe_historian(engine)
    envelope = _agent_mention("market_scout", "historian")
    thread_id, row_id = str(uuid.uuid4()), str(uuid.uuid4())
    created = NOW - timedelta(minutes=1)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, 'demo', 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "key": f"atl101-{row_id[:8]}", "ts": created},
        )
        conn.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, created_at, updated_at) VALUES (:id, 'demo', :thread, :event, 'agent', "
                "'market_scout', 'text', :content, :ts, :ts)"
            ),
            {
                "id": row_id,
                "thread": thread_id,
                "event": str(envelope.id),
                "content": envelope.payload["content_md"],
                "ts": created,
            },
        )
    yield envelope, thread_id, row_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})
    _wipe_historian(engine)


def _historian_answer() -> list[Any]:
    card = {
        "type": "history_verdict",
        "question": "BTC regime",
        "inst_id": "BTC-USDT-SWAP",
        "bar": "1H",
        "period": None,
        "n": 0,
        "distribution": {},
        "base_rate": {},
        "regime": None,
        "stop_noise_check": None,
        "overfit_warning": False,
        "verdict": "does_not_support",
        "key_stat": "no data",
        "data_quality_notes": [],
        "data_as_of": "2026-10-30T04:00:00Z",
    }
    return [
        fake.assistant_text(f"No sample to judge.\n\n```json\n{json.dumps(card)}\n```"),
        fake.result(tokens_in=100, tokens_out=20),
    ]


async def test_an_agent_mention_starts_the_mentioned_agents_run_in_the_mentions_thread(
    engine: Engine, recorder: Recorder, stored_agent_mention: tuple[EventEnvelope, str, str], tmp_path: Path
) -> None:
    envelope, thread_id, row_id = stored_agent_mention
    client = fake.FakeClaudeClient([_historian_answer()])
    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={"historian": REGISTRY["historian"]},
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=tmp_path,
        clock=lambda: NOW,
    )

    (result,) = await sup.handle_envelope(envelope)

    assert result.status == "ok"
    assert len(client.queries) == 1 and "regime check on BTC-USDT-SWAP" in client.queries[0]
    (reply,) = recorder.of("warroom.message")
    # Same rule as an owner mention: the reply goes into the mention's thread, under the mention.
    assert reply.payload["thread_id"] == thread_id and reply.payload["reply_to"] == row_id
    assert reply.correlation_id == "c-1"
    for event in recorder.of("warroom.thinking"):
        assert event.payload["thread_id"] == thread_id and event.payload["reply_to"] == row_id
    with Session(engine) as db:
        run = db.get(AgentRun, result.run_id)
        assert run is not None and run.trigger == "mention" and run.correlation_id == "c-1"


async def test_an_agent_cannot_start_its_own_run_by_mentioning_itself(
    engine: Engine, recorder: Recorder, tmp_path: Path
) -> None:
    client = fake.FakeClaudeClient([])
    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={"historian": REGISTRY["historian"]},
        client_factory=fake.factory(client),
        publisher=recorder,
        prompts_dir=tmp_path,
    )
    assert await sup.handle_envelope(_agent_mention("historian", "historian")) == []
    assert client.queries == []


# -- chain depth ---------------------------------------------------------------------------------------


class _StubSession:
    """Stands in for an AgentSession: each run calls the real warroom.mention tool with the agent's
    ToolContext (as the model would), so the published envelope carries the run's depth."""

    def __init__(
        self, sup_ref: list[Supervisor], slug: str, next_target: str | None, log: list[Any], rec: Recorder
    ) -> None:
        self._sup_ref = sup_ref
        self._slug = slug
        self._next = next_target
        self._log = log
        self._rec = rec

    async def run(self, prompt: str, **kwargs: Any) -> RunResult:
        # The supervisor's context (same shared RunScope), publishing to the recorder, not Redis.
        ctx = dataclasses.replace(self._sup_ref[0].tool_context(self._slug), publish=self._rec)
        self._log.append((self._slug, ctx.run.mention_depth, ctx.run.correlation_id))
        if self._next is not None:
            handler = fw.make_handler(fw.get_tool(fq_name("warroom", "mention")), ctx)
            result = await handler({"agent": self._next, "request": "please check"})
            assert not result.get("is_error"), result
        return RunResult(run_id=uuid.uuid4(), status="ok")

    async def close(self) -> None:
        return None


def _chain_supervisor(
    engine: Engine, recorder: Recorder, tmp_path: Path, log: list[Any]
) -> tuple[Supervisor, HookRegistry]:
    """market_scout -> news_intelligence -> historian, each run mentioning the next."""
    chain = {"market_scout": "news_intelligence", "news_intelligence": "historian", "historian": None}
    registry = HookRegistry()
    for slug in chain:

        async def answer(host: AgentHost, request: MentionRequest, slug: str = slug) -> RunResult:
            return await host.run(
                slug, request.content_md, trigger="mention", correlation_id=request.correlation_id
            )

        registry.mention(slug)(answer)
    specs = {slug: dataclasses.replace(REGISTRY[slug], enabled=True) for slug in chain}
    sup_ref: list[Supervisor] = []

    def build(spec: AgentSpec) -> Any:
        return _StubSession(sup_ref, spec.slug, chain[spec.slug], log, recorder)

    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry=specs,
        hooks=registry.hooks(),
        session_builder=build,
        publisher=recorder,
        prompts_dir=tmp_path,
    )
    sup_ref.append(sup)
    return sup, registry


async def test_a_chain_stops_at_max_depth_and_logs_it(
    engine: Engine, recorder: Recorder, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert DEFAULTS["agents"]["mentions"]["max_depth"] == 2
    assert mentions.max_depth() == 2
    log: list[Any] = []
    sup, _registry = _chain_supervisor(engine, recorder, tmp_path, log)
    owner = _owner_message("market_scout")
    # Owner -> market_scout (depth 0): its run mentions news_intelligence.
    (first,) = await sup.handle_envelope(owner)
    assert first.status == "ok"
    (to_news,) = [e for e in _messages(recorder) if e.payload["mentions"] == ["news_intelligence"]]
    assert to_news.payload["mention_depth"] == 1

    # market_scout -> news_intelligence (depth 1 < 2): runs, and mentions the historian.
    (second,) = await sup.handle_envelope(to_news)
    assert second.status == "ok"
    (to_historian,) = [e for e in _messages(recorder) if e.payload["mentions"] == ["historian"]]
    assert to_historian.payload["mention_depth"] == 2

    # news_intelligence -> historian (depth 2 = max_depth): no run, and the stop is logged.
    with caplog.at_level(logging.WARNING, logger="atlas_agents.supervisor"):
        assert await sup.handle_envelope(to_historian) == []
    assert [entry[:2] for entry in log] == [("market_scout", 0), ("news_intelligence", 1)]
    stopped = [r for r in caplog.records if "mention chain stopped" in r.getMessage()]
    assert len(stopped) == 1
    assert (
        "depth 2" in stopped[0].getMessage() and "news_intelligence -> historian" in stopped[0].getMessage()
    )
    # Outside a run the scope is back to depth 0.
    assert sup.tool_context("market_scout").run.mention_depth == 0


def _messages(recorder: Recorder) -> list[EventEnvelope]:
    return recorder.of("warroom.message")


async def test_the_mention_tool_stamps_depth_and_the_runs_correlation_id() -> None:
    rec = Recorder()
    ctx = ToolContext(
        env=ENV,
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        agent="market_scout",
        clock=lambda: NOW,
        publish=rec,
        run=RunScope(correlation_id="watchlist:2026-10-30", mention_depth=0),
    )
    handler = fw.make_handler(fw.get_tool(fq_name("warroom", "mention")), ctx)
    result = await handler({"agent": "historian", "request": "regime table for the shortlist"})
    assert not result.get("is_error")
    (sent,) = rec.of("warroom.message")
    assert sent.payload["mention_depth"] == 1 and sent.correlation_id == "watchlist:2026-10-30"
    assert mentions.envelope_depth(sent) == 1


def test_mention_depth_rules() -> None:
    assert mentions.envelope_depth(_owner_message("historian")) == 0
    assert mentions.envelope_depth(_agent_mention("market_scout", "historian", depth=None)) == 1
    assert mentions.envelope_depth(_agent_mention("market_scout", "historian", depth=3)) == 3
    with pytest.raises(ValueError):
        mentions.max_depth({"agents": {"mentions": {"max_depth": 0}}})
