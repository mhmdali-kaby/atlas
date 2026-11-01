"""ATL-194: every closed trade gets a Portfolio Manager post-mortem in the journal (brief §7.8).

Figures come from the proposal, fills and candles seeded here; runs use a FakeClaudeClient script.
Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from atlas_agents import testing as fake
from atlas_agents.agents import portfolio_manager as hooks
from atlas_agents.pm import manage
from atlas_agents.pm import postmortem as pmm
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    AlgoOrder,
    Candle,
    Fill,
    Instrument,
    JournalEntry,
    PositionClosed,
    Proposal,
    Vote,
)
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
SLUG = "portfolio_manager"
D = Decimal
# 10:00 Baghdad on a far-future day: neither the 23:30 daily report nor real rows interfere.
OPENED = datetime(2099, 7, 1, 7, 0, tzinfo=UTC)
CLOSED = OPENED + timedelta(minutes=270)
NOW = CLOSED + timedelta(minutes=3)


@dataclass
class World:
    engine: Engine
    tag: str
    clock: list[datetime]
    inst: str = ""
    proposal_id: uuid.UUID | None = None

    def now(self) -> datetime:
        return self.clock[0]

    def entries(self) -> list[JournalEntry]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(JournalEntry).where(JournalEntry.env == ENV, JournalEntry.inst_id == self.inst)
                )
            )


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


def _fill(
    s: Session, w: World, n: int, side: str, px: str, sz: str, fee: str, at: datetime, pnl: str
) -> None:
    s.add(
        Fill(
            env=ENV,
            trade_id=f"t{w.tag}{n}",
            ord_id=f"o{w.tag}{n}",
            inst_id=w.inst,
            side=side,
            fill_px=D(px),
            fill_sz=D(sz),
            fee=D(fee),
            fee_ccy="USDT",
            ts=at,
            fill_pnl=D(pnl),
            created_at=at,
        )
    )


def _candle(s: Session, w: World, bar: str, at: datetime, h: str, low: str) -> None:
    s.add(
        Candle(
            env=ENV,
            inst_id=w.inst,
            bar=bar,
            ts=at,
            o=D("100"),
            h=D(h),
            l=D(low),
            c=D("100"),
            vol=D("1"),
            confirmed=True,
        )
    )


def _seed(w: World) -> None:
    """A long: 2 @ 100 + 2 @ 101 (ctVal 0.1), stop 95, closed by the take-profit: 4 @ 110."""
    pid = uuid.uuid4()
    w.proposal_id = pid
    with Session(w.engine) as s:
        s.add(
            Instrument(
                env=ENV,
                inst_id=w.inst,
                inst_type="SWAP",
                ct_val=D("0.1"),
                lot_sz=D("1"),
                min_sz=D("1"),
                tick_sz=D("0.1"),
                state="live",
                asset_class="crypto",
            )
        )
        s.add(
            Proposal(
                id=pid,
                env=ENV,
                kind="entry",
                author="strategist",
                inst_id=w.inst,
                side="buy",
                order_type="limit",
                entry=D("100"),
                stop=D("95"),
                take_profits=[{"price": "110", "pct": "100"}],
                expected_r=D("2"),
                probability=D("0.55"),
                invalidation="1H close below 96",
                evidence=[{"type": "research_report", "id": "r1"}],
                status="filled",
                created_at=OPENED - timedelta(minutes=30),
                updated_at=OPENED,
            )
        )
        s.flush()
        for agent, vote in (("technical_analyst", "take"), ("devils_advocate", "skip")):
            s.add(
                Vote(
                    env=ENV,
                    proposal_id=pid,
                    agent=agent,
                    vote=vote,
                    confidence=D("0.7"),
                    reason="test",
                    created_at=OPENED,
                )
            )
        _fill(s, w, 1, "buy", "100", "2", "-0.01", OPENED, "0")
        _fill(s, w, 2, "buy", "101", "2", "-0.0101", OPENED + timedelta(minutes=10), "0")
        _fill(s, w, 3, "sell", "110", "4", "-0.022", CLOSED, "3.8")  # (110 - 100.5) x 4 x 0.1
        s.add(
            AlgoOrder(
                env=ENV,
                algo_id=f"a{w.tag}",
                algo_cl_ord_id=f"tp{w.tag.lower()}",
                inst_id=w.inst,
                kind="tp",
                side="sell",
                trigger_px=D("110"),
                state="effective",
                origin="atlas",
                created_at=OPENED,
                updated_at=CLOSED,
            )
        )
        # 1m candles inside the hold (high 111, low 98) and outside it (never used).
        _candle(s, w, "1m", OPENED, "101", "99")
        _candle(s, w, "1m", OPENED + timedelta(minutes=60), "105", "98")
        _candle(s, w, "1m", CLOSED - timedelta(minutes=1), "111", "109")
        _candle(s, w, "1m", OPENED - timedelta(minutes=5), "100", "50")
        _candle(s, w, "1m", CLOSED + timedelta(minutes=1), "200", "109")
        _candle(s, w, "1H", OPENED, "300", "10")  # 1m wins when it has candles
        s.add(
            PositionClosed(
                env=ENV,
                pos_id=f"p{w.tag}",
                inst_id=w.inst,
                inst_type="SWAP",
                mgn_mode="isolated",
                pos_side="net",
                side="long",
                close_type="2",
                size=D("4"),
                entry_px=D("100.5"),
                exit_px=D("110"),
                realised_pnl=D("3.7079"),
                pnl=D("3.8"),
                fee=D("-0.0421"),
                funding_fee=D("-0.05"),
                opened_at=OPENED,
                closed_at=CLOSED,
                proposal_id=pid,
                agent="strategist",
                origin="atlas",
                created_at=CLOSED,
            )
        )
        s.commit()


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    _wipe_runs(engine)
    manage.reset()
    tag = uuid.uuid4().hex[:6].upper()
    w = World(engine, tag, [NOW], inst=f"PM{tag}-USDT-SWAP")
    _seed(w)
    yield w
    with engine.begin() as conn:
        conn.execute(delete(JournalEntry).where(JournalEntry.inst_id == w.inst))
        conn.execute(delete(PositionClosed).where(PositionClosed.inst_id == w.inst))
        conn.execute(delete(Fill).where(Fill.inst_id == w.inst))
        conn.execute(delete(Candle).where(Candle.inst_id == w.inst))
        conn.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == w.inst))
        conn.execute(delete(Vote).where(Vote.proposal_id == w.proposal_id))
        conn.execute(delete(Proposal).where(Proposal.inst_id == w.inst))
        conn.execute(delete(Instrument).where(Instrument.inst_id == w.inst))
    _wipe_runs(engine)
    manage.reset()


def _supervisor(w: World, client: fake.FakeClaudeClient, rec: Recorder) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)  # disabled by default
    return Supervisor(
        env=ENV,
        engine=w.engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=w.now,
    )


GOOD = {
    "right_md": "Technical Analyst's 110 target held; the Strategist sized it right.",
    "wrong_md": "Devil's Advocate voted skip on weak momentum, which did not matter.",
    "lesson": "When the 1H trend and a strong level agree, let the take-profit work, do not trail early.",
    "setup_type": "pullback_to_support",
    "regime": "trend",
    "catalyst_type": "none",
}


def _answer(block: dict[str, Any], text: str = "The long reached its target.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(block)}\n```"),
        fake.result(tokens_in=400, tokens_out=150, turns=1),
    ]


def _cards(rec: Recorder) -> list[EventEnvelope]:
    return [e for e in rec.of("warroom.message") if e.payload.get("kind") == "post_mortem"]


async def _tick(w: World, client: fake.FakeClaudeClient, at: datetime) -> Recorder:
    rec = Recorder()
    sup = _supervisor(w, client, rec)
    try:
        w.clock[0] = at
        await sup.tick(at)
        await hooks.settled()
    finally:
        await sup.close()
    return rec


def _r(value: Decimal) -> str:
    return format(value.quantize(D("0.01"), rounding=ROUND_HALF_UP), "f")


def test_registered_as_a_pm_schedule_hook() -> None:
    found = [h for h in discover_hooks()[SLUG].schedule if h.name == pmm.JOB]
    assert len(found) == 1 and found[0].every_seconds == pmm.EVERY_SECONDS


# -- AC1 + AC2 + AC3: one entry, one card, hand-computed numbers, tags and a rule lesson -----------


async def test_a_closed_position_gets_one_entry_and_one_card_with_hand_computed_figures(world: World) -> None:
    client = fake.FakeClaudeClient([_answer(GOOD)])
    rec = await _tick(world, client, NOW)

    assert len(client.queries) == 1
    entries = world.entries()
    assert len(entries) == 1
    entry = entries[0]
    cards = _cards(rec)
    assert len(cards) == 1

    # Hand-computed from the seeded fills and candles.
    avg_entry = (D("100") * 2 + D("101") * 2) / 4  # 100.5
    gross = (D("110") - avg_entry) * 4 * D("0.1")  # 3.8
    fees = D("-0.01") + D("-0.0101") + D("-0.022")
    realised = gross + fees + D("-0.05")
    risk = (avg_entry - D("95")) * 4 * D("0.1")  # 2.2
    mfe = (D("111") - avg_entry) / (avg_entry - D("95"))
    mae = (D("98") - avg_entry) / (avg_entry - D("95"))
    actual = entry.data["actual"]
    assert actual["avg_entry"] == "100.5" and actual["avg_exit"] == "110"
    assert actual["realised_pnl_usd"] == _r(realised) == "3.71"
    assert actual["fees_usd"] == _r(fees) and actual["funding_usd"] == "-0.05"
    assert actual["initial_risk_usd"] == _r(risk) == "2.20"
    assert actual["realised_r"] == _r(realised / risk) == "1.69"
    assert actual["holding_minutes"] == 270
    assert actual["mfe_r"] == _r(mfe) == "1.91"
    assert actual["mae_r"] == _r(mae) == "-0.45"
    assert actual["excursion_bar"] == "1m"
    assert actual["close_reason"] == "take_profit"
    assert entry.data["expected"]["stop"] == "95" and entry.data["expected"]["expected_r"] == "2"
    assert {v["agent"]: v["vote"] for v in entry.data["votes"]} == {
        "technical_analyst": "take",
        "devils_advocate": "skip",
    }
    assert entry.data["position_ref"].startswith(f"p{world.tag}:")
    assert entry.proposal_id == world.proposal_id
    assert entry.data["lesson_pending"] is False and entry.data["on_time"] is True

    # Tags: setup type, asset class, regime, catalyst type and outcome; one rule-style lesson.
    assert set(entry.tags) == {
        "setup:pullback_to_support",
        "asset:crypto",
        "regime:trend",
        "catalyst:none",
        "outcome:win",
        "win",
    }
    assert entry.data["outcome"] == "win"
    # Every agent that authored or voted on the proposal: the proposer plus the seeded voters.
    agents = ["devils_advocate", "strategist", "technical_analyst"]
    assert entry.data["agents"] == agents
    assert set(entry.data["agents"]) == {"strategist", "technical_analyst", "devils_advocate"}
    assert entry.lessons_md == GOOD["lesson"]
    assert "\n" not in entry.lessons_md and entry.lessons_md.startswith("When ")

    card = cards[0].payload["card"]
    assert card["type"] == "post_mortem" and card["journal_id"] == str(entry.id)
    assert card["actual"] == actual and card["lesson"] == GOOD["lesson"]
    assert card["lesson_pending"] is False and card["tags"]["outcome"] == "win"
    assert card["agents"] == agents and "win" in card["tag_list"]

    # A second run adds nothing.
    rec2 = await _tick(world, fake.FakeClaudeClient(), NOW + timedelta(minutes=2))
    assert len(world.entries()) == 1
    assert _cards(rec2) == []


async def test_the_model_cannot_change_a_figure_and_invented_numbers_are_flagged(world: World) -> None:
    forged = {**GOOD, "actual": {"realised_pnl_usd": "9999"}, "realised_r": "7"}
    client = fake.FakeClaudeClient([_answer(forged, "We made $9,999 on this, a 7R winner.")])
    rec = await _tick(world, client, NOW)
    entry = world.entries()[0]
    assert entry.data["actual"]["realised_pnl_usd"] == "3.71"
    card = _cards(rec)[0].payload["card"]
    assert card["actual"]["realised_r"] == "1.69"
    assert card["meta"]["unmatched_numbers"]


async def test_a_lesson_that_is_not_a_rule_gets_one_retry(world: World) -> None:
    bad = {**GOOD, "lesson": "The trade went well."}
    client = fake.FakeClaudeClient([_answer(bad), _answer(GOOD)])
    await _tick(world, client, NOW)
    assert len(client.queries) == 2 and "reusable rule" in client.queries[1]
    assert world.entries()[0].lessons_md == GOOD["lesson"]


# -- AC4: a failed run still writes the facts, and the next run fills the lesson -------------------


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param([fake.result(is_error=True, text="boom")], id="error"),
        pytest.param([fake.rate_limit_rejected(), fake.rate_limit_result()], id="rate_limited"),
    ],
)
async def test_a_failed_run_writes_the_facts_with_lesson_pending_then_fills_it_next_run(
    world: World, failure: list[Any]
) -> None:
    rec = await _tick(world, fake.FakeClaudeClient([failure]), NOW)
    entries = world.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.data["lesson_pending"] is True
    assert entry.lessons_md is None
    assert entry.data["actual"]["realised_pnl_usd"] == "3.71"
    assert entry.data["actual"]["realised_r"] == "1.69"
    assert "outcome:win" in entry.tags and "asset:crypto" in entry.tags
    assert "regime:unknown" in entry.tags
    cards = _cards(rec)
    assert len(cards) == 1 and cards[0].payload["card"]["lesson_pending"] is True

    _wipe_runs(world.engine)  # a fresh session: the usage limit has reset
    client = fake.FakeClaudeClient([_answer(GOOD)])
    rec2 = await _tick(world, client, NOW + timedelta(minutes=2))
    assert len(client.queries) == 1
    entries = world.entries()
    assert len(entries) == 1 and entries[0].id == entry.id
    filled = entries[0]
    assert filled.data["lesson_pending"] is False
    assert filled.lessons_md == GOOD["lesson"]
    assert "regime:trend" in filled.tags and "setup:pullback_to_support" in filled.tags
    assert "win" in filled.tags and filled.data["agents"] == entry.data["agents"]
    assert filled.data["actual"] == entry.data["actual"]
    update = _cards(rec2)
    assert len(update) == 1 and update[0].payload["card"]["lesson_update"] is True

    # Nothing left to do.
    client3 = fake.FakeClaudeClient()
    await _tick(world, client3, NOW + timedelta(minutes=4))
    assert client3.queries == [] and len(world.entries()) == 1


# -- pure helpers ----------------------------------------------------------------------------------


def test_validate_answer_refuses_tags_outside_the_vocabulary() -> None:
    answer, why = pmm.validate_answer({**GOOD, "regime": "moon"})
    assert answer is None and why is not None and "regime" in why
    answer, why = pmm.validate_answer({**GOOD, "lesson": "When X, do Y.\nAlso Z."})
    assert answer is None and why is not None and "one rule" in why


def test_outcome_is_scratch_inside_the_threshold() -> None:
    assert pmm.outcome_of(D("0.05"), D("1")) == "scratch"
    assert pmm.outcome_of(D("-0.5"), D("-1")) == "loss"
    assert pmm.outcome_of(None, D("2")) == "win"
