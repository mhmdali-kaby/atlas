"""warroom.submit_proposal and warroom.cast_vote (ATL-153, brief §6.3, §7.6). Needs the checkout's test
DB migrated: run `cd backend && php artisan test` first.

The world is set far in the future (NOW) so this test's account sync is the newest one, and every
row it adds names its own instrument, so the tests do not see each other's rows.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents.pm import guard
from atlas_agents.pm.rules import RuleAction
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_shared.db import (
    BalanceSnapshot,
    EconomicEvent,
    NewsItem,
    NewsSource,
    PositionSnapshot,
    Proposal,
    Vote,
    Watchlist,
    WatchlistItem,
    owner_day,
)
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

NOW = datetime(2098, 3, 4, 10, 0, tzinfo=UTC)
ENV = "demo"


@dataclass
class World:
    engine: Engine
    factory: sessionmaker[Session]
    inst: str
    watchlist_item_id: uuid.UUID
    thread_id: uuid.UUID
    tag: str

    def ctx(self, agent: str = "strategist", recorder: Recorder | None = None) -> ToolContext:
        return ToolContext(
            env=ENV,
            session_factory=self.factory,
            agent=agent,
            correlation_id=f"test-{self.tag}",
            clock=lambda: NOW,
            publish=recorder if recorder is not None else Recorder(),
        )

    def card(self, author: str, card: dict[str, Any], posted: datetime) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, created_at, updated_at) VALUES (:id, :env, :thread, :event, 'agent', "
                    ":author, 'research_report', 'card', cast(:card as jsonb), :ts, :ts)"
                ),
                {
                    "id": uuid.uuid4(),
                    "env": ENV,
                    "thread": self.thread_id,
                    "event": uuid.uuid4(),
                    "author": author,
                    "card": json.dumps(card),
                    "ts": posted,
                },
            )

    def proposals(self) -> list[Proposal]:
        with self.factory() as s:
            return list(
                s.scalars(select(Proposal).where(Proposal.inst_id == self.inst).order_by(Proposal.version))
            )


def _setup_card(inst: str, setup_type: str = "breakout") -> dict[str, Any]:
    none = setup_type == "none"
    return {
        "type": "technical_setup",
        "inst_id": inst,
        "setup_type": setup_type,
        "direction": "none" if none else "long",
        "entry_zone": None if none else ["100.0", "100.2"],
        "stop": None if none else "96.7",
        "data_as_of": (NOW - timedelta(minutes=10)).isoformat(),
    }


def _verdict_card(inst: str, verdict: str = "supports") -> dict[str, Any]:
    return {"type": "history_verdict", "inst_id": inst, "verdict": verdict, "n": 42}


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"T{tag}-USDT-SWAP"
    factory = sessionmaker(bind=engine)
    thread_id = uuid.uuid4()
    added_settings: uuid.UUID | None = None
    with engine.begin() as conn:
        if conn.execute(text("select 1 from settings where env = :e"), {"e": ENV}).first() is None:
            added_settings = uuid.uuid4()
            conn.execute(
                text(
                    "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "values (:id, :env, 1, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
                ),
                {
                    "id": added_settings,
                    "env": ENV,
                    "data": json.dumps({"risk_profile": "balanced", "min_expected_r": "1.8"}),
                    "t": NOW - timedelta(days=1),
                },
            )
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "env": ENV, "key": f"p{tag}", "ts": NOW - timedelta(hours=1)},
        )
    with Session(engine) as s:
        wl = Watchlist(
            env=ENV,
            date=owner_day(NOW),
            version=1,
            created_by="market_scout",
            status="published",
            data={},
            created_at=NOW - timedelta(hours=2),
            updated_at=NOW - timedelta(hours=2),
        )
        s.add(wl)
        s.flush()
        item = WatchlistItem(
            watchlist_id=wl.id,
            inst_id=inst,
            rank=1,
            asset_class="crypto",
            bias="long",
            conviction=Decimal("0.7"),
            thesis="t",
            catalysts=[],
            key_levels={},
            invalidation="i",
            data_as_of=NOW - timedelta(hours=2),
        )
        s.add(item)
        s.add(
            BalanceSnapshot(
                env=ENV,
                ccy=f"Z{tag}",
                account="trading",
                eq=Decimal("1000"),
                taken_at=NOW - timedelta(minutes=1),
            )
        )
        s.commit()
        item_id = item.id
    w = World(engine, factory, inst, item_id, thread_id, tag)
    w.card("technical_analyst", _setup_card(inst), NOW - timedelta(minutes=10))
    w.card("historian", _verdict_card(inst), NOW - timedelta(minutes=5))
    yield w
    with engine.begin() as conn:
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst, Proposal.parent_id.is_not(None)))
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst))
        conn.execute(delete(Watchlist).where(Watchlist.env == ENV, Watchlist.date == owner_day(NOW)))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.inst_id == inst))
        conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.ccy == f"Z{tag}"))
        conn.execute(delete(NewsItem).where(NewsItem.instruments.contains([inst])))
        conn.execute(delete(NewsSource).where(NewsSource.key == f"official_{tag.lower()}"))
        conn.execute(delete(EconomicEvent).where(EconomicEvent.source == f"test_{tag}"))
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})
        if added_settings is not None:
            conn.execute(text("delete from settings where id = :id"), {"id": added_settings})


def _proposal(inst: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "inst_id": inst,
        "side": "buy",
        "order_type": "limit",
        "entry_zone": [100.0, 100.2],
        "stop": 96.7,
        "take_profits": [{"price": 105.0, "pct": 50}, {"price": 110.5, "pct": 50}],
        "time_stop_minutes": 1440,
        "size_suggestion_pct_equity": 2.5,
        "leverage_suggestion": 2,
        "expected_r_multiple": 3.0,
        "probability_estimate": 0.5,
        "rationale_md": "Breakout with support from positioning.",
        "invalidation": "1H close below 99.0",
        "evidence": [{"type": "research_report", "id": "abc"}],
    }
    body.update(over)
    return body


async def _call(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await fw.make_handler(fw.get_tool(fq_name("warroom", name)), ctx)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    return bool(result.get("is_error")), body


async def _submit(w: World, rec: Recorder | None = None, **over: Any) -> tuple[bool, dict[str, Any]]:
    return await _call(w.ctx(recorder=rec), "submit_proposal", {"proposal": _proposal(w.inst, **over)})


# -- AC1: schema -----------------------------------------------------------------------------------------


async def test_a_proposal_without_a_stop_is_refused_by_the_schema(world: World) -> None:
    rec = Recorder()
    body = _proposal(world.inst)
    del body["stop"]
    error, out = await _call(world.ctx(recorder=rec), "submit_proposal", {"proposal": body})
    assert error and out["error"] == "invalid_proposal"
    assert "stop" in out["message"]
    assert world.proposals() == []
    assert rec.events == []


# -- AC2: the six checklist items -----------------------------------------------------------------------


def _off_watchlist(w: World) -> None:
    with w.engine.begin() as conn:
        conn.execute(delete(WatchlistItem).where(WatchlistItem.id == w.watchlist_item_id))


def _stale_setup(w: World) -> None:
    with w.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM warroom_messages WHERE thread_id = :t AND author = 'technical_analyst'"),
            {"t": w.thread_id},
        )
    w.card("technical_analyst", _setup_card(w.inst), NOW - timedelta(minutes=61))


def _confirmed_bad_news(w: World) -> None:
    key = f"official_{w.tag.lower()}"
    with Session(w.engine) as s:
        s.add(
            NewsSource(
                key=key,
                provider="test",
                kind="rss",
                category="crypto_news",
                name=key,
                poll_seconds=300,
                official=True,
                reputable=True,
            )
        )
        s.add(
            NewsItem(
                source=key,
                url=f"https://example.test/{w.tag}",
                canonical_url=f"https://example.test/{w.tag}",
                title=f"{w.inst} issuer halts withdrawals",
                published_at=NOW - timedelta(hours=2),
                fetched_at=NOW - timedelta(hours=2),
                instruments=[w.inst],
                sentiment=Decimal("-0.7"),
                importance=4,
            )
        )
        s.commit()


def _contradicting_historian(w: World) -> None:
    w.card("historian", _verdict_card(w.inst, "contradicts"), NOW - timedelta(minutes=1))


def _pending_duplicate(w: World) -> None:
    with Session(w.engine) as s:
        s.add(
            Proposal(
                env=ENV,
                author="strategist",
                inst_id=w.inst,
                side="buy",
                order_type="limit",
                entry=Decimal("100"),
                stop=Decimal("96"),
                status="voting",
                created_at=NOW - timedelta(minutes=20),
                updated_at=NOW - timedelta(minutes=20),
            )
        )
        s.commit()


@pytest.mark.parametrize(
    ("item", "name", "arrange", "over"),
    [
        (1, "on_watchlist", _off_watchlist, {}),
        (2, "technical_setup", _stale_setup, {}),
        (3, "news_and_events", _confirmed_bad_news, {}),
        (4, "historian_verdict", _contradicting_historian, {}),
        (5, "expectancy", None, {"expected_r_multiple": 1.0, "probability_estimate": 0.55}),
        (6, "no_duplicate", _pending_duplicate, {}),
    ],
)
async def test_each_checklist_item_returns_its_failure_and_saves_nothing(
    world: World, item: int, name: str, arrange: Any, over: dict[str, Any]
) -> None:
    if arrange is not None:
        arrange(world)
    before = len(world.proposals())
    rec = Recorder()
    error, out = await _submit(world, rec, **over)
    assert error and out["error"] == "checklist_failed", out
    assert f"({item}) {name}:" in out["message"]
    assert out["message"].startswith("1 of 6 "), out["message"]  # only that item failed
    assert len(world.proposals()) == before
    assert rec.events == []


async def test_a_setup_of_none_fails_item_2(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst, "none"), NOW - timedelta(minutes=2))
    error, out = await _submit(world)
    assert error and "(2) technical_setup:" in out["message"] and "no setup" in out["message"]


async def test_an_importance_5_event_inside_the_blackout_fails_item_3(world: World) -> None:
    with Session(world.engine) as s:
        s.add(
            EconomicEvent(
                source=f"test_{world.tag}",
                external_id="cpi",
                country="US",
                name="CPI",
                importance=5,
                scheduled_at=NOW + timedelta(minutes=30),
                created_at=NOW - timedelta(days=1),
            )
        )
        s.commit()
    error, out = await _submit(world)
    assert error and "(3) news_and_events:" in out["message"] and "CPI" in out["message"]


async def test_negative_expectancy_fails_item_5_even_with_enough_r(world: World) -> None:
    error, out = await _submit(world, expected_r_multiple=2.0, probability_estimate=0.35)
    assert error and "(5) expectancy:" in out["message"] and "p x R" in out["message"]


async def test_every_failing_item_is_returned_together(world: World) -> None:
    _off_watchlist(world)
    _contradicting_historian(world)
    error, out = await _submit(world, expected_r_multiple=1.0)
    assert error
    for part in ("(1) on_watchlist", "(4) historian_verdict", "(5) expectancy"):
        assert part in out["message"]


async def test_a_passing_proposal_is_saved_under_review_and_announced(world: World) -> None:
    rec = Recorder()
    error, out = await _submit(world, rec)
    assert not error, out
    data = out["data"]
    rows = world.proposals()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == data["proposal_id"]
    assert (row.status, row.version, row.kind, row.author) == ("under_review", 1, "entry", "strategist")
    assert row.stop == Decimal("96.7") and row.side == "buy" and row.entry_zone == ["100.0", "100.2"]
    assert row.take_profits == [{"price": "105.0", "pct": "50"}, {"price": "110.5", "pct": "50"}]
    assert [i["passed"] for i in data["checklist"]] == [True] * 6

    streams = [s for s, _e in rec.events]
    assert streams == ["atlas.warroom", "atlas.proposals"]
    message = rec.events[0][1]
    assert message.payload["kind"] == "proposal" and message.payload["author"] == "strategist"
    card = message.payload["card"]
    assert card["type"] == "proposal" and card["proposal_id"] == data["proposal_id"]
    assert card["expectancy_r"] == "1"  # 0.5 x 3 - (1 - 0.5)
    opened = rec.of("proposal.opened")
    assert len(opened) == 1
    assert opened[0].source == "agent:strategist"
    assert opened[0].payload["proposal_id"] == data["proposal_id"]
    assert opened[0].payload["message_id"] == str(message.id)
    assert opened[0].payload["card"] == card


async def test_the_cycle_id_comes_from_the_runs_correlation_id(world: World) -> None:
    cycle_id = uuid.uuid4()
    with world.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cycles (id, env, kind, started_at, created_at, updated_at) "
                "VALUES (:id, :e, 'scheduled', :t, :t, :t)"
            ),
            {"id": cycle_id, "e": ENV, "t": NOW},
        )
    try:
        ctx = world.ctx()
        ctx.run.correlation_id = f"cycle-{cycle_id}"
        error, out = await _call(ctx, "submit_proposal", {"proposal": _proposal(world.inst)})
        assert not error, out
        assert out["data"]["cycle_id"] == str(cycle_id)
    finally:
        with world.engine.begin() as conn:
            conn.execute(delete(Proposal).where(Proposal.inst_id == world.inst))
            conn.execute(text("DELETE FROM cycles WHERE id = :id"), {"id": cycle_id})


# -- AC3: revise and withdraw -----------------------------------------------------------------------------


async def test_one_revision_is_allowed_and_a_second_is_refused(world: World) -> None:
    _err, first = await _submit(world)
    pid = first["data"]["proposal_id"]
    rec = Recorder()
    error, revised = await _call(
        world.ctx(recorder=rec),
        "submit_proposal",
        {"action": "revise", "proposal_id": pid, "proposal": _proposal(world.inst, stop=96.0)},
    )
    assert not error, revised
    assert revised["data"]["version"] == 2 and revised["data"]["parent_id"] == pid
    rows = {str(r.id): r for r in world.proposals()}
    assert rows[pid].status == "cancelled"
    child = rows[revised["data"]["proposal_id"]]
    assert child.status == "under_review" and child.stop == Decimal("96.0")
    assert len(rec.of("proposal.opened")) == 1

    for target in (pid, revised["data"]["proposal_id"]):
        error, again = await _call(
            world.ctx(),
            "submit_proposal",
            {"action": "revise", "proposal_id": target, "proposal": _proposal(world.inst, stop=95.0)},
        )
        assert error and again["error"] in {"revision_limit"}, again
    assert len(world.proposals()) == 2


async def test_withdraw_sets_cancelled(world: World) -> None:
    _err, first = await _submit(world)
    pid = first["data"]["proposal_id"]
    rec = Recorder()
    error, out = await _call(
        world.ctx(recorder=rec), "submit_proposal", {"action": "withdraw", "proposal_id": pid}
    )
    assert not error, out
    assert out["data"]["status"] == "cancelled"
    assert world.proposals()[0].status == "cancelled"
    assert [e.type for _s, e in rec.events] == ["proposal.withdrawn"]
    # A withdrawn proposal no longer blocks a new one on the same side (item 6).
    error, again = await _submit(world)
    assert not error, again


async def test_only_the_author_can_withdraw(world: World) -> None:
    _err, first = await _submit(world)
    error, out = await _call(
        world.ctx("portfolio_manager"),
        "submit_proposal",
        {"action": "withdraw", "proposal_id": first["data"]["proposal_id"]},
    )
    assert error and out["error"] == "not_author"


# -- AC4: votes -------------------------------------------------------------------------------------------


async def test_a_second_vote_by_the_same_agent_replaces_the_first(world: World) -> None:
    _err, first = await _submit(world)
    pid = first["data"]["proposal_id"]
    ctx = world.ctx("devils_advocate")
    error, one = await _call(
        ctx,
        "cast_vote",
        {"proposal_id": pid, "vote": "change", "confidence": 0.6, "reason": "stop", "fix": "wider"},
    )
    assert not error, one
    error, two = await _call(
        ctx, "cast_vote", {"proposal_id": pid, "vote": "take", "confidence": 0.8, "reason": "ok"}
    )
    assert not error, two
    with world.factory() as s:
        votes = list(s.scalars(select(Vote).where(Vote.proposal_id == uuid.UUID(pid))))
    assert len(votes) == 1
    assert (votes[0].vote, votes[0].confidence, votes[0].reason, votes[0].fix) == (
        "take",
        Decimal("0.8"),
        "ok",
        None,
    )
    error, _ = await _call(
        world.ctx("historian"),
        "cast_vote",
        {"proposal_id": pid, "vote": "skip", "confidence": 0.5, "reason": "n"},
    )
    assert not error
    with world.factory() as s:
        assert len(list(s.scalars(select(Vote).where(Vote.proposal_id == uuid.UUID(pid))))) == 2


async def test_a_vote_on_a_cancelled_proposal_is_refused(world: World) -> None:
    _err, first = await _submit(world)
    pid = first["data"]["proposal_id"]
    await _call(world.ctx(), "submit_proposal", {"action": "withdraw", "proposal_id": pid})
    rec = Recorder()
    error, out = await _call(
        world.ctx("devils_advocate", rec),
        "cast_vote",
        {"proposal_id": pid, "vote": "take", "confidence": 0.7, "reason": "x"},
    )
    assert error and out["error"] == "proposal_not_open"
    assert rec.events == []
    with world.factory() as s:
        assert list(s.scalars(select(Vote).where(Vote.proposal_id == uuid.UUID(pid)))) == []


async def test_a_change_vote_needs_a_fix(world: World) -> None:
    _err, first = await _submit(world)
    error, out = await _call(
        world.ctx("devils_advocate"),
        "cast_vote",
        {"proposal_id": first["data"]["proposal_id"], "vote": "change", "confidence": 0.7, "reason": "x"},
    )
    assert error and out["error"] == "invalid_vote"


# -- AC5: who may submit which kind ----------------------------------------------------------------------


async def test_an_entry_from_the_portfolio_manager_is_refused(world: World) -> None:
    error, out = await _call(
        world.ctx("portfolio_manager"), "submit_proposal", {"proposal": _proposal(world.inst)}
    )
    assert error and out["error"] == "kind_not_allowed"
    assert world.proposals() == []


async def test_an_amend_stop_from_the_strategist_is_refused(world: World) -> None:
    error, out = await _call(
        world.ctx("strategist"),
        "submit_proposal",
        {
            "kind": "amend_stop",
            "proposal": {"inst_id": world.inst, "stop": 99.0, "rationale_md": "breakeven"},
        },
    )
    assert error and out["error"] == "kind_not_allowed"
    assert world.proposals() == []


async def test_a_portfolio_manager_action_needs_an_open_position(world: World) -> None:
    args = {
        "kind": "amend_stop",
        "proposal": {"inst_id": world.inst, "stop": 99.0, "rule": "breakeven_at_1r", "rationale_md": "+1R"},
    }
    error, out = await _call(world.ctx("portfolio_manager"), "submit_proposal", args)
    assert error and out["error"] == "no_open_position"
    with Session(world.engine) as s:
        s.add(
            PositionSnapshot(
                env=ENV,
                inst_id=world.inst,
                pos_side="net",
                pos=Decimal("3"),
                taken_at=NOW - timedelta(minutes=1),
            )
        )
        s.commit()
    rec = Recorder()
    ctx = dataclasses.replace(world.ctx("portfolio_manager", rec), correlation_id="pm:test:rule")
    # ATL-169: a PM action is accepted only when it is what its rules computed in this run.
    error, out = await _call(ctx, "submit_proposal", args)
    assert error and out["error"] == "not_computed_action"
    computed = RuleAction("amend_stop", "breakeven_at_1r", world.inst, "+1R", stop=Decimal("99.0"))
    expected = guard.Expected(
        inst_id=world.inst, side="long", current_stop=None, mark=Decimal("101"), actions=(computed,)
    )
    with guard.expecting(ENV, "pm:test:rule", expected):
        error, out = await _call(ctx, "submit_proposal", args)
    assert not error, out
    assert expected.submitted == {"breakeven_at_1r": out["data"]["proposal_id"]}
    row = world.proposals()[0]
    assert (row.kind, row.author, row.stop, row.status) == (
        "amend_stop",
        "portfolio_manager",
        Decimal("99.0"),
        "under_review",
    )
    assert len(rec.of("proposal.opened")) == 1
    # The open long now also blocks a new long entry (item 6).
    error, entry = await _submit(world)
    assert error and "(6) no_duplicate:" in entry["message"] and "open long position" in entry["message"]


async def test_a_partial_close_needs_close_pct(world: World) -> None:
    error, out = await _call(
        world.ctx("portfolio_manager"),
        "submit_proposal",
        {"kind": "partial_close", "proposal": {"inst_id": world.inst, "rationale_md": "TP1"}},
    )
    assert error and out["error"] == "invalid_proposal" and "close_pct" in out["message"]
