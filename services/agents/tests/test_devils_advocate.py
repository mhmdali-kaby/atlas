"""ATL-159: the Devil's Advocate (brief §7.7). Runs use a FakeClaudeClient script; the instrument,
candles, the proposal and its War Room card are seeded in this checkout's test DB
(`cd backend && php artisan test` first).

AC1 access row cell by cell; AC2 proposal.opened -> one da_review reply under the proposal and one
vote; AC3 missing item 7 twice -> degraded; AC4 take at rating 4 without an applied fix -> change;
AC5 the Strategist's reply -> one re-rate, a second reply -> none.
"""

from __future__ import annotations

import dataclasses
import json
import math
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import access
from atlas_agents import devils_advocate as da
from atlas_agents import testing as fake
from atlas_agents.agents import devils_advocate as hooks
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.catalogue import CATALOGUE
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, Candle, Instrument, Proposal, Vote
from atlas_shared.devils_review import CHECKLIST, DevilsReview, ReviewItem, guide_rating
from atlas_shared.events import EventEnvelope
from pydantic import ValidationError
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
SLUG = "devils_advocate"
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


def _candles(inst_id: str, bar: str, step: timedelta, bars: int) -> list[Candle]:
    last_open = NOW - step
    rows = []
    for i in range(bars):
        mid = 100 + 3 * math.sin(i / 7) + i * 0.01
        c = Decimal(f"{mid:.4f}")
        rows.append(
            Candle(
                env=ENV,
                inst_id=inst_id,
                bar=bar,
                ts=last_open - step * (bars - 1 - i),
                o=c - Decimal("0.2"),
                h=c + Decimal("1"),
                l=c - Decimal("1"),
                c=c,
                vol=Decimal(100),
                vol_ccy=Decimal(1),
                vol_ccy_quote=Decimal(10000),
                confirmed=True,
            )
        )
    return rows


@dataclass
class World:
    engine: Engine
    inst: str
    proposal_id: uuid.UUID
    proposal_row: uuid.UUID
    proposal_event: uuid.UUID
    thread_id: uuid.UUID

    def store(self, envelope: EventEnvelope) -> uuid.UUID:
        """Store a published warroom.message as atlas:bridge would; returns its row id."""
        p = envelope.payload
        row_id = uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, reply_to, created_at, updated_at) VALUES (:id, :env, :thread, :event, "
                    ":atype, :author, :kind, :content, cast(:card as jsonb), :reply, :ts, :ts)"
                ),
                {
                    "id": row_id,
                    "env": ENV,
                    "thread": p.get("thread_id") or self.thread_id,
                    "event": envelope.id,
                    "atype": p.get("author_type", "agent"),
                    "author": p["author"],
                    "kind": p.get("kind", "text"),
                    "content": p.get("content_md", ""),
                    "card": json.dumps(p.get("card")) if p.get("card") is not None else None,
                    "reply": p.get("reply_to"),
                    "ts": NOW,
                },
            )
        return row_id

    def votes(self) -> list[Vote]:
        with Session(self.engine) as s:
            return list(s.scalars(select(Vote).where(Vote.proposal_id == self.proposal_id)))


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    _wipe_runs(engine)
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"D{tag}-USDT-SWAP"
    thread_id, proposal_id, proposal_row, proposal_event = (uuid.uuid4() for _ in range(4))
    with Session(engine) as s:
        s.add(
            Instrument(
                env=ENV,
                inst_id=inst,
                inst_type="SWAP",
                asset_class="crypto",
                base_ccy=f"D{tag}",
                quote_ccy="USDT",
                settle_ccy="USDT",
                ct_val=Decimal("0.01"),
                lot_sz=Decimal("0.1"),
                min_sz=Decimal("0.1"),
                tick_sz=Decimal("0.1"),
                max_lever=Decimal("50"),
                state="live",
                list_time=NOW - timedelta(days=400),
                created_at=NOW - timedelta(days=400),
                updated_at=NOW - timedelta(days=400),
                last_seen_at=NOW - timedelta(hours=1),
            )
        )
        s.add_all(_candles(inst, "1H", timedelta(hours=1), 200))
        s.add_all(_candles(inst, "4H", timedelta(hours=4), 60))
        s.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "env": ENV, "key": f"da{tag}", "ts": NOW - timedelta(hours=1)},
        )
    with Session(engine) as s:
        s.add(
            Proposal(
                id=proposal_id,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst,
                side="buy",
                order_type="limit",
                entry_zone=["100.0", "100.4"],
                stop=Decimal("96.2"),
                take_profits=[{"price": "108", "pct": "50"}, {"price": "112", "pct": "50"}],
                time_stop_minutes=1440,
                size_suggestion_pct=Decimal("2.5"),
                leverage_suggestion=2,
                expected_r=Decimal("2.4"),
                probability=Decimal("0.52"),
                rationale_md="Breakout retest.",
                invalidation="1H close below 97",
                evidence=[],
                status="under_review",
                created_at=NOW - timedelta(seconds=5),
                updated_at=NOW - timedelta(seconds=5),
            )
        )
        s.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                "'strategist', 'text', 'BUY', cast(:card as jsonb), :ts, :ts)"
            ),
            {
                "id": proposal_row,
                "env": ENV,
                "thread": thread_id,
                "event": proposal_event,
                "card": json.dumps({"type": "proposal", "proposal_id": str(proposal_id), "inst_id": inst}),
                "ts": NOW - timedelta(seconds=5),
            },
        )
    yield World(engine, inst, proposal_id, proposal_row, proposal_event, thread_id)
    with engine.begin() as conn:
        conn.execute(delete(Vote).where(Vote.proposal_id == proposal_id))
        conn.execute(text("DELETE FROM warroom_messages WHERE thread_id = :t"), {"t": thread_id})
        conn.execute(delete(Proposal).where(Proposal.id == proposal_id))
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :t"), {"t": thread_id})
        conn.execute(delete(Candle).where(Candle.inst_id == inst))
        conn.execute(delete(Instrument).where(Instrument.inst_id == inst))
    _wipe_runs(engine)


