"""The Claude plan usage limit: a stuck run ends, every agent pauses together, the owner is told,
and a probe every agents.claude_limit.probe_minutes resumes the team once Claude answers again."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from atlas_agents import testing as fake
from atlas_agents.claude_limit import CLAUDE_LIMIT, recover_interrupted_runs
from atlas_agents.registry import AgentSpec
from atlas_agents.session import AgentSession
from atlas_agents.supervisor import Supervisor
from atlas_shared.db import AgentRun, AgentState
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from .conftest import Recorder


def _supervisor(
    specs: list[AgentSpec],
    engine: Engine,
    client: fake.FakeClaudeClient,
    recorder: Recorder,
    clock: list[datetime],
) -> Supervisor:
    return Supervisor(
        env="demo",
        engine=engine,
        registry={s.slug: s for s in specs},
        client_factory=fake.factory(client),
        publisher=recorder,
        clock=lambda: clock[0],
        hooks={},
    )


def _runs(engine: Engine, slug: str) -> list[AgentRun]:
    with Session(engine) as db:
        return list(db.scalars(select(AgentRun).where(AgentRun.agent == slug).order_by(AgentRun.started_at)))


def _state(engine: Engine, slug: str) -> AgentState:
    with Session(engine) as db:
        return db.scalars(select(AgentState).where(AgentState.agent == slug)).one()


def _unix(at: datetime) -> int:
    return int(at.timestamp())


async def test_a_rejected_limit_ends_the_run_even_if_the_cli_keeps_the_stream_open(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    """The real CLI reports the limit and then sends nothing more: the run must not wait forever."""
    resets = datetime.now(UTC) + timedelta(hours=2)
    client = fake.FakeClaudeClient(
        [[fake.assistant_text("Looking"), fake.rate_limit_rejected(resets_at=_unix(resets)), fake.HANG]]
    )
    session = AgentSession(
        spec, env="demo", engine=engine, client_factory=fake.factory(client), publisher=recorder
    )

    result = await asyncio.wait_for(session.run("go"), timeout=5)

    assert result.status == "rate_limited"
    assert [r.status for r in _runs(engine, spec.slug)] == ["rate_limited"]
    assert _state(engine, spec.slug).state == "rate_limited"


async def test_a_run_with_no_answer_times_out_instead_of_thinking_forever(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.HANG]])
    session = AgentSession(
        spec,
        env="demo",
        engine=engine,
        client_factory=fake.factory(client),
        publisher=recorder,
        run_timeout_seconds=0.2,
    )

    result = await asyncio.wait_for(session.run("go"), timeout=5)

    assert result.status == "error"
    assert result.error is not None and result.error.startswith("timeout")
    run = _runs(engine, spec.slug)[0]
    assert run.status == "error" and run.ended_at is not None
    assert _state(engine, spec.slug).state == "degraded"


async def test_one_agent_hitting_the_limit_pauses_every_agent_and_the_owner_is_told_once(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    other = dataclasses.replace(spec, slug=f"{spec.slug}_b")
    clock = [datetime.now(UTC)]
    resets = clock[0] + timedelta(hours=3)
    client = fake.FakeClaudeClient([[fake.rate_limit_rejected(resets_at=_unix(resets))]])
    supervisor = _supervisor([spec, other], engine, client, recorder, clock)
    try:
        assert (await supervisor.run(spec.slug, "go")).status == "rate_limited"

        refused = await supervisor.run(other.slug, "go")

        assert refused.status == "rate_limited"
        assert client.queries == ["go"]  # the second agent never called Claude
        await supervisor.check_claude_limit()
        await supervisor.check_claude_limit()
        reached = [e for e in recorder.of(CLAUDE_LIMIT) if e.payload["state"] == "reached"]
        assert len(reached) == 1
        assert reached[0].payload["resets_at"] == resets.replace(microsecond=0).isoformat()
    finally:
        _cleanup(engine, other.slug)


async def test_a_probe_every_ten_minutes_resumes_the_team_when_claude_answers_again(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    clock = [datetime.now(UTC)]
    client = fake.FakeClaudeClient(
        [
            [fake.rate_limit_rejected(resets_at=None)],  # the run that hits the limit
            [fake.rate_limit_rejected(resets_at=None)],  # first probe: still limited
            [fake.assistant_text("OK"), fake.result(tokens_in=1, tokens_out=1, turns=1)],  # second probe
            [fake.assistant_text("Back."), fake.result(tokens_in=1, tokens_out=1, turns=1)],  # next real run
        ]
    )
    supervisor = _supervisor([spec], engine, client, recorder, clock)

    await supervisor.run(spec.slug, "go")
    await supervisor.check_claude_limit()  # announces; no probe before probe_minutes
    assert len(client.queries) == 1

    clock[0] += timedelta(minutes=10)
    await supervisor.check_claude_limit()  # probe 1: still limited, nothing new announced
    assert len(client.queries) == 2
    assert [e.payload["state"] for e in recorder.of(CLAUDE_LIMIT)] == ["reached"]

    clock[0] += timedelta(minutes=5)
    await supervisor.check_claude_limit()  # too soon for another probe
    assert len(client.queries) == 2

    clock[0] += timedelta(minutes=5)
    await supervisor.check_claude_limit()  # probe 2 answers
    assert [e.payload["state"] for e in recorder.of(CLAUDE_LIMIT)] == ["reached", "cleared"]
    assert _state(engine, spec.slug).state == "idle"

    result = await supervisor.run(spec.slug, "again")
    assert (result.status, result.text) == ("ok", "Back.")


async def test_a_timeout_probes_at_once_and_says_nothing_if_claude_is_fine(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    clock = [datetime.now(UTC)]
    client = fake.FakeClaudeClient(
        [[fake.HANG], [fake.assistant_text("OK"), fake.result(tokens_in=1, turns=1)]]
    )
    supervisor = _supervisor([spec], engine, client, recorder, clock)
    supervisor.sessions[spec.slug].run_timeout_seconds = 0.2

    await supervisor.run(spec.slug, "go")
    await supervisor.check_claude_limit()

    assert len(client.queries) == 2  # probed straight away
    assert recorder.of(CLAUDE_LIMIT) == []


def test_runs_left_running_by_a_stopped_process_are_closed_at_startup(
    spec: AgentSpec, engine: Engine
) -> None:
    started = datetime.now(UTC) - timedelta(hours=1)
    with Session(engine) as db:
        db.add(
            AgentRun(
                id=uuid.uuid4(),
                env="demo",
                agent=spec.slug,
                trigger="schedule",
                started_at=started,
                status="running",
                model="opus",
                created_at=started,
                updated_at=started,
            )
        )
        db.add(
            AgentState(
                agent=spec.slug,
                env="demo",
                state="thinking",
                current_task="today's watchlist",
                state_since=started,
                created_at=started,
                updated_at=started,
            )
        )
        db.commit()

    closed = recover_interrupted_runs(engine)

    assert closed >= 1
    run = _runs(engine, spec.slug)[0]
    assert run.status == "error" and run.ended_at is not None
    assert run.error is not None and "interrupted" in run.error
    state = _state(engine, spec.slug)
    assert (state.state, state.current_task) == ("idle", None)


def _cleanup(engine: Engine, slug: str) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentState):
            conn.execute(delete(model).where(model.agent == slug))


async def test_after_a_known_reset_time_the_next_run_tries_and_its_answer_resumes_the_team(
    spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    clock = [datetime.now(UTC)]
    resets = clock[0] + timedelta(minutes=30)
    client = fake.FakeClaudeClient(
        [
            [fake.rate_limit_rejected(resets_at=_unix(resets))],
            [fake.assistant_text("Back."), fake.result(tokens_in=1, turns=1)],
        ]
    )
    supervisor = _supervisor([spec], engine, client, recorder, clock)
    await supervisor.run(spec.slug, "go")
    await supervisor.check_claude_limit()

    clock[0] = resets + timedelta(seconds=1)
    assert (await supervisor.run(spec.slug, "again")).status == "ok"
    await supervisor.check_claude_limit()

    assert [e.payload["state"] for e in recorder.of(CLAUDE_LIMIT)] == ["reached", "cleared"]
    assert not supervisor.claude_limit.limited
