"""ATL-182: when a vote opens, the four analyst voters each get a vote run and cast their own vote.

Reuses ATL-160's simulated-clock world (``test_orchestrator_cycle``). Every voter session is a fake
SDK client: a vote run's scripted behaviour stands in for the model calling ``warroom.cast_vote``
(the real tool function, with the session's own ToolContext), so the tool's deadline and
one-vote-per-version rules are what decide whether a vote is stored. No test calls the CLI.
Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import prompts
from atlas_agents import testing as fake
from atlas_agents.orchestrator import cycle, voters
from atlas_agents.registry import REGISTRY
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.catalogue import CastVoteInput
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.warroom import cast_vote
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, PromptVersion, Proposal, Vote, owner_day
from claude_agent_sdk.types import Message
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from .test_orchestrator_cycle import (
    ENV,
    VOTERS,
    FakeTradingCore,
    SimClock,
    World,
    _host_research,
    _row,
    baghdad,
    world,  # noqa: F401  (the fixture)
)

RUN_VOTERS = ("technical_analyst", "derivatives_sentiment", "news_intelligence", "historian")
ALL_AGENTS = ("orchestrator", "strategist", *VOTERS)
ROLE_SLUGS = {
    "ROLE: Technical Analyst": "technical_analyst",
    "ROLE: Derivatives & Sentiment Analyst": "derivatives_sentiment",
    "ROLE: News Intelligence": "news_intelligence",
    "ROLE: Historian": "historian",
    "ROLE: Strategist": "strategist",
    "ROLE: Orchestrator": "orchestrator",
    "ROLE: Devil's Advocate": "devils_advocate",
}
PID = re.compile(r'proposal_id "([0-9a-f-]{36})"')

Behaviour = Callable[[str, str], Awaitable[None]]
"""``(agent, proposal_id) -> None``: what the fake model does in a vote run."""


@dataclass
class VoteRun:
    agent: str
    proposal_id: str
    prompt: str


@dataclass
class Team:
    world: World
    clock: SimClock
    sup: Supervisor | None = None
    behaviours: dict[str, Behaviour] = field(default_factory=dict)
    vote_runs: list[VoteRun] = field(default_factory=list)
    refusals: list[tuple[str, str]] = field(default_factory=list)

    def ctx(self, agent: str) -> Any:
        assert self.sup is not None
        return dataclasses.replace(self.sup.tool_context(agent), publish=self.world.rec)

    async def cast(
        self, agent: str, pid: str, vote: str = "take", confidence: float = 0.8, fix: str | None = None
    ) -> None:
        try:
            await cast_vote(
                self.ctx(agent),
                CastVoteInput(proposal_id=pid, vote=vote, confidence=confidence, reason="r 1.2 ATR", fix=fix),
            )
        except ToolError as exc:  # what the model would read back
            self.refusals.append((agent, exc.code))

    def factory(self) -> Any:
        team = self

        class Client(fake.FakeClaudeClient):
            def __init__(self, options: Any) -> None:
                super().__init__([], options)
                system = str(getattr(options, "system_prompt", "") or "")
                self.agent = next((slug for role, slug in ROLE_SLUGS.items() if role in system), "?")

            async def query(self, prompt: str, session_id: str = "default") -> None:
                self.queries.append(prompt)

            async def receive_response(self) -> AsyncIterator[Message]:
                prompt = self.queries[-1]
                found = PID.search(prompt)
                if found and "The vote is open on" in prompt:
                    team.vote_runs.append(VoteRun(self.agent, found.group(1), prompt))
                    behaviour = team.behaviours.get(self.agent)
                    if behaviour is not None:
                        await behaviour(self.agent, found.group(1))
                yield fake.assistant_text("Done.")
                yield fake.result(tokens_in=10, tokens_out=5, turns=1)

        return Client

    def supervisor(self, disabled: tuple[str, ...] = ()) -> Supervisor:
        registry = {
            slug: dataclasses.replace(REGISTRY[slug], enabled=slug not in disabled) for slug in ALL_AGENTS
        }
        self.sup = Supervisor(
            env=ENV,
            engine=self.world.engine,
            registry=registry,
            client_factory=self.factory(),
            publisher=self.world.rec,
            clock=self.clock.now,
        )
        return self.sup

    def runner(self, strategist: Any) -> cycle.CycleRunner:
        assert self.sup is not None
        tc = FakeTradingCore(self.world.engine)
        self.tc = tc
        fns = {
            "technical_analyst": _host_research("technical_analyst"),
            "derivatives_sentiment": _host_research("derivatives_sentiment"),
        }
        return cycle.CycleRunner(
            self.sup, clock=self.clock, research=fns, strategist=strategist, risk=tc.client()
        )

    def da_review(self, pid: uuid.UUID, rating: int = 2, vote: str = "take") -> None:
        """The Devil's Advocate's review card and vote, as ATL-159 stores them."""
        at = self.clock.now()
        with self.world.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                    "'devils_advocate', 'research_report', 'review', cast(:card as jsonb), :ts, :ts)"
                ),
                {
                    "id": uuid.uuid4(),
                    "env": ENV,
                    "thread": self.world.thread_id,
                    "event": uuid.uuid4(),
                    "card": json.dumps({"type": "da_review", "proposal_id": str(pid), "risk_rating": rating}),
                    "ts": at,
                },
            )
        with Session(self.world.engine) as s:
            s.add(
                Vote(
                    env=ENV,
                    proposal_id=pid,
                    agent="devils_advocate",
                    vote=vote,
                    confidence=Decimal("0.8"),
                    reason="Risk 2/5.",
                    created_at=at,
                    updated_at=at,
                )
            )
            s.commit()

    def research_card(self, agent: str, card: dict[str, Any]) -> None:
        at = self.clock.now()
        with self.world.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                    ":author, 'research_report', 'card', cast(:card as jsonb), :ts, :ts)"
                ),
                {
                    "id": uuid.uuid4(),
                    "env": ENV,
                    "thread": self.world.thread_id,
                    "event": uuid.uuid4(),
                    "author": agent,
                    "card": json.dumps(card),
                    "ts": at,
                },
            )

    def votes(self) -> list[Vote]:
        with Session(self.world.engine) as s:
            ids = select(Proposal.id).where(Proposal.inst_id == self.world.inst)
            return list(s.scalars(select(Vote).where(Vote.proposal_id.in_(ids))))

    def runs(self, agent: str) -> list[AgentRun]:
        """``agent``'s vote runs: research runs use its main model, vote runs the routine one."""
        with Session(self.world.engine) as s:
            return list(
                s.scalars(
                    select(AgentRun).where(
                        AgentRun.agent == agent,
                        AgentRun.env == ENV,
                        AgentRun.model == REGISTRY[agent].routine_model,
                    )
                )
            )

    def summary(self) -> dict[str, Any]:
        (card,) = [
            e.payload["card"]
            for e in self.world.rec.of("warroom.message")
            if e.payload["kind"] == "vote_summary"
        ]
        result: dict[str, Any] = card
        return result