def _supervisor(engine: Engine, client: fake.FakeClaudeClient, rec: Recorder) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)  # off by default; the owner turns it on
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: NOW,
    )


def _items(**problems: tuple[str, str]) -> list[dict[str, Any]]:
    """All 10 items "ok", except ``name=(severity, detail)`` ones."""
    out = []
    for n, name in enumerate(CHECKLIST, start=1):
        if name in problems:
            severity, detail = problems[name]
            out.append({"item": n, "name": name, "status": "problem", "severity": severity, "detail": detail})
        else:
            out.append({"item": n, "name": name, "status": "ok", "severity": "none", "detail": "OK"})
    return out


def _card(**changes: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "da_review",
        "items": _items(stop_quality=("minor", "stop is 1.2 x the noise threshold")),
        "strongest_objection": "The stop is only just outside normal 4h noise.",
        "risk_rating": 2,
        "fix": None,
        "fix_applied": False,
        "vote": "take",
        "confidence": 0.7,
        "reason": "Clean apart from a slightly tight stop.",
    }
    card.update(changes)
    return card


def _reply(card: dict[str, Any], text_: str = "Strongest objection: the stop.") -> list[Any]:
    return [
        fake.assistant_text(f"{text_}\n\n```json\n{json.dumps(card)}\n```"),
        fake.result(tokens_in=500, tokens_out=200, turns=1),
    ]


def _opened(world: World, ts: datetime = NOW) -> EventEnvelope:
    return EventEnvelope(
        type="proposal.opened",
        ts=ts,
        env=ENV,
        source="agent:strategist",
        correlation_id=f"cycle-{uuid.uuid4()}",
        payload={
            "proposal_id": str(world.proposal_id),
            "version": 1,
            "parent_id": None,
            "inst_id": world.inst,
            "side": "buy",
            "status": "under_review",
            "message_id": str(world.proposal_event),
        },
    )


def _reviews(rec: Recorder) -> list[EventEnvelope]:
    return [
        e
        for e in rec.of("warroom.message")
        if isinstance(e.payload.get("card"), dict) and e.payload["card"].get("type") == "da_review"
    ]


