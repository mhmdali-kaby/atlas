"""ATL-196: paper trades of never-executed proposals, the Strategist's daily score (average realised
R, Risk Engine rejection rate, Brier score with mean_p/observed, regret) and the Devil's Advocate's
(caught, false-alarm, low-rating win and miss rates), idempotent re-runs and the 00:15 hooks.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents.agents import scores as hooks
from atlas_agents.registry import discover_hooks
from atlas_agents.scorecards import devils_advocate as da
from atlas_agents.scorecards import paper
from atlas_agents.scorecards import strategist as strat
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import (
    AgentScore,
    Candle,
    Fill,
    JournalEntry,
    Order,
    PaperTrade,
    Proposal,
    RiskCheck,
)
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
RUN = uuid.uuid4().hex[:6].upper()
DAY = date(2097, 3, 10)  # Baghdad day of the proposals
T = datetime(2097, 3, 10, 6, 0, tzinfo=UTC)  # 09:00 Baghdad: proposals created / entries filled
AFTER = datetime(2097, 3, 11, 21, 15, tzinfo=UTC)  # 00:15 Baghdad on the 12th
FEE = Decimal("0.0005")  # backtest.taker_fee_rates.SWAP (instruments are unknown -> the fallback, same)
D = Decimal


def inst(name: str) -> str:
    return f"P{RUN}{name}-USDT-SWAP"


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with factory() as s:
            s.execute(delete(AgentScore).where(AgentScore.agent.in_([strat.AGENT, da.AGENT])))
            s.commit()
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _ctx(factory: sessionmaker[Session], now: datetime = AFTER, agent: str = strat.AGENT) -> ToolContext:
    return ToolContext(env=ENV, session_factory=factory, agent=agent, clock=lambda: now)


def _bar(inst_id: str, ts: datetime, o: str, h: str, lo: str, c: str) -> Candle:
    return Candle(
        env=ENV, inst_id=inst_id, bar="1m", ts=ts, o=D(o), h=D(h), l=D(lo), c=D(c), vol=D(1), confirmed=True
    )


# Bar 0 (T) opens at 100 and stays in 99.5-100.5; bar 1 decides.
STOP_BAR = ("99", "99.5", "94", "95")  # trades through the 95 stop
TP_BAR = ("101", "111", "100.5", "110")  # reaches the 110 target
BOTH_BAR = ("100", "111", "94", "100")  # touches both: stop first
FLAT_BAR = ("100", "101", "99", "100")


def _seed_bars(s: Session, inst_id: str, second: tuple[str, str, str, str], *, more: int = 0) -> None:
    s.add(_bar(inst_id, T, "100", "100.5", "99.5", "100"))
    s.add(_bar(inst_id, T + timedelta(minutes=1), *second))
    for k in range(more):
        s.add(_bar(inst_id, T + timedelta(minutes=2 + k), *FLAT_BAR))


@dataclass
class Seed:
    factory: sessionmaker[Session]

    def proposal(
        self,
        name: str,
        *,
        status: str,
        probability: str | None,
        order_type: str = "market",
        entry: str | None = None,
        status_reason: str | None = None,
        tps: list[dict[str, str]] | None = None,
        time_stop: int | None = None,
    ) -> uuid.UUID:
        with self.factory() as s:
            row = Proposal(
                env=ENV,
                kind="entry",
                author="strategist",
                inst_id=inst(name),
                side="buy",
                order_type=order_type,
                entry=D(entry) if entry else None,
                stop=D("95"),
                take_profits=tps or [{"price": "110", "pct": "100"}],
                time_stop_minutes=time_stop,
                expected_r=D("2"),
                probability=D(probability) if probability else None,
                status=status,
                status_reason=status_reason,
                created_at=T,
                updated_at=T,
            )
            s.add(row)
            s.commit()
            return row.id

    def risk(self, proposal_id: uuid.UUID, result: str, *, minutes: int = 1) -> uuid.UUID:
        with self.factory() as s:
            rc = RiskCheck(
                env=ENV,
                proposal_id=proposal_id,
                settings_version=1,
                result=result,
                checks=[],
                decision=None if result == "rejected" else "awaiting_owner",
                created_at=T + timedelta(seconds=minutes),
            )
            s.add(rc)
            s.commit()
            return rc.id

    def taken(self, proposal_id: uuid.UUID, name: str, realised_r: str) -> None:
        rc_id = self.risk(proposal_id, "approved")
        tag = uuid.uuid4().hex[:10]
        with self.factory() as s:
            order = Order(
                env=ENV,
                proposal_id=proposal_id,
                risk_check_id=rc_id,
                cl_ord_id=f"e{tag}",
                ord_id=f"o{tag}",
                inst_id=inst(name),
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
            s.add(order)
            s.flush()
            s.add(
                Fill(
                    env=ENV,
                    order_id=order.id,
                    trade_id=f"t{tag}",
                    ord_id=f"o{tag}",
                    inst_id=inst(name),
                    side="buy",
                    fill_px=D("100"),
                    fill_sz=D("1"),
                    ts=T,
                    created_at=T,
                )
            )
            s.add(
                JournalEntry(
                    env=ENV,
                    agent="portfolio_manager",
                    inst_id=inst(name),
                    proposal_id=proposal_id,
                    title="post-mortem",
                    data={"actual": {"realised_r": realised_r}, "outcome": "win"},
                    tags=[],
                    created_at=T + timedelta(hours=2),
                )
            )
            s.commit()

    def da_card(self, proposal_id: uuid.UUID, rating: int, *, minutes: int = 2) -> None:
        with self.factory() as s:
            thread = s.execute(
                text("SELECT id FROM warroom_threads WHERE env = :env AND key = :key"),
                {"env": ENV, "key": f"p{RUN.lower()}"},
            ).scalar()
            if thread is None:
                thread = uuid.uuid4()
                s.execute(
                    text(
                        "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                        "VALUES (:id, :env, 'agent', :key, :key, :ts, :ts)"
                    ),
                    {"id": thread, "env": ENV, "key": f"p{RUN.lower()}", "ts": T},
                )
            card = {"type": "da_review", "proposal_id": str(proposal_id), "risk_rating": rating}
            ts = T + timedelta(minutes=minutes)
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
                    "card": json.dumps(card),
                    "ts": ts,
                },
            )
            s.commit()


def _paper(factory: sessionmaker[Session], proposal_id: uuid.UUID) -> PaperTrade | None:
    with factory() as s:
        return s.scalars(select(PaperTrade).where(PaperTrade.proposal_id == proposal_id)).first()


def _score(factory: sessionmaker[Session], agent: str) -> list[AgentScore]:
    with factory() as s:
        return list(s.scalars(select(AgentScore).where(AgentScore.agent == agent, AgentScore.env == ENV)))


# -- AC1: paper trades ----------------------------------------------------------------------------------


def test_rejected_long_stopped_first_is_minus_one_r_with_exit_reason_stop(db: sessionmaker[Session]) -> None:
    seed = Seed(db)
    with db() as s:
        _seed_bars(s, inst("S"), STOP_BAR)
        s.commit()
    pid = seed.proposal("S", status="rejected", status_reason="risk_rejected", probability="0.6")
    start, end = datetime(2097, 3, 9, 21, tzinfo=UTC), datetime(2097, 3, 10, 21, tzinfo=UTC)
    with db() as s:
        out = paper.ensure_paper_trades(s, ENV, start, end, AFTER)
        s.commit()
    assert set(out) == {pid}
    row = _paper(db, pid)
    assert row is not None
    assert row.status == "filled"
    assert row.exit_reason == "stop"
    assert row.r_gross == D("-1")  # entry 100 (next 1m open), stop 95, risk 5
    assert row.fees_r == D("0.0195")  # 0.0005 x (100 + 95) / 5
    assert row.realised_r == D("-1.0195")
    assert row.entry_px == D("100") and row.exit_px == D("95")
    assert row.tp1_hit is False
    assert row.bar == "1m"
    assert row.proposal_status == "rejected" and row.proposal_status_reason == "risk_rejected"


def test_limit_entry_never_reached_is_stored_as_not_filled_not_as_a_loss(db: sessionmaker[Session]) -> None:
    seed = Seed(db)
    with db() as s:
        _seed_bars(s, inst("N"), FLAT_BAR, more=40)  # never below 99; entry timeout 30 min
        s.commit()
    pid = seed.proposal("N", status="rejected", probability="0.5", order_type="limit", entry="98")
    with db() as s:
        paper.ensure_paper_trades(s, ENV, T - timedelta(hours=1), T + timedelta(hours=1), AFTER)
        s.commit()
    row = _paper(db, pid)
    assert row is not None
    assert row.status == "not_filled" and row.exit_reason == "not_filled"
    assert row.realised_r is None and row.r_gross is None and row.tp1_hit is None


def test_a_bar_touching_both_stop_and_target_is_a_stop(db: sessionmaker[Session]) -> None:
    seed = Seed(db)
    with db() as s:
        _seed_bars(s, inst("B"), BOTH_BAR)
        s.commit()
    pid = seed.proposal("B", status="expired", probability="0.5")
    with db() as s:
        paper.ensure_paper_trades(s, ENV, T - timedelta(hours=1), T + timedelta(hours=1), AFTER)
        s.commit()
    row = _paper(db, pid)
    assert row is not None and row.exit_reason == "stop" and row.r_gross == D("-1")


def test_partial_targets_then_time_stop_weigh_each_leg_by_its_pct() -> None:
    bars = [
        paper.Bar(T + timedelta(minutes=k), T + timedelta(minutes=k + 1), D(o), D(h), D(lo), D(c))
        for k, (o, h, lo, c) in enumerate(
            [("100", "100.5", "99.5", "100"), ("101", "106", "100", "105"), ("105", "105", "104", "104")]
        )
    ]
    spec = paper.PaperSpec(
        side="buy",
        order_type="limit",
        limit_px=D("100"),
        stop=D("95"),
        take_profits=[paper.TakeProfit(D("105"), D("50")), paper.TakeProfit(D("115"), D("30"))],
        time_stop_minutes=3,
        decided_at=T,
    )
    # Bar 0 trades through 100 (low 99.5) but opens at 100 -> fills at the open, whole bar counts.
    res = paper.simulate(
        spec,
        bars,
        entry_timeout=timedelta(minutes=30),
        max_hold=timedelta(hours=72),
        fee_rate=D(0),
        now=AFTER,
    )
    assert res is not None and res.status == "filled"
    # TP1 50% at 105 = +0.5R; the other 50% closes at bar 2's close 104 at the 3-minute time stop = +0.4R.
    assert [(x.reason, x.price, x.pct) for x in res.legs] == [
        ("take_profit", D("105"), D("50")),
        ("time_stop", D("104"), D("50")),
    ]
    assert res.r_gross == D("0.9") and res.tp1_hit is True and res.exit_reason == "time_stop"


def test_an_open_window_writes_no_paper_trade(db: sessionmaker[Session]) -> None:
    seed = Seed(db)
    with db() as s:
        _seed_bars(s, inst("O"), FLAT_BAR)
        s.commit()
    pid = seed.proposal("O", status="advise_only", probability="0.5")
    with db() as s:
        out = paper.ensure_paper_trades(
            s, ENV, T - timedelta(hours=1), T + timedelta(hours=1), T + timedelta(hours=1)
        )
        s.commit()
    assert out == {} and _paper(db, pid) is None


# -- AC2/AC3/AC5: golden Strategist and Devil's Advocate days ------------------------------------------


def _seed_day(db: sessionmaker[Session]) -> dict[str, uuid.UUID]:
    """P1 taken +2R (TP1 first), P2 taken -1R (stopped), P3 risk-rejected paper stopped (-1.0195R),
    P4 vote-rejected limit never filled, P5 advise-only paper TP (+1.979R)."""
    seed = Seed(db)
    with db() as s:
        _seed_bars(s, inst("1"), TP_BAR)
        _seed_bars(s, inst("2"), STOP_BAR)
        _seed_bars(s, inst("3"), STOP_BAR)
        _seed_bars(s, inst("4"), FLAT_BAR, more=40)
        _seed_bars(s, inst("5"), TP_BAR)
        s.commit()
    ids = {
        "P1": seed.proposal("1", status="closed", probability="0.6"),
        "P2": seed.proposal("2", status="closed", probability="0.5"),
        "P3": seed.proposal("3", status="rejected", status_reason="risk_rejected", probability="0.7"),
        "P4": seed.proposal("4", status="rejected", probability="0.4", order_type="limit", entry="90"),
        "P5": seed.proposal("5", status="advise_only", probability="0.8"),
    }
    seed.taken(ids["P1"], "1", "2.0")
    seed.taken(ids["P2"], "2", "-1.0")
    seed.risk(ids["P3"], "rejected")
    seed.risk(ids["P5"], "approved")
    return ids


def test_strategist_golden_day(db: sessionmaker[Session]) -> None:
    ids = _seed_day(db)
    data = strat.score_strategist_day(_ctx(db), DAY)
    assert data is not None
    assert data["submitted_n"] == 5
    assert data["risk_rejected_n"] == 1 and data["rejection_rate"] == pytest.approx(0.2)
    assert data["taken_n"] == 2 and data["avg_realised_r"] == pytest.approx(0.5)  # (2 - 1) / 2
    # Brier on (p, TP1-before-stop): P1 (0.6, 1), P2 (0.5, 0), P3 (0.7, 0), P5 (0.8, 1); P4 not filled.
    assert data["brier_n"] == 4
    assert data["brier_score"] == pytest.approx((0.16 + 0.25 + 0.49 + 0.04) / 4)  # 0.235
    assert data["mean_p"] == pytest.approx(0.65)
    assert data["observed"] == pytest.approx(0.5)
    # Regret: P3 -1 - 0.0005 x 195 / 5 = -1.0195; P5 2 - 0.0005 x 210 / 5 = 1.979.
    assert data["regret_n"] == 2 and data["regret_r"] == pytest.approx((-1.0195 + 1.979) / 2)
    (row,) = _score(db, strat.AGENT)
    assert row.day == DAY and row.n == 5
    assert row.brier_score == D("0.235000")
    assert row.hit_rate == D("0.5000")
    assert row.data["mean_p"] == pytest.approx(0.65) and row.data["observed"] == pytest.approx(0.5)
    assert row.data["metrics"]["rejection_rate"] == pytest.approx(0.2)
    p4 = _paper(db, ids["P4"])
    assert p4 is not None and p4.status == "not_filled"
    with db() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(PaperTrade).where(PaperTrade.proposal_id.in_(ids.values()))
            )
            == 3
        )


def test_devils_advocate_golden_day_stores_false_alarm_and_miss_separately(db: sessionmaker[Session]) -> None:
    ids = _seed_day(db)
    seed = Seed(db)
    p6 = seed.proposal("6", status="closed", probability="0.55")
    seed.taken(p6, "6", "-0.6")
    with db() as s:
        _seed_bars(s, inst("6"), STOP_BAR)
        s.commit()
    seed.da_card(ids["P1"], 2)  # won  -> clean call right
    seed.da_card(ids["P2"], 1)  # lost -> miss
    seed.da_card(p6, 1)  # lost -> miss
    seed.da_card(ids["P3"], 2, minutes=2)  # round 1 ...
    seed.da_card(ids["P3"], 4, minutes=5)  # ... the newest re-rate wins: paper lost -> caught
    seed.da_card(ids["P5"], 5)  # paper won -> false alarm
    seed.da_card(ids["P4"], 4)  # not filled -> no outcome
    data = da.score_da_day(_ctx(db, agent=da.AGENT), DAY)
    assert data is not None
    assert (data["high_n"], data["high_lost"], data["high_won"]) == (2, 1, 1)
    assert data["caught_rate"] == pytest.approx(0.5)
    assert data["false_alarm_rate"] == pytest.approx(0.5)
    assert (data["low_n"], data["low_won"], data["low_lost"]) == (3, 1, 2)
    assert data["low_win_rate"] == pytest.approx(1 / 3)
    assert data["miss_rate"] == pytest.approx(2 / 3)
    assert data["rated_n"] == 5
    (row,) = _score(db, da.AGENT)
    assert row.n == 5 and row.hit_rate == D("0.4000")  # (1 caught + 1 clean win) / 5
    assert row.brier_score is None
    metrics: dict[str, Any] = row.data["metrics"]
    assert metrics["false_alarm_rate"] == pytest.approx(0.5)
    assert metrics["miss_rate"] == pytest.approx(2 / 3)
    assert row.data["pending"] == [str(ids["P4"])]


# -- AC4: idempotent, open day ------------------------------------------------------------------------


def test_running_both_jobs_twice_leaves_one_row_each_and_no_duplicate_paper_trades(
    db: sessionmaker[Session],
) -> None:
    ids = _seed_day(db)
    Seed(db).da_card(ids["P3"], 4)
    for _ in range(2):
        strat.score_days(_ctx(db), date(2097, 3, 12))
        da.score_days(_ctx(db, agent=da.AGENT), date(2097, 3, 12))
    with db() as s:
        counts = s.execute(
            select(AgentScore.agent, AgentScore.day, func.count())
            .where(AgentScore.agent.in_([strat.AGENT, da.AGENT]), AgentScore.env == ENV)
            .group_by(AgentScore.agent, AgentScore.day)
        ).all()
        papers = s.scalar(
            select(func.count()).select_from(PaperTrade).where(PaperTrade.proposal_id.in_(ids.values()))
        )
    assert sorted(counts) == sorted([(strat.AGENT, DAY, 1), (da.AGENT, DAY, 1)])
    assert papers == 3


def test_a_day_that_has_not_ended_writes_nothing(db: sessionmaker[Session]) -> None:
    _seed_day(db)
    assert strat.score_strategist_day(_ctx(db, T + timedelta(hours=3)), DAY) is None
    assert da.score_da_day(_ctx(db, T + timedelta(hours=3), da.AGENT), DAY) is None
    assert _score(db, strat.AGENT) == [] and _score(db, da.AGENT) == []


def test_scoring_hooks_are_registered_at_0015_for_both_agents() -> None:
    found = discover_hooks()
    for slug in (strat.AGENT, da.AGENT):
        (hook,) = [h for h in found[slug].schedule if h.name == "scoring"]
        assert hook.time_of_day().isoformat() == "00:15:00"


async def test_the_strategist_hook_scores_once_per_day(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_day(db)
    monkeypatch.setattr(hooks, "_scored", {})

    class Host:
        env = ENV
        replaying = False

        def tool_context(self, agent: str) -> ToolContext:
            return _ctx(db, AFTER, agent)

    from atlas_agents.registry import ScheduleFire

    fire = ScheduleFire(name="scoring", at=AFTER, day=date(2097, 3, 12), correlation_id="scoring:2097-03-12")
    assert await hooks.score_strategist(Host(), fire) is None  # type: ignore[arg-type]
    first = [(r.day, r.updated_at) for r in _score(db, strat.AGENT)]
    assert await hooks.score_strategist(Host(), fire) is None  # type: ignore[arg-type]
    assert first == [(r.day, r.updated_at) for r in _score(db, strat.AGENT)]
    assert [d for d, _ in first] == [DAY]