def _wipe(engine: Engine) -> None:
    with engine.begin() as c:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            c.execute(delete(model).where(model.agent.in_(ALL_AGENTS), model.env == ENV))


@pytest.fixture
def team(world: World) -> Iterator[Team]:  # noqa: F811
    _wipe(world.engine)
    t = Team(world, SimClock(baghdad(world.day, 10, 10)))
    yield t
    with world.engine.begin() as c:
        c.execute(
            text("DELETE FROM warroom_messages WHERE author = ANY(:a) AND card->>'inst_id' = :i"),
            {"a": list(ALL_AGENTS), "i": world.inst},
        )
    _wipe(world.engine)


def _strategist(team: Team, made: list[uuid.UUID], *, da: bool = True) -> Any:
    async def run(host: Any, cycle_id: str) -> None:
        pid = team.world.propose(cycle_id, team.clock.now())
        if da:
            team.da_review(pid)
        made.append(pid)

    return run


async def _cycle(team: Team, strategist: Any) -> tuple[uuid.UUID, cycle.CycleRunner]:
    runner = team.runner(strategist)
    cid = runner.create("manual")
    assert cid is not None
    await runner.run(cid)
    return cid, runner


# -- AC1 + AC4: one vote run per enabled voter; votes stored on the version; a full cycle passes ---------