def _runs(engine: Engine) -> list[AgentRun]:
    with Session(engine) as s:
        return list(s.scalars(select(AgentRun).where(AgentRun.agent == SLUG, AgentRun.env == ENV)))


def _strategist_reply(reply_to: uuid.UUID, content: str) -> EventEnvelope:
    return EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="agent:strategist",
        payload={
            "author_type": "agent",
            "author": "strategist",
            "kind": "text",
            "content_md": content,
            "reply_to": str(reply_to),
            "mentions": [],
        },
    )


# -- AC1 + registration -------------------------------------------------------------------------------


def test_devils_advocate_row_cell_by_cell() -> None:
    """Brief §7.10's Devil's Advocate row: every read research tool, reply and cast_vote, no web."""
    allowed = set(access.allowed_tools(SLUG))
    by_server: dict[str, set[str]] = {}
    for name in allowed:
        _mcp, server, tool = name.split("__", 2)
        by_server.setdefault(server, set()).add(tool)
    catalogue = CATALOGUE
    assert by_server["okx_market"] == set(catalogue["okx_market"])  # all read
    assert by_server["atlas_data"] == set(catalogue["atlas_data"])  # all read
    assert by_server["news"] == set(catalogue["news"]) - {"web_search", "fetch_article"}  # all read
    assert by_server["history"] == set(catalogue["history"])  # all
    assert by_server["memory"] == {"read_journal", "read_notebook", "get_my_scores"}  # read
    assert by_server["warroom"] == {"post_message", "reply", "mention", "cast_vote"}  # reply + cast_vote
    assert "mcp__warroom__submit_proposal" not in allowed  # no proposals
    assert not any(t in allowed for t in ("WebSearch", "WebFetch", "mcp__news__web_search"))  # no web
    assert not access.MATRIX[SLUG].web
    assert set(by_server) == {"okx_market", "atlas_data", "news", "history", "memory", "warroom"}


def test_disabled_by_default_and_registers_both_triggers() -> None:
    assert not REGISTRY[SLUG].enabled
    found = discover_hooks()[SLUG]
    assert found.on_mention is None
    assert set(found.on_event) == {"proposal.opened", "warroom.message.text"}


# -- AC2: proposal.opened -> one reply + one vote --------------------------------------------------


async def test_proposal_opened_posts_one_review_reply_and_casts_one_vote(world: World) -> None:
    client = fake.FakeClaudeClient([_reply(_card())])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()

    assert len(client.queries) == 1
    prompt = client.queries[0]
    assert str(world.proposal_id) in prompt and '"stop_noise"' in prompt and '"chasing"' in prompt
    [message] = _reviews(rec)
    assert message.payload["author"] == SLUG
    assert message.payload["kind"] == "research_report"
    assert message.payload["reply_to"] == str(world.proposal_row)  # under the proposal's card
    assert message.payload["thread_id"] == str(world.thread_id)
    review = DevilsReview.model_validate(message.payload["card"])
    assert len(review.items) == 10 and [i.item for i in review.items] == list(range(1, 11))
    assert review.proposal_id == str(world.proposal_id) and review.version == 1 and review.round == 1
    assert isinstance(message.payload["card"]["risk_rating"], int)  # ATL-154 reads an integer
    noise = review.precomputed["stop_noise"] if review.precomputed else {}
    assert noise["available"] and Decimal(noise["ratio"]) > 0
    assert review.precomputed and review.precomputed["chasing"]["available"]
    assert review.meta and review.meta["late"] is False and "vote_error" not in review.meta, review.meta

    [cast] = rec.of("proposal.vote_cast")
    assert cast.payload["agent"] == SLUG and cast.payload["vote"] == "take"
    [vote] = world.votes()
    assert vote.agent == SLUG and vote.vote == "take" and vote.confidence == Decimal("0.7")
    [run] = _runs(world.engine)
    assert run.status == "ok"
    await sup.close()


