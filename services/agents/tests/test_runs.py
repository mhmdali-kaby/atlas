"""AgentSession runs against FakeClaudeClient: run records, usage, budgets and rate limits (ATL-83)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_agents import testing as fake
from atlas_agents.events import AGENT_BUDGET, AGENT_STATE, SYSTEM_STREAM
from atlas_agents.registry import AgentSpec
from atlas_agents.session import AgentSession
from atlas_agents.supervisor import AgentUnavailableError, Supervisor
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, add_usage, usage_day
from claude_agent_sdk import ResultError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from .conftest import Recorder


def _session(
    spec: AgentSpec, engine: Engine, client: fake.FakeClaudeClient, recorder: Recorder
) -> AgentSession:
    return AgentSession(
        spec,
        env="demo",
        engine=engine,
        client_factory=fake.factory(client),
        publisher=recorder,
        prompt_version=3,
    )


def _supervisor(
    spec: AgentSpec, engine: Engine, client: fake.FakeClaudeClient, recorder: Recorder
) -> Supervisor:
    return Supervisor(
        env="demo",
        engine=engine,
        registry={spec.slug: spec},
        client_factory=fake.factory(client),
        publisher=recorder,
    )


def _runs(engine: Engine, slug: str) -> list[AgentRun]:
    with Session(engine) as db:
        return list(db.scalars(select(AgentRun).where(AgentRun.agent == slug).order_by(AgentRun.started_at)))


def _usage(engine: Engine, slug: str) -> AgentUsageDaily | None:
    with Session(engine) as db:
        return db.scalars(
            select(AgentUsageDaily).where(
                AgentUsageDaily.agent == slug, AgentUsageDaily.day == usage_day(datetime.now(UTC))
            )
        ).one_or_none()


def _state(engine: Engine, slug: str) -> AgentState:
    with Session(engine) as db:
        return db.scalars(select(AgentState).where(AgentState.agent == slug)).one()


def _preload(engine: Engine, slug: str, *, tokens: int = 0, turns: int = 0) -> None:
    with Session(engine) as db:
        add_usage(db, agent=slug, env="demo", day=usage_day(datetime.now(UTC)), tokens_in=tokens, turns=turns)
        db.commit()


def _unix(delta: timedelta) -> int:
    return int((datetime.now(UTC) + delta).timestamp())


async def test_ok_run_writes_one_row_with_the_scripted_usage(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient(
        [
            [
                fake.tool_use("mcp__history__compute_stats", {"inst_id": "BTC-USDT-SWAP"}),
                fake.tool_result('{"as_of": "2026-10-29T08:00:00Z"}'),
                fake.assistant_text("Regime: trend up."),
                fake.result(
                    tokens_in=1000, cache_read=150, cache_write=50, tokens_out=300, turns=2, cost_usd=0.0125
                ),
            ]
        ]
    )
    result = await _session(spec, engine, client, recorder).run(
        "Regime table please", trigger="schedule", correlation_id="cycle-2026-10-29"
    )

    assert result.status == "ok"
    assert result.text == "Regime: trend up."
    assert client.queries == ["Regime table please"]
    [run] = _runs(engine, spec.slug)
    assert run.id == result.run_id
    assert run.status == "ok"
    assert (run.tokens_in, run.tokens_out, run.turns) == (1200, 300, 2)
    assert run.cost_usd_notional == Decimal("0.012500")
    assert run.prompt_version == 3
    assert run.model == "opus"
    assert run.trigger == "schedule"
    assert run.correlation_id == "cycle-2026-10-29"
    assert run.ended_at is not None and run.ended_at >= run.started_at
    assert run.error is None

    usage = _usage(engine, spec.slug)
    assert usage is not None
    assert (usage.tokens_in, usage.tokens_out, usage.turns, usage.runs) == (1200, 300, 2, 1)
    assert _state(engine, spec.slug).state == "idle"
    # thinking, then back to idle; every event is on atlas.system from agent:<slug>
    states = [e.payload["state"] for e in recorder.of(AGENT_STATE)]
    assert states == ["thinking", "idle"]
    assert all(s == SYSTEM_STREAM for s, _e in recorder.events)
    assert all(e.source == f"agent:{spec.slug}" for _s, e in recorder.events)


async def test_usage_accumulates_and_cost_is_the_session_delta(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient(
        [
            [fake.result(tokens_in=100, tokens_out=10, turns=1, cost_usd=0.01)],
            [fake.result(tokens_in=200, tokens_out=20, turns=3, cost_usd=0.03)],
        ]
    )
    session = _session(spec, engine, client, recorder)
    await session.run("one")
    second = await session.run("two")

    assert second.cost_usd_notional == Decimal("0.02")
    assert client.connects == 1  # one long-lived session
    usage = _usage(engine, spec.slug)
    assert usage is not None
    assert (usage.tokens_in, usage.tokens_out, usage.turns, usage.runs) == (300, 30, 4, 2)


@pytest.mark.parametrize(("tokens", "turns"), [(10_000, 0), (0, 100)])
async def test_at_budget_the_run_is_refused_without_calling_the_client(
    tokens: int, turns: int, spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    _preload(engine, spec.slug, tokens=tokens, turns=turns)
    client = fake.FakeClaudeClient([[fake.result(tokens_in=1)]])

    result = await _session(spec, engine, client, recorder).run("anything")

    assert result.status == "over_budget"
    assert client.connects == 0 and client.queries == []
    [run] = _runs(engine, spec.slug)
    assert run.status == "over_budget"
    assert run.error is not None and "over_budget" in run.error
    assert (run.tokens_in, run.tokens_out, run.turns) == (0, 0, 0)
    state = _state(engine, spec.slug)
    assert state.state == "degraded"
    assert state.last_error == run.error
    [event] = recorder.of(AGENT_STATE)
    assert event.payload["reason"] == "over_budget"


async def test_crossing_80_percent_publishes_one_budget_event(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    _preload(engine, spec.slug, tokens=7_000)
    client = fake.FakeClaudeClient(
        [
            [fake.result(tokens_in=1_000, tokens_out=500, turns=1)],  # 7000 -> 8500: crosses 8000
            [fake.result(tokens_in=100, tokens_out=0, turns=1)],  # 8500 -> 8600: already past
        ]
    )
    session = _session(spec, engine, client, recorder)
    await session.run("one")
    await session.run("two")

    [event] = recorder.of(AGENT_BUDGET)
    assert event.payload == {
        "agent": spec.slug,
        "day": usage_day(datetime.now(UTC)).isoformat(),
        "threshold_pct": "80",
        "crossed": ["tokens"],
        "tokens_used": 8_500,
        "tokens_budget": 10_000,
        "turns_used": 1,
        "turns_budget": 100,
    }


async def test_below_80_percent_publishes_no_budget_event(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.result(tokens_in=7_999, turns=1)]])
    await _session(spec, engine, client, recorder).run("one")
    assert recorder.of(AGENT_BUDGET) == []


async def test_rate_limit_event_marks_agent_rate_limited_and_next_request_runs(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    resets = _unix(timedelta(seconds=-1))  # window already over by the next request
    client = fake.FakeClaudeClient(
        [
            [
                fake.assistant_text("Looking"),
                fake.rate_limit_rejected(resets_at=resets),
                fake.rate_limit_result(),
            ],
            [fake.assistant_text("Back."), fake.result(tokens_in=10, tokens_out=5, turns=1)],
        ]
    )
    supervisor = _supervisor(spec, engine, client, recorder)

    first = await supervisor.run(spec.slug, "go")
    assert first.status == "rate_limited"
    assert _state(engine, spec.slug).state == "rate_limited"
    limited = [e for e in recorder.of(AGENT_STATE) if e.payload["state"] == "rate_limited"]
    assert len(limited) == 1
    assert limited[0].payload["resets_at"] == datetime.fromtimestamp(resets, UTC).isoformat()
    assert limited[0].payload["previous_state"] == "thinking"
    assert _runs(engine, spec.slug)[0].status == "rate_limited"

    second = await supervisor.run(spec.slug, "again")
    assert second.status == "ok"
    assert second.text == "Back."
    assert _state(engine, spec.slug).state == "idle"
    assert [r.status for r in _runs(engine, spec.slug)] == ["rate_limited", "ok"]


async def test_a_known_future_reset_refuses_runs_until_then(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.rate_limit_rejected(resets_at=_unix(timedelta(hours=1)))]])
    session = _session(spec, engine, client, recorder)
    await session.run("go")

    refused = await session.run("again")

    assert refused.status == "rate_limited"
    assert client.queries == ["go"]
    assert [r.status for r in _runs(engine, spec.slug)] == ["rate_limited", "rate_limited"]


async def test_sdk_result_error_429_is_a_rate_limit_and_reconnects_next_time(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    error = ResultError("API Error", data={"api_error_status": 429, "is_error": True})
    client = fake.FakeClaudeClient([[fake.assistant_text("..."), error], [fake.result(tokens_in=1, turns=1)]])
    supervisor = _supervisor(spec, engine, client, recorder)

    first = await supervisor.run(spec.slug, "go")
    second = await supervisor.run(spec.slug, "again")

    assert first.status == "rate_limited"
    assert second.status == "ok"
    assert (client.connects, client.disconnects) == (2, 1)


async def test_other_errors_are_recorded_and_degrade_the_agent(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[RuntimeError("CLI crashed")], [fake.result(tokens_in=1, turns=1)]])
    supervisor = _supervisor(spec, engine, client, recorder)

    first = await supervisor.run(spec.slug, "go")
    assert first.status == "error"
    assert first.error == "RuntimeError: CLI crashed"
    state = _state(engine, spec.slug)
    assert (state.state, state.last_error) == ("degraded", "RuntimeError: CLI crashed")

    assert (await supervisor.run(spec.slug, "again")).status == "ok"
    assert _state(engine, spec.slug).state == "idle"


async def test_error_result_message_is_an_error(spec: AgentSpec, engine: Engine, recorder: Recorder) -> None:
    client = fake.FakeClaudeClient([[fake.result(is_error=True, subtype="error_max_turns", turns=5)]])
    result = await _session(spec, engine, client, recorder).run("go")
    assert result.status == "error"
    assert result.error == "error_max_turns"
    assert result.turns == 5


async def test_routine_runs_switch_to_the_cheaper_model(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.result(turns=1)], [fake.result(turns=1)], [fake.result(turns=1)]])
    session = _session(spec, engine, client, recorder)
    await session.run("normal")
    await session.run("check", routine=True)
    await session.run("normal again")

    assert client.models == ["haiku", "opus"]
    assert [r.model for r in _runs(engine, spec.slug)] == ["opus", "haiku", "opus"]


async def test_session_options_default_to_an_isolated_agent(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.result(turns=1)]])
    await _session(spec, engine, client, recorder).run("go")
    options = client.options
    assert options is not None
    assert options.model == "opus"
    assert options.max_turns == spec.max_turns_per_run
    assert options.tools == []
    assert options.setting_sources == []
    assert options.skills == []


async def test_supervisor_only_holds_enabled_agents(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    supervisor = _supervisor(spec, engine, fake.FakeClaudeClient(), recorder)
    assert list(supervisor.sessions) == [spec.slug]
    with pytest.raises(AgentUnavailableError):
        await supervisor.run("strategist", "go")


async def test_supervisor_survives_a_database_failure(
    spec: AgentSpec, engine: Engine, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _supervisor(spec, engine, fake.FakeClaudeClient(), recorder)

    def broken(*_a: object, **_k: object) -> None:
        raise RuntimeError("database is down")

    monkeypatch.setattr("atlas_agents.budget.today_usage", broken)
    result = await supervisor.run(spec.slug, "go")
    assert result.status == "error"
    assert result.error == "RuntimeError: database is down"