async def test_each_voter_gets_one_vote_run_and_four_takes_plus_the_da_pass_the_vote(team: Team) -> None:
    sup = team.supervisor()
    team.research_card("technical_analyst", {"type": "technical_setup", "inst_id": team.world.inst, "x": 1})
    team.research_card("news_intelligence", {"type": "news_brief", "inst_ids": [team.world.inst], "y": 2})
    for agent in RUN_VOTERS:
        team.behaviours[agent] = team.cast
    made: list[uuid.UUID] = []

    cid, _runner = await _cycle(team, _strategist(team, made))

    (pid,) = made
    assert sorted(r.agent for r in team.vote_runs) == sorted(RUN_VOTERS)  # exactly one each
    assert {r.proposal_id for r in team.vote_runs} == {str(pid)}
    votes = {v.agent: v for v in team.votes()}
    assert set(votes) == set(VOTERS)
    for agent in RUN_VOTERS:
        assert votes[agent].proposal_id == pid and votes[agent].vote == "take"
        assert votes[agent].confidence == Decimal("0.8")
        (run,) = team.runs(agent)
        assert run.model == REGISTRY[agent].routine_model  # the cheaper model, counted as a run
        assert run.trigger == "schedule"
    # each run saw its own newest card and the deadline
    by_agent = {r.agent: r.prompt for r in team.vote_runs}
    assert '"type": "technical_setup"' in by_agent["technical_analyst"]
    assert '"type": "news_brief"' in by_agent["news_intelligence"]
    assert "You have no history_verdict card" in by_agent["historian"]
    row = _row(team.world, cid)
    vote_closes = datetime.fromisoformat(row.deadlines["vote"])
    run_timeout = timedelta(seconds=float(voters.config()["voter_run_timeout_seconds"]))
    for prompt in by_agent.values():
        found = re.search(r"Vote deadline: (\S+) ", prompt)
        assert found is not None
        told = datetime.fromisoformat(found.group(1))
        assert told == min(vote_closes, datetime.fromisoformat(row.transitions["voting"]) + run_timeout)
    # the vote passed and the Risk Engine was called exactly once
    assert team.summary()["result"] == "passed"
    assert team.tc.paths == [f"/internal/risk/evaluate/{pid}"]
    assert _row(team.world, cid).outcome == "awaiting_owner"
    assert team.world.proposal(pid).status == "awaiting_owner"
    await sup.close()


# -- AC2: disabled / degraded / over-budget voters get no run and count as skip 0.5 --------------------