async def test_chasing_uses_the_latest_technical_setup_levels(world: World) -> None:
    """A stored TechnicalSetup card feeds item 5's nearest level (ATL-159 review: LatestCard)."""
    world.store(
        EventEnvelope(
            type="warroom.message",
            env=ENV,
            source="agent:technical_analyst",
            payload={
                "author_type": "agent",
                "author": "technical_analyst",
                "kind": "research_report",
                "content_md": "Pullback long.",
                "card": {
                    "type": "technical_setup",
                    "inst_id": world.inst,
                    "levels_used": [{"id": "1H:z1", "price": "99.8"}],
                },
            },
        )
    )
    client = fake.FakeClaudeClient([_reply(_card())])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()

    [message] = _reviews(rec)
    chase = message.payload["card"]["precomputed"]["chasing"]
    assert chase["available"] is True, chase
    assert chase["nearest_level"] == "99.8"
    assert chase["level_source"] == "technical_setup.levels_used"
    await sup.close()


async def test_an_event_older_than_the_deadline_starts_no_review(world: World) -> None:
    client = fake.FakeClaudeClient([])
    sup = _supervisor(world.engine, client, Recorder())
    await sup.handle_proposals_envelope(_opened(world, ts=NOW - timedelta(seconds=61)))
    await hooks.settled()
    assert client.queries == []
    await sup.close()


# -- AC3: a missing item is retried once, then degraded --------------------------------------------


async def test_missing_item_7_is_retried_once_then_the_agent_is_degraded(world: World) -> None:
    card = _card()
    card["items"] = [i for i in card["items"] if i["item"] != 7]
    client = fake.FakeClaudeClient([_reply(card), _reply(card)])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()

    assert len(client.queries) == 2  # the answer and its one retry
    assert "not accepted" in client.queries[1] and "7 (liquidity)" in client.queries[1]
    assert _reviews(rec) == []
    assert rec.of("proposal.vote_cast") == [] and world.votes() == []
    [run] = _runs(world.engine)
    assert run.status == "degraded"
    with Session(world.engine) as s:
        state = s.scalars(select(AgentState).where(AgentState.agent == SLUG, AgentState.env == ENV)).one()
    assert state.state == "degraded"
    await sup.close()


async def test_missing_item_then_a_full_card_is_accepted(world: World) -> None:
    bad = _card()
    bad["items"] = bad["items"][:6]
    client = fake.FakeClaudeClient([_reply(bad), _reply(_card())])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()
    assert len(client.queries) == 2
    assert len(_reviews(rec)) == 1 and len(world.votes()) == 1
    await sup.close()


# -- AC4: take at rating 4 with no applied fix becomes change ---------------------------------------


async def test_take_at_rating_4_without_an_applied_fix_becomes_change(world: World) -> None:
    card = _card(
        items=_items(
            event_risk=("serious", "CPI in 3 h, inside the 24 h window"),
            stop_quality=("serious", "stop is 0.6 x the noise threshold"),
        ),
        risk_rating=4,
        vote="take",
        fix="Wait until after CPI and widen the stop to 95.4",
    )
    client = fake.FakeClaudeClient([_reply(card)])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()

    [message] = _reviews(rec)
    review = DevilsReview.model_validate(message.payload["card"])
    assert review.vote == "change" and review.risk_rating == 4
    [rule] = [c for c in review.corrections if c["rule"] == "take_needs_low_rating"]
    assert rule["from"] == "take" and rule["to"] == "change" and rule["risk_rating"] == 4
    [vote] = world.votes()
    assert vote.vote == "change" and vote.fix == "Wait until after CPI and widen the stop to 95.4"
    [cast] = rec.of("proposal.vote_cast")
    assert cast.payload["vote"] == "change"
    await sup.close()


def _review(**changes: Any) -> DevilsReview:
    base = {"proposal_id": "p", "version": 1, "inst_id": "X-USDT-SWAP", **_card()}
    base.update(changes)
    return DevilsReview.model_validate(base)


