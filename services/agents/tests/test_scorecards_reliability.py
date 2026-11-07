"""ATL-198: each voter's 30-day reliability weight, written into agent_scores by the daily job and read
back by votes.close_vote.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back; the world is set in 2093 so no other test's rows are in the window.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_agents import scoring
from atlas_agents import votes as v
from atlas_agents.agents import scores as hooks
from atlas_agents.registry import discover_hooks
from atlas_agents.scorecards import reliability as rel
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import AgentScore, JournalEntry, Order, PaperTrade, Proposal, RiskCheck, Vote
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

ENV = "demo"
RUN = uuid.uuid4().hex[:6].upper()
DAY = date(2093, 4, 20)  # Baghdad day of the proposals (the window's last day)
T = datetime(2093, 4, 20, 6, 0, tzinfo=UTC)  # 09:00 Baghdad
TODAY = DAY + timedelta(days=1)  # the job's fire day: it writes the rows for DAY
NOW = datetime(2093, 4, 20, 21, 30, tzinfo=UTC)  # 00:30 Baghdad on TODAY
D = Decimal
TA = "technical_analyst"
HIST = "historian"


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _ctx(factory: sessionmaker[Session], recorder: Recorder | None = None) -> ToolContext:
    return ToolContext(
        env=ENV,
        session_factory=factory,
        agent="orchestrator",
        correlation_id="test-reliability",
        clock=lambda: NOW,
        publish=recorder or Recorder(),
    )


@dataclass
class Seed:
    factory: sessionmaker[Session]
    k: int = 0

    def proposal(
        self, *, status: str = "rejected", version: int = 1, parent: uuid.UUID | None = None, at: datetime = T
    ) -> uuid.UUID:
        self.k += 1
        with self.factory() as s:
            row = Proposal(
                env=ENV,
                version=version,
                parent_id=parent,
                kind="entry",
                author="strategist",
                inst_id=f"R{RUN}{self.k}-USDT-SWAP",
                side="buy",
                order_type="market",
                stop=D("95"),
                take_profits=[{"price": "110", "pct": "100"}],
                expected_r=D("2"),
                status=status,
                created_at=at + timedelta(seconds=version),
                updated_at=at + timedelta(seconds=version),
            )
            s.add(row)
            s.commit()
            return row.id

    def paper(self, proposal_id: uuid.UUID, r: str | None) -> None:
        with self.factory() as s:
            p = s.get(Proposal, proposal_id)
            assert p is not None
            s.add(
                PaperTrade(
                    env=ENV,
                    proposal_id=p.id,
                    version=p.version,
                    inst_id=p.inst_id,
                    side="buy",
                    order_type="market",
                    proposal_status=p.status,
                    status="filled" if r is not None else "not_filled",
                    decided_at=T,
                    entry_px=D("100") if r is not None else None,
                    entry_ts=T if r is not None else None,
                    stop=D("95"),
                    exit_px=D("100") if r is not None else None,
                    exit_ts=T + timedelta(hours=1) if r is not None else None,
                    exit_reason="stop" if r is not None else "not_filled",
                    realised_r=D(r) if r is not None else None,
                    r_gross=D(r) if r is not None else None,
                    fees_r=D(0) if r is not None else None,
                    fee_rate=D("0.0005"),
                    tp1_hit=(D(r) > 0) if r is not None else None,
                    bar="1m" if r is not None else None,
                    legs=[],
                    simulated_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            s.commit()

    def taken(self, proposal_id: uuid.UUID, r: str) -> None:
        tag = uuid.uuid4().hex[:10]
        with self.factory() as s:
            p = s.get(Proposal, proposal_id)
            assert p is not None
            rc = RiskCheck(
                env=ENV,
                proposal_id=p.id,
                settings_version=1,
                result="approved",
                checks=[],
                decision="awaiting_owner",
                created_at=T,
            )
            s.add(rc)
            s.flush()
            s.add(
                Order(
                    env=ENV,
                    proposal_id=p.id,
                    risk_check_id=rc.id,
                    cl_ord_id=f"e{tag}",
                    ord_id=f"o{tag}",
                    inst_id=p.inst_id,
                    side="buy",
                    ord_type="market",
                    td_mode="isolated",
                    sz=D("1"),
                    filled_sz=D("1"),
                    avg_px=D("100"),
                    state="filled",
                    origin="atlas",
                    purpose="entry",
                    created_at=T,
                )
            )
            s.add(
                JournalEntry(
                    env=ENV,
                    agent="portfolio_manager",
                    inst_id=p.inst_id,
                    proposal_id=p.id,
                    title="post-mortem",
                    data={"actual": {"realised_r": r}},
                    tags=[],
                    created_at=T + timedelta(hours=2),
                )
            )
            s.commit()

    def vote(self, proposal_id: uuid.UUID, agent: str, vote: str, conf: str = "1") -> None:
        with self.factory() as s:
            s.add(
                Vote(
                    env=ENV,
                    proposal_id=proposal_id,
                    agent=agent,
                    vote=vote,
                    confidence=D(conf),
                    reason="r",
                    fix="tighten" if vote == "change" else None,
                    created_at=T,
                    updated_at=T,
                )
            )
            s.commit()


def _seed_world(db: sessionmaker[Session]) -> Seed:
    """The Technical Analyst: 12 scored votes, 9 correct, plus 4 that must not count. The Historian:
    5 scored votes, all correct."""
    seed = Seed(db)
    # 5 filled paper wins voted take (correct); the Historian also takes them (5/5 correct)
    for _ in range(5):
        pid = seed.proposal()
        seed.paper(pid, "2")
        seed.vote(pid, TA, "take")
        seed.vote(pid, HIST, "take")
    # a taken trade that won (journal R) voted take: correct
    pid = seed.proposal(status="closed")
    seed.taken(pid, "1.5")
    seed.vote(pid, TA, "take")
    # change on v1, fix applied in v2, the v2 paper trade won: correct
    v1 = seed.proposal(status="under_review")
    v2 = seed.proposal(version=2, parent=v1)
    seed.paper(v2, "1.2")
    seed.vote(v1, TA, "change")
    # 2 paper losses voted skip: correct
    for _ in range(2):
        pid = seed.proposal()
        seed.paper(pid, "-1")
        seed.vote(pid, TA, "skip")
    # 2 paper wins voted skip and 1 loss voted take: wrong
    for r, vote in (("2", "skip"), ("1", "skip"), ("-1.02", "take")):
        pid = seed.proposal()
        seed.paper(pid, r)
        seed.vote(pid, TA, vote)
    # not counted: 2 not-filled paper trades, a scratch, a proposal with no outcome yet, and one
    # outside the 30-day window
    for r in (None, None, "0.05"):
        pid = seed.proposal()
        seed.paper(pid, r)
        seed.vote(pid, TA, "skip" if r is None else "take")
    seed.vote(seed.proposal(status="expired"), TA, "take")
    old = seed.proposal(at=T - timedelta(days=30))
    seed.paper(old, "-1")
    seed.vote(old, TA, "take")
    return seed


def _row(factory: sessionmaker[Session], agent: str, day: date = DAY) -> AgentScore | None:
    with factory() as s:
        return s.scalars(
            select(AgentScore).where(AgentScore.env == ENV, AgentScore.agent == agent, AgentScore.day == day)
        ).first()


def test_weight_is_half_plus_accuracy_clamped_and_one_below_min_n() -> None:
    assert rel.weight_for(9, 12) == (D(9) / D(12), D("1.25"))
    assert rel.weight_for(10, 10)[1] == D("1.5")
    assert rel.weight_for(0, 10)[1] == D("0.5")
    assert rel.weight_for(9, 9)[1] == D("1")  # n < 10
    assert rel.weight_for(0, 0) == (None, D("1"))


def test_vote_correctness_follows_the_outcome() -> None:
    take = v.CastVote(vote="take", confidence=D(1))
    skip = v.CastVote(vote="skip", confidence=D(1))
    applied = v.CastVote(vote="change", confidence=D(1), fix_applied=True)
    unapplied = v.CastVote(vote="change", confidence=D(1))
    assert rel.is_correct(take, "win") and rel.is_correct(applied, "win")
    assert not rel.is_correct(skip, "win") and not rel.is_correct(unapplied, "win")
    assert rel.is_correct(skip, "loss") and rel.is_correct(unapplied, "loss")
    assert not rel.is_correct(take, "loss")
    assert rel.is_correct(take, "scratch") is None


def test_twelve_votes_nine_correct_writes_1_25_and_small_samples_get_one(db: sessionmaker[Session]) -> None:
    _seed_world(db)
    out = rel.run(_ctx(db), TODAY)
    assert (out[TA].n, out[TA].correct) == (12, 9)

    ta = _row(db, TA)
    assert ta is not None and ta.n == 0  # created: the agent had no row for the day
    assert ta.data["reliability_weight"] == "1.25"
    assert ta.data["reliability_accuracy"] == 0.75
    assert ta.data["reliability_n"] == 12 and ta.data["reliability_correct"] == 9

    hist = _row(db, HIST)
    assert hist is not None
    assert hist.data["reliability_weight"] == "1"  # 5 < min_n 10
    assert hist.data["reliability_n"] == 5 and hist.data["reliability_accuracy"] == 1.0

    nobody = _row(db, "derivatives_sentiment")
    assert nobody is not None and nobody.data["reliability_n"] == 0
    assert nobody.data["reliability_weight"] == "1"


def test_not_filled_paper_trades_are_not_counted(db: sessionmaker[Session]) -> None:
    seed = Seed(db)
    for _ in range(9):
        pid = seed.proposal()
        seed.paper(pid, "2")
        seed.vote(pid, TA, "take")
    for _ in range(5):  # would make n 14 if counted
        pid = seed.proposal()
        seed.paper(pid, None)
        seed.vote(pid, TA, "take")
    out = rel.run(_ctx(db), TODAY)
    assert out[TA].n == 9
    ta = _row(db, TA)
    assert ta is not None and ta.data["reliability_weight"] == "1"


def test_merges_into_the_scorers_row_and_survives_a_rescore(db: sessionmaker[Session]) -> None:
    _seed_world(db)
    with db() as s:
        scoring.upsert_score(
            s, agent=TA, env=ENV, day=DAY, n=4, hit_rate=0.5, data={"metrics": {"x": 1}}, now=NOW
        )
        s.commit()
    rel.run(_ctx(db), TODAY)
    ta = _row(db, TA)
    assert ta is not None and ta.n == 4 and ta.data["metrics"] == {"x": 1}
    assert ta.data["reliability_weight"] == "1.25"

    with db() as s:  # the scorer re-scores the day later (lookback): the weight stays
        scoring.upsert_score(
            s, agent=TA, env=ENV, day=DAY, n=5, hit_rate=0.6, data={"metrics": {"x": 2}}, now=NOW
        )
        s.commit()
    ta = _row(db, TA)
    assert ta is not None and ta.n == 5 and ta.data["metrics"] == {"x": 2}
    assert ta.data["reliability_weight"] == "1.25" and ta.data["reliability_n"] == 12

    rel.run(_ctx(db), TODAY)  # idempotent re-run: still one row
    with db() as s:
        count = s.scalar(
            text("SELECT count(*) FROM agent_scores WHERE env = :env AND agent = :a AND day = :d"),
            {"env": ENV, "a": TA, "d": DAY},
        )
    assert count == 1


def test_close_vote_after_the_job_uses_the_new_weight(db: sessionmaker[Session]) -> None:
    seed = _seed_world(db)
    rel.run(_ctx(db), TODAY)

    pid = seed.proposal(status="voting", at=NOW - timedelta(minutes=10))
    seed.vote(pid, TA, "take", "0.8")
    with db() as s:
        thread = uuid.uuid4()
        s.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread, "env": ENV, "key": f"r{RUN.lower()}", "ts": NOW},
        )
        s.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                "'devils_advocate', 'research_report', 'review', cast(:card as jsonb), :ts, :ts)"
            ),
            {
                "id": uuid.uuid4(),
                "env": ENV,
                "thread": thread,
                "event": uuid.uuid4(),
                "card": f'{{"type": "da_review", "proposal_id": "{pid}", "risk_rating": 1}}',
                "ts": NOW - timedelta(minutes=2),
            },
        )
        s.commit()

    closed = v.close_vote(_ctx(db), str(pid))
    lines = {x["agent"]: x for x in closed.card["voters"]}
    assert lines[TA]["reliability"] == "1.25"
    assert lines[TA]["weight"] == "1"  # 0.8 confidence x 1.25
    assert lines[HIST]["reliability"] == "1"  # n 5 < 10


def test_the_reliability_hook_runs_under_the_orchestrator_at_0030() -> None:
    found = discover_hooks()[rel.HOST_AGENT]
    (hook,) = [h for h in found.schedule if h.name == rel.JOB]
    assert hook.daily and hook.time_of_day().isoformat() == "00:30:00"
    assert hook.run is hooks.write_reliability_weights