async def test_unavailable_voters_get_no_run_and_count_as_skip_at_half_confidence(team: Team) -> None:
    sup = team.supervisor(disabled=("technical_analyst",))
    with Session(team.world.engine) as s:
        s.add(
            AgentUsageDaily(
                agent="news_intelligence",
                env=ENV,
                day=owner_day(team.clock.now()),
                tokens_in=REGISTRY["news_intelligence"].daily_tokens,
                tokens_out=0,
                turns=1,
                runs=1,
            )
        )
        s.commit()
    team.behaviours["historian"] = team.cast
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        # after research (a good research run would set the agent idle again)
        team.world.set_state("derivatives_sentiment", "degraded", "invalid card twice")
        pid = team.world.propose(cycle_id, team.clock.now())
        team.da_review(pid)
        made.append(pid)

    runner = team.runner(strategist)
    runner.cfg = {**runner.cfg, "blocking_agents": ["strategist"]}
    cid = runner.create("manual")
    assert cid is not None
    await runner.run(cid)

    assert [r.agent for r in team.vote_runs] == ["historian"]
    assert team.runs("technical_analyst") == []
    assert team.runs("derivatives_sentiment") == []
    assert team.runs("news_intelligence") == []
    lines = {line["agent"]: line for line in team.summary()["voters"]}
    for agent in ("technical_analyst", "derivatives_sentiment", "news_intelligence"):
        assert lines[agent]["missing"] is True and lines[agent]["vote"] == "skip"
        assert Decimal(lines[agent]["confidence"]) == Decimal("0.5")
    assert lines["historian"]["missing"] is False
    asked = [
        e
        for e in team.world.rec.of("warroom.message")
        if (e.payload["card"] or {}).get("type") == "vote_runs"
    ]
    assert len(asked) == 1 and "not asked" in asked[0].payload["content_md"]
    assert "over its daily tokens budget" in asked[0].payload["content_md"]
    await sup.close()


# -- AC3: a late answer or no cast_vote records nothing, and the vote still closes on time -------------


async def test_a_late_vote_or_no_vote_stores_nothing_and_the_vote_closes_on_time(team: Team) -> None:
    sup = team.supervisor()
    never = asyncio.Event()

    async def late(agent: str, pid: str) -> None:
        team.clock.t += timedelta(seconds=float(voters.config()["voter_run_timeout_seconds"]) + 1)
        await team.cast(agent, pid)

    async def hangs(agent: str, pid: str) -> None:
        await never.wait()
        await team.cast(agent, pid)

    async def silent(agent: str, pid: str) -> None:
        return None

    team.behaviours.update(
        technical_analyst=late, derivatives_sentiment=silent, news_intelligence=hangs, historian=team.cast
    )
    made: list[uuid.UUID] = []

    cid, runner = await _cycle(team, _strategist(team, made))

    row = _row(team.world, cid)
    voted_at = datetime.fromisoformat(row.transitions["voting"])
    closed_at = datetime.fromisoformat(row.transitions["vote_closed"])
    assert closed_at - voted_at <= timedelta(seconds=float(cycle.config()["vote_seconds"]) + 1)
    assert ("technical_analyst", "vote_deadline_passed") in team.refusals
    assert {v.agent for v in team.votes()} == {"historian", "devils_advocate"}
    missing = {t["agent"] for t in row.timeouts if t["step"] == "vote"}
    assert missing == {"technical_analyst", "derivatives_sentiment", "news_intelligence"}
    # the hung run finishes after the close: its vote is refused too
    never.set()
    await asyncio.gather(*runner.late, return_exceptions=True)
    assert {v.agent for v in team.votes()} == {"historian", "devils_advocate"}
    assert ("news_intelligence", "vote_deadline_passed") in team.refusals or (
        "news_intelligence",
        "proposal_not_open",
    ) in team.refusals
    await sup.close()


async def test_cast_vote_after_the_cycles_vote_deadline_is_refused(team: Team) -> None:
    team.supervisor()
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = team.world.propose(cycle_id, team.clock.now())
        made.append(pid)
        with team.world.engine.begin() as c:
            c.execute(
                text("UPDATE cycles SET deadlines = cast(:d as jsonb) WHERE id = :id"),
                {"d": json.dumps({"vote": "2000-01-01T00:00:00Z"}), "id": cycle_id},
            )

    runner = team.runner(strategist)
    cid = runner.create("manual")
    assert cid is not None
    await runner.strategist_step(cid)
    await team.cast("historian", str(made[0]))
    assert team.refusals == [("historian", "vote_deadline_passed")]
    assert team.votes() == []
    assert team.sup is not None
    await team.sup.close()