def test_rating_guide_and_vote_rules_in_code() -> None:
    gap = _review(items=_items(event_risk=("loss_gt_1r", "earnings in 5 h can gap 2 ATR through the stop")))
    out, corrections = da.enforce_review(gap, version=1, round_=1)
    assert out.risk_rating == 5 and corrections[0]["rule"] == "rating_guide"
    assert out.vote == "skip"  # take at 5 with no fix
    assert [c["rule"] for c in corrections] == ["rating_guide", "take_needs_low_rating"]

    applied = _review(
        items=_items(crowding=("serious", "funding z 2.8")), risk_rating=3, fix="halve size", fix_applied=True
    )
    first, corr = da.enforce_review(applied, version=1, round_=1)
    assert first.fix_applied is False and first.vote == "change"  # nothing applied on a first review
    assert corr[0]["rule"] == "fix_applied_impossible"
    rerated, corr = da.enforce_review(applied, version=1, round_=2)
    assert rerated.vote == "take" and corr == []  # the re-rate may accept an applied fix

    lazy = _review(vote="change", fix=None)
    out, corrections = da.enforce_review(lazy, version=1, round_=1)
    assert out.vote == "skip" and corrections[-1]["rule"] == "change_without_fix"

    assert guide_rating(_review().items) == 2
    assert guide_rating(_review(items=_items()).items) == 1


def test_a_problem_needs_a_number_and_items_must_be_complete() -> None:
    with pytest.raises(ValidationError, match="state it with a number"):
        ReviewItem(item=4, name="stop_quality", status="problem", severity="minor", detail="tight")
    with pytest.raises(ValidationError, match="item 4 is stop_quality"):
        ReviewItem(item=4, name="chasing", status="ok", detail="OK")
    with pytest.raises(ValidationError, match="missing: 7 \\(liquidity\\)"):
        _review(items=[i for i in _items() if i["item"] != 7])


# -- AC5: one re-rate after the Strategist's reply ----------------------------------------------------


async def test_strategist_reply_triggers_one_rerate_and_a_second_reply_none(world: World) -> None:
    rerate_card = _card(risk_rating=1, items=_items(), reason="The Strategist's evidence resolves the stop.")
    client = fake.FakeClaudeClient([_reply(_card()), _reply(rerate_card, "Re-rated.")])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()
    [first] = _reviews(rec)
    review_row = world.store(first)

    await sup.handle_envelope(_strategist_reply(review_row, "The stop is 1.2x noise per your own table."))
    await hooks.settled()
    assert len(client.queries) == 2
    assert "Re-rate it once" in client.queries[1] and "1.2x noise" in client.queries[1]
    reviews = _reviews(rec)
    assert len(reviews) == 2
    second = DevilsReview.model_validate(reviews[1].payload["card"])
    assert second.round == 2 and second.rerate_of == str(review_row)
    assert reviews[1].payload["reply_to"] == str(review_row)
    assert second.risk_rating == 1
    [vote] = world.votes()  # the re-rate replaced the first vote
    assert vote.vote == "take" and "Strategist's evidence" in vote.reason
    rerate_row = world.store(reviews[1])

    await sup.handle_envelope(_strategist_reply(review_row, "One more point."))
    await sup.handle_envelope(_strategist_reply(rerate_row, "And on your re-rate..."))
    await hooks.settled()
    assert len(client.queries) == 2  # no further run
    assert len(_reviews(rec)) == 2
    await sup.close()


async def test_a_stored_rerate_blocks_another_after_a_restart(world: World) -> None:
    """The once-only rule survives a restart: a stored round-2 card for the proposal is enough."""
    client = fake.FakeClaudeClient([_reply(_card())])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()
    [first] = _reviews(rec)
    review_row = world.store(first)
    stored = dict(first.payload)
    stored["card"] = {**first.payload["card"], "round": 2, "rerate_of": str(review_row)}
    world.store(
        EventEnvelope(type="warroom.message", env=ENV, source="agent:devils_advocate", payload=stored)
    )

    await sup.handle_envelope(_strategist_reply(review_row, "Reply after a restart."))
    await hooks.settled()
    assert len(client.queries) == 1
    await sup.close()


async def test_a_reply_by_anyone_but_the_strategist_starts_nothing(world: World) -> None:
    client = fake.FakeClaudeClient([_reply(_card())])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await sup.handle_proposals_envelope(_opened(world))
    await hooks.settled()
    review_row = world.store(_reviews(rec)[0])
    other = _strategist_reply(review_row, "I disagree.")
    other.payload["author"] = "historian"
    await sup.handle_envelope(other)
    await hooks.settled()
    assert len(client.queries) == 1
    await sup.close()
