"""The weighted vote (ATL-154, brief §7.9). The pure tally needs nothing; the close_vote tests need the
checkout's test DB migrated (run `cd backend && php artisan test` first).

The DB world is set far in the future (NOW) and every row names its own instrument, so no other
test's agent_scores, cards or proposals are seen.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import votes as v
from atlas_agents.tools.framework import ToolContext, ToolError
from atlas_shared.db import AgentScore, Proposal, Vote, owner_day
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

VOTERS = ["technical_analyst", "derivatives_sentiment", "news_intelligence", "historian", "devils_advocate"]
SIX = Decimal("0.6")
NOW = datetime(2098, 5, 6, 9, 0, tzinfo=UTC)
ENV = "demo"


def take(conf: str = "1") -> v.CastVote:
    return v.CastVote(vote="take", confidence=Decimal(conf))


# -- pure tally -----------------------------------------------------------------------------------------


def test_five_takes_pass_and_da_rating_five_fails() -> None:
    votes = {a: take() for a in VOTERS}
    ok = v.tally(votes, threshold=SIX, da_rating=4)
    assert ok.passed and ok.ratio == 1 and ok.total_weight == 5
    bad = v.tally(votes, threshold=SIX, da_rating=5)
    assert not bad.passed and bad.ratio == 1
    assert bad.reasons == ("da_rating_too_high",)
    missing = v.tally(votes, threshold=SIX, da_rating=None)
    assert not missing.passed and missing.reasons == ("da_rating_missing",)


def test_missing_vote_is_skip_at_half_confidence_in_score_and_weight() -> None:
    votes = {a: take() for a in VOTERS[:4]}  # devils_advocate did not vote
    t = v.tally(votes, threshold=SIX, da_rating=2)
    line = next(x for x in t.lines if x.agent == "devils_advocate")
    assert line.missing and line.vote == "skip"
    assert line.confidence == Decimal("0.5") and line.weight == Decimal("0.5") and line.score == 0
    assert t.total_weight == Decimal("4.5")
    assert t.weighted_score == Decimal("4")
    assert t.ratio == Decimal("4") / Decimal("4.5")


def test_change_scores_half_only_when_fix_applied() -> None:
    base = {a: v.CastVote(vote="skip", confidence=Decimal("1")) for a in VOTERS}
    applied = v.tally(
        {**base, "historian": v.CastVote(vote="change", confidence=Decimal("1"), fix_applied=True)},
        threshold=SIX,
        da_rating=1,
    )
    not_applied = v.tally(
        {**base, "historian": v.CastVote(vote="change", confidence=Decimal("1"), fix_applied=False)},
        threshold=SIX,
        da_rating=1,
    )
    assert next(x for x in applied.lines if x.agent == "historian").score == Decimal("0.5")
    assert next(x for x in not_applied.lines if x.agent == "historian").score == 0
    assert applied.ratio == Decimal("0.1") and not_applied.ratio == 0


def test_reliability_is_clamped_and_multiplies_confidence() -> None:
    votes = {"historian": take("0.5"), "technical_analyst": take("1")}
    t = v.tally(
        votes,
        reliability={"historian": Decimal("9"), "technical_analyst": Decimal("0.1")},
        threshold=SIX,
        da_rating=1,
    )
    hist = next(x for x in t.lines if x.agent == "historian")
    ta = next(x for x in t.lines if x.agent == "technical_analyst")
    assert hist.reliability == Decimal("1.5") and hist.weight == Decimal("0.75")
    assert ta.reliability == Decimal("0.5") and ta.weight == Decimal("0.5")


_conf = st.decimals(min_value=0, max_value=1, places=4)
_cast = st.one_of(
    st.none(),
    st.builds(
        v.CastVote,
        vote=st.sampled_from(["take", "change", "skip"]),
        confidence=_conf,
        fix_applied=st.booleans(),
    ),
)
_votes = st.fixed_dictionaries({a: _cast for a in VOTERS})
_rel = st.fixed_dictionaries({a: st.decimals(min_value=0, max_value=3, places=3) for a in VOTERS})


@given(votes=_votes, rel=_rel, rating=st.one_of(st.none(), st.integers(1, 5)))
def test_ratio_is_always_between_zero_and_one(
    votes: dict[str, v.CastVote | None], rel: dict[str, Decimal], rating: int | None
) -> None:
    t = v.tally(votes, reliability=rel, threshold=SIX, da_rating=rating)
    assert 0 <= t.ratio <= 1
    assert t.passed == (t.ratio >= SIX and rating is not None and rating <= 4)


@given(votes=_votes, rel=_rel, who=st.sampled_from(VOTERS), conf=_conf)
def test_raising_skip_to_take_never_lowers_the_ratio(
    votes: dict[str, v.CastVote | None], rel: dict[str, Decimal], who: str, conf: Decimal
) -> None:
    before = {**votes, who: v.CastVote(vote="skip", confidence=conf)}
    after = {**votes, who: v.CastVote(vote="take", confidence=conf)}
    low = v.tally(before, reliability=rel, threshold=SIX, da_rating=1)
    high = v.tally(after, reliability=rel, threshold=SIX, da_rating=1)
    assert high.ratio >= low.ratio
    if low.passed:
        assert high.passed


# -- close_vote on Postgres -------------------------------------------------------------------------------


@dataclass
class World:
    engine: Engine
    factory: sessionmaker[Session]
    inst: str
    thread_id: uuid.UUID
    recorder: Recorder

    def ctx(self) -> ToolContext:
        return ToolContext(
            env=ENV,
            session_factory=self.factory,
            agent="orchestrator",
            correlation_id="test-votes",
            clock=lambda: NOW,
            publish=self.recorder,
        )

    def proposal(
        self, *, version: int = 1, parent: uuid.UUID | None = None, status: str = "voting"
    ) -> uuid.UUID:
        with self.factory() as s:
            row = Proposal(
                env=ENV,
                version=version,
                parent_id=parent,
                kind="entry",
                author="strategist",
                inst_id=self.inst,
                side="buy",
                order_type="limit",
                entry=Decimal("100"),
                stop=Decimal("97"),
                take_profits=[{"price": "106", "pct": "100"}],
                status=status,
                created_at=NOW - timedelta(minutes=30 - version),
                updated_at=NOW - timedelta(minutes=30 - version),
            )
            s.add(row)
            s.commit()
            return row.id

    def vote(self, proposal_id: uuid.UUID, agent: str, vote: str, conf: str, fix: str | None = None) -> None:
        with self.factory() as s:
            s.add(
                Vote(
                    env=ENV,
                    proposal_id=proposal_id,
                    agent=agent,
                    vote=vote,
                    confidence=Decimal(conf),
                    reason="r",
                    fix=fix,
                    created_at=NOW - timedelta(minutes=5),
                    updated_at=NOW - timedelta(minutes=5),
                )
            )
            s.commit()

    def da_card(self, proposal_id: uuid.UUID, rating: Any, *, minutes_ago: int = 3) -> None:
        card = {"type": "da_review", "proposal_id": str(proposal_id), "risk_rating": rating}
        ts = NOW - timedelta(minutes=minutes_ago)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                    "'devils_advocate', 'research_report', 'review', cast(:card as jsonb), :ts, :ts)"
                ),
                {
                    "id": uuid.uuid4(),
                    "env": ENV,
                    "thread": self.thread_id,
                    "event": uuid.uuid4(),
                    "card": json.dumps(card),
                    "ts": ts,
                },
            )

    def status(self, proposal_id: uuid.UUID) -> str:
        with self.factory() as s:
            return str(s.scalar(select(Proposal.status).where(Proposal.id == proposal_id)))


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"V{tag}-USDT-SWAP"
    thread_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "env": ENV, "key": f"v{tag}", "ts": NOW - timedelta(hours=1)},
        )
    w = World(engine, sessionmaker(bind=engine), inst, thread_id, Recorder())
    yield w
    with engine.begin() as conn:
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst, Proposal.parent_id.is_not(None)))
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst))
        conn.execute(text("DELETE FROM warroom_messages WHERE thread_id = :id"), {"id": thread_id})
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})
        conn.execute(
            delete(AgentScore).where(
                AgentScore.env == ENV, AgentScore.day >= owner_day(NOW) - timedelta(days=40)
            )
        )


def test_close_vote_passes_posts_one_summary_and_publishes(world: World) -> None:
    pid = world.proposal()
    for agent in VOTERS:
        world.vote(pid, agent, "take", "1")
    world.da_card(pid, 3)
    closed = v.close_vote(world.ctx(), str(pid))
    assert closed.result == "passed"
    assert world.status(pid) == "passed"
    summaries = [e for s, e in world.recorder.events if s == "atlas.warroom"]
    assert len(summaries) == 1
    payload = summaries[0].payload
    assert payload["kind"] == "vote_summary" and payload["author"] == "orchestrator"
    card = payload["card"]
    assert card["result"] == "passed" and card["ratio"] == "1" and card["threshold"] == "0.6"
    assert card["da_rating"] == 3 and len(card["voters"]) == 5
    assert {x["agent"] for x in card["voters"]} == set(VOTERS)
    closed_events = world.recorder.of("proposal.vote_closed")
    assert len(closed_events) == 1
    assert closed_events[0].payload["result"] == "passed"
    assert closed_events[0].payload["message_id"] == str(summaries[0].id)
    with pytest.raises(ToolError) as err:
        v.close_vote(world.ctx(), str(pid))
    assert err.value.code == "proposal_not_open"


def test_close_vote_rejects_on_da_rating_five_and_on_missing_rating(world: World) -> None:
    pid = world.proposal()
    for agent in VOTERS:
        world.vote(pid, agent, "take", "1")
    world.da_card(pid, 2, minutes_ago=10)
    world.da_card(pid, 5, minutes_ago=2)  # the newest rating counts
    closed = v.close_vote(world.ctx(), str(pid))
    assert closed.result == "rejected" and world.status(pid) == "rejected"
    assert closed.card["reasons"] == ["da_rating_too_high"]
    assert len(world.recorder.of("warroom.message")) == 1

    other = world.proposal(status="under_review")
    for agent in VOTERS:
        world.vote(other, agent, "take", "1")
    world.da_card(other, "4")  # not an integer: no rating
    again = v.close_vote(world.ctx(), str(other))
    assert again.result == "rejected" and again.card["reasons"] == ["da_rating_missing"]


def test_missing_vote_counts_as_skip_half_confidence_in_close_vote(world: World) -> None:
    pid = world.proposal()
    for agent in VOTERS[:4]:
        world.vote(pid, agent, "take", "1")
    world.da_card(pid, 2)
    closed = v.close_vote(world.ctx(), str(pid))
    da = next(x for x in closed.card["voters"] if x["agent"] == "devils_advocate")
    assert da["missing"] is True and da["vote"] == "skip" and da["confidence"] == "0.5"
    assert closed.card["total_weight"] == "4.5" and closed.card["weighted_score"] == "4"
    assert closed.result == "passed"


def test_change_vote_scores_half_only_when_fix_applied_in_version_two(world: World) -> None:
    # v1: historian asks for a change; then the Strategist revises to v2 (the fix applied).
    v1 = world.proposal(version=1, status="cancelled")
    world.vote(v1, "historian", "change", "1", fix="widen the stop to 96")
    v2 = world.proposal(version=2, parent=v1)
    for agent in ("technical_analyst", "derivatives_sentiment", "news_intelligence", "devils_advocate"):
        world.vote(v2, agent, "skip", "1")
    world.da_card(v1, 3)
    closed = v.close_vote(world.ctx(), str(v2))
    hist = next(x for x in closed.card["voters"] if x["agent"] == "historian")
    assert hist["vote"] == "change" and hist["fix_applied"] is True and hist["voted_version"] == 1
    assert hist["score"] == "0.5"
    assert closed.card["da_rating"] == 3  # the v1 review still counts for v2

    # A change cast on the version being closed: no later version, so the fix was not applied.
    solo = world.proposal(version=1)
    world.vote(solo, "historian", "change", "1", fix="tighten entry")
    world.da_card(solo, 1)
    again = v.close_vote(world.ctx(), str(solo))
    hist2 = next(x for x in again.card["voters"] if x["agent"] == "historian")
    assert hist2["fix_applied"] is False and hist2["score"] == "0"


def test_reliability_is_read_from_agent_scores(world: World) -> None:
    pid = world.proposal()
    world.vote(pid, "historian", "take", "1")
    world.da_card(pid, 1)
    with world.factory() as s:
        s.add(
            AgentScore(
                env=ENV,
                agent="historian",
                day=owner_day(NOW) - timedelta(days=2),
                n=10,
                data={"reliability_weight": "1.4"},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        s.add(
            AgentScore(
                env=ENV,
                agent="technical_analyst",
                day=owner_day(NOW) - timedelta(days=35),  # older than 30 days: ignored
                n=10,
                data={"reliability_weight": "0.5"},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        s.commit()
    closed = v.close_vote(world.ctx(), str(pid))
    lines = {x["agent"]: x for x in closed.card["voters"]}
    assert lines["historian"]["reliability"] == "1.4" and lines["historian"]["weight"] == "1.4"
    assert lines["technical_analyst"]["reliability"] == "1"


def test_owner_threshold_from_settings_decides(world: World) -> None:
    pid = world.proposal()
    for agent in VOTERS[:3]:
        world.vote(pid, agent, "take", "1")
    for agent in VOTERS[3:]:
        world.vote(pid, agent, "skip", "1")
    world.da_card(pid, 1)  # ratio 0.6: passes the default threshold 0.6
    key = str(v.votes_config()["threshold_setting"])
    with world.engine.begin() as conn:
        row = conn.execute(text("select id, data from settings where env = :e"), {"e": ENV}).first()
        added = row is None
        sid = uuid.uuid4() if added else row[0]
        if added:
            conn.execute(
                text(
                    "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "values (:id, :env, 1, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
                ),
                {"id": sid, "env": ENV, "data": json.dumps({key: "0.7"}), "t": NOW},
            )
        else:
            conn.execute(
                text("update settings set data = data || cast(:patch as jsonb) where id = :id"),
                {"id": sid, "patch": json.dumps({key: "0.7"})},
            )
    try:
        closed = v.close_vote(world.ctx(), str(pid))
    finally:
        with world.engine.begin() as conn:
            if added:
                conn.execute(text("delete from settings where id = :id"), {"id": sid})
            else:
                conn.execute(
                    text("update settings set data = data - :k where id = :id"), {"id": sid, "k": key}
                )
    assert closed.card["threshold"] == "0.7" and closed.card["ratio"] == "0.6"
    assert closed.result == "rejected" and closed.card["reasons"] == ["ratio_below_threshold"]


def test_unknown_proposal_is_refused(world: World) -> None:
    with pytest.raises(ToolError) as err:
        v.close_vote(world.ctx(), str(uuid.uuid4()))
    assert err.value.code == "unknown_proposal"
    assert world.recorder.events == []