# -- AC5: a revision gets fresh vote runs; prompt versions are bumped -----------------------------------


async def test_a_revised_proposal_gets_a_fresh_vote_run_per_voter(team: Team) -> None:
    sup = team.supervisor()
    revised: list[uuid.UUID] = []

    def revise(parent: uuid.UUID) -> uuid.UUID:
        child = uuid.uuid4()
        at = team.clock.now()
        with Session(team.world.engine) as s:
            old = s.get(Proposal, parent)
            assert old is not None
            s.add(
                Proposal(
                    id=child,
                    env=ENV,
                    cycle_id=old.cycle_id,
                    parent_id=parent,
                    version=2,
                    kind="entry",
                    author="strategist",
                    inst_id=old.inst_id,
                    side="buy",
                    order_type="limit",
                    entry=Decimal(100),
                    stop=Decimal(95),
                    take_profits=[{"price": "110", "pct": "100"}],
                    size_suggestion_pct=Decimal(2),
                    leverage_suggestion=2,
                    expected_r=Decimal("2.0"),
                    probability=Decimal("0.55"),
                    status="under_review",
                    created_at=at,
                    updated_at=at,
                )
            )
            old.status = "cancelled"
            s.commit()
        return child

    async def first_votes_change_then_revised(agent: str, pid: str) -> None:
        if not revised:
            await team.cast(agent, pid, vote="change", fix="stop at 95")
            revised.append(revise(uuid.UUID(pid)))
            return
        await team.cast(agent, pid)

    team.behaviours["technical_analyst"] = first_votes_change_then_revised
    for agent in RUN_VOTERS[1:]:
        team.behaviours[agent] = team.cast
    made: list[uuid.UUID] = []

    cid, _runner = await _cycle(team, _strategist(team, made))

    (v1,) = made
    (v2,) = revised
    on_v2 = sorted(r.agent for r in team.vote_runs if r.proposal_id == str(v2))
    assert on_v2 == sorted(RUN_VOTERS)  # one fresh run per voter on the new version
    assert sum(1 for r in team.vote_runs if r.agent == "technical_analyst" and r.proposal_id == str(v1)) == 1
    stored = {(v.agent, v.proposal_id) for v in team.votes() if v.proposal_id == v2}
    assert stored == {(a, v2) for a in RUN_VOTERS}
    assert team.world.proposal(v2).status != "under_review"  # the vote closed on the new version
    assert team.summary()["proposal_id"] == str(v2)
    assert _row(team.world, cid).state == "cycle_closed"
    await sup.close()


BEFORE_ATL_182 = {
    "technical_analyst": "a274476f91de",
    "derivatives_sentiment": "390f01630b90",
    "news_intelligence": "b3b6bd281d44",
    "historian": "526247d3fcbc",
}


def test_each_voter_prompt_has_a_voting_section_and_a_new_version(engine: Engine) -> None:
    for slug, old in BEFORE_ATL_182.items():
        src = prompts.load_source(slug)
        assert "\nVOTING (when a vote opens on a proposal):\n" in src.body
        assert "warroom.cast_vote" in src.body
        assert src.version != old, slug
    synced = prompts.sync_versions(engine, list(BEFORE_ATL_182))
    with Session(engine) as s:
        for slug in BEFORE_ATL_182:
            # The Agents API shows the newest stored version when the agent has not run it yet
            # (AgentRoster::prompts): it is this file's.
            newest = s.scalars(
                select(PromptVersion)
                .where(PromptVersion.agent == slug)
                .order_by(PromptVersion.version.desc())
            ).first()
            assert newest is not None and newest.sha256[:12] == prompts.load_source(slug).version
            assert synced[slug][1] == newest.version
            assert s.scalar(
                select(func.count()).select_from(PromptVersion).where(PromptVersion.agent == slug)
            )
