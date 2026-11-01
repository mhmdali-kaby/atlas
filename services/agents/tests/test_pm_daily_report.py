"""ATL-170: the Portfolio Manager's 23:30 daily report (brief §7.8). Figures are computed in code from
the day's bills, closed positions and equity points written here; runs use a FakeClaudeClient script.
Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.

The Baghdad day is far in the future so this world's equity points and syncs are the newest.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import testing as fake
from atlas_agents.agents import portfolio_manager as hooks
from atlas_agents.pm import daily_report as dr
from atlas_agents.pm import manage
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    AlgoOrder,
    Bill,
    EquityPoint,
    PositionClosed,
    Proposal,
    ResearchReport,
)
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

DAY = date(2099, 6, 10)  # a Wednesday
START = datetime(2099, 6, 9, 21, 0, tzinfo=UTC)  # 00:00 Baghdad
NOW = datetime(2099, 6, 10, 20, 30, tzinfo=UTC)  # 23:30 Baghdad
ENV = "demo"
SLUG = "portfolio_manager"
D = Decimal


@dataclass
class World:
    engine: Engine
    tag: str
    clock: list[datetime]

    def now(self) -> datetime:
        return self.clock[0]

    def inst(self, n: int) -> str:
        return f"R{self.tag}{n}-USDT-SWAP"

    def ctx(self) -> ToolContext:
        return ToolContext(env=ENV, session_factory=sessionmaker(self.engine), agent=SLUG, clock=self.now)

    def equity(self, at: datetime, value: str, upl: str | None = None) -> None:
        with Session(self.engine) as s:
            s.add(
                EquityPoint(
                    env=ENV,
                    ts=at,
                    total_eq_usd=D(value),
                    upl_usd=None if upl is None else D(upl),
                    created_at=at,
                )
            )
            s.commit()

    def bill(
        self,
        at: datetime,
        inst: str | None,
        type_: str,
        bal_chg: str,
        pnl: str | None = None,
        fee: str | None = None,
        ccy: str = "USDT",
    ) -> None:
        with Session(self.engine) as s:
            s.add(
                Bill(
                    env=ENV,
                    bill_id=f"b{self.tag}{uuid.uuid4().hex[:12]}",
                    inst_id=inst,
                    ccy=ccy,
                    type=type_,
                    bal_chg=D(bal_chg),
                    pnl=None if pnl is None else D(pnl),
                    fee=None if fee is None else D(fee),
                    ts=at,
                    created_at=at,
                )
            )
            s.commit()

    def closed(
        self,
        inst: str,
        *,
        side: str,
        entry: str,
        exit_px: str,
        pnl: str,
        opened: datetime,
        closed: datetime,
        proposal_id: uuid.UUID | None = None,
    ) -> None:
        with Session(self.engine) as s:
            s.add(
                PositionClosed(
                    env=ENV,
                    pos_id=f"p{self.tag}{uuid.uuid4().hex[:8]}",
                    inst_id=inst,
                    inst_type="SWAP",
                    mgn_mode="isolated",
                    pos_side="net",
                    side=side,
                    close_type="2",
                    size=D("2"),
                    entry_px=D(entry),
                    exit_px=D(exit_px),
                    realised_pnl=D(pnl),
                    pnl=D(pnl),
                    opened_at=opened,
                    closed_at=closed,
                    proposal_id=proposal_id,
                    origin="atlas" if proposal_id else "external",
                    created_at=closed,
                )
            )
            s.commit()

    def proposal(self, inst: str, **values: Any) -> uuid.UUID:
        pid = uuid.uuid4()
        at = values.pop("at", START)
        with Session(self.engine) as s:
            s.add(Proposal(id=pid, env=ENV, inst_id=inst, created_at=at, updated_at=at, **values))
            s.commit()
        return pid

    def reports(self) -> list[ResearchReport]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(ResearchReport).where(
                        ResearchReport.env == ENV,
                        ResearchReport.kind == dr.KIND,
                        ResearchReport.data["day"].astext == DAY.isoformat(),
                    )
                )
            )


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    _wipe_runs(engine)
    manage.reset()
    tag = uuid.uuid4().hex[:6].upper()
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
                    "data": json.dumps({"risk_profile": "balanced"}),
                    "t": START,
                },
            )
    w = World(engine, tag, [NOW])
    yield w
    lo, hi = START - timedelta(days=2), NOW + timedelta(days=2)
    with engine.begin() as conn:
        conn.execute(delete(ResearchReport).where(ResearchReport.env == ENV, ResearchReport.kind == dr.KIND))
        conn.execute(delete(Bill).where(Bill.env == ENV, Bill.bill_id.like(f"b{tag}%")))
        conn.execute(delete(PositionClosed).where(PositionClosed.pos_id.like(f"p{tag}%")))
        conn.execute(delete(AlgoOrder).where(AlgoOrder.inst_id.like(f"R{tag}%")))
        conn.execute(delete(Proposal).where(Proposal.inst_id.like(f"R{tag}%")))
        conn.execute(
            delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts >= lo, EquityPoint.ts <= hi)
        )
        if added_settings is not None:
            conn.execute(text("delete from settings where id = :id"), {"id": added_settings})
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


def _answer(block: dict[str, Any], text: str = "Quiet day; equity steady.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(block)}\n```"),
        fake.result(tokens_in=500, tokens_out=120, turns=1),
    ]


def _reports_posted(rec: Recorder) -> list[EventEnvelope]:
    return [
        e
        for e in rec.of("warroom.message")
        if e.payload.get("kind") == "research_report"
        and (e.payload.get("card") or {}).get("type") == "daily_report"
    ]


def _three_trade_day(w: World) -> dict[str, Any]:
    """Three closed trades, their opening/closing bills, funding, and bills outside the day."""
    w.equity(START - timedelta(minutes=1), "100000", "0")
    w.equity(START + timedelta(hours=10), "100150.25", "12.5")
    w.equity(NOW - timedelta(minutes=1), "100230.40", "-7.75")
    a, b, c = w.inst(1), w.inst(2), w.inst(3)
    entry_a = w.proposal(
        a,
        kind="entry",
        author="strategist",
        side="buy",
        order_type="limit",
        entry=D("100"),
        stop=D("95"),
        status="filled",
    )
    # A: long 100 -> 110 with a 95 stop = +2R, closed by the PM's time stop.
    w.proposal(
        a, kind="close", author=SLUG, status="filled", invalidation="time_stop", at=START + timedelta(hours=5)
    )
    w.closed(
        a,
        side="long",
        entry="100",
        exit_px="110",
        pnl="20.00",
        opened=START + timedelta(hours=1),
        closed=START + timedelta(hours=5),
        proposal_id=entry_a,
    )
    # B: short, stopped out on OKX.
    with Session(w.engine) as s:
        s.add(
            AlgoOrder(
                env=ENV,
                algo_id=f"a{w.tag}",
                algo_cl_ord_id=f"s{w.tag.lower()}",
                inst_id=b,
                kind="sl",
                side="buy",
                trigger_px=D("51"),
                state="effective",
                origin="atlas",
                created_at=START + timedelta(hours=2),
                updated_at=START + timedelta(hours=8),
            )
        )
        s.commit()
    w.closed(
        b,
        side="short",
        entry="50",
        exit_px="51",
        pnl="-12.34",
        opened=START + timedelta(hours=2),
        closed=START + timedelta(hours=8),
    )
    # C: closed by hand.
    w.closed(
        c,
        side="long",
        entry="10",
        exit_px="10.5",
        pnl="4.56",
        opened=START + timedelta(hours=3),
        closed=START + timedelta(hours=12),
    )
    # Bills: opening fees, closing pnl + fees, funding paid and received.
    w.bill(START + timedelta(hours=1), a, "2", "-0.10", pnl="0", fee="-0.10")
    w.bill(START + timedelta(hours=5), a, "2", "20.0005", pnl="20.1105", fee="-0.11")
    w.bill(START + timedelta(hours=2), b, "2", "-0.05", pnl="0", fee="-0.05")
    w.bill(START + timedelta(hours=8), b, "2", "-12.391", pnl="-12.338", fee="-0.053")
    w.bill(START + timedelta(hours=3), c, "2", "-0.01", pnl="0", fee="-0.01")
    w.bill(START + timedelta(hours=12), c, "2", "4.552", pnl="4.5655", fee="-0.0135")
    w.bill(START + timedelta(hours=8, minutes=1), a, "8", "-1.2345")
    w.bill(START + timedelta(hours=16, minutes=1), b, "8", "0.4321")
    # Outside the day (yesterday, tomorrow) and a non-USD bill: never summed.
    w.bill(START - timedelta(minutes=5), a, "2", "-100", pnl="-99", fee="-1")
    w.bill(NOW + timedelta(hours=2), a, "8", "-50")
    w.bill(START + timedelta(hours=9), None, "2", "-0.5", pnl="1", fee="-0.5", ccy="BTC")
    realised = D("0") + D("20.1105") + D("0") + D("-12.338") + D("0") + D("4.5655")
    fees = D("-0.10") + D("-0.11") + D("-0.05") + D("-0.053") + D("-0.01") + D("-0.0135")
    funding = D("-1.2345") + D("0.4321")
    return {"realised": realised, "fees": fees, "funding": funding, "a": a, "b": b, "c": c}


def _cents(value: Decimal) -> str:
    return format(value.quantize(D("0.01")), "f")


# -- AC1: realised, fees and funding are the sums of the day's bills -------------------------------


def test_a_three_trade_day_sums_realised_fees_and_funding_from_the_bills_to_the_cent(world: World) -> None:
    expected = _three_trade_day(world)
    with Session(world.engine) as s:
        report = dr.compute(s, world.ctx(), DAY)
    card = report.card
    assert card["pnl"]["realised_usd"] == _cents(expected["realised"]) == "12.34"
    assert card["pnl"]["fees_usd"] == _cents(expected["fees"]) == "-0.34"
    assert card["pnl"]["funding_usd"] == _cents(expected["funding"]) == "-0.80"
    assert card["pnl"]["net_realised_usd"] == _cents(
        expected["realised"] + expected["fees"] + expected["funding"]
    )
    assert card["pnl"]["unrealised_usd"] == "-7.75"
    assert "BTC" in " ".join(card["notes"])
    # Equity and the day's change.
    assert card["equity"] == {
        "start_usd": "100000.00",
        "end_usd": "100230.40",
        "net_flows_usd": "0.00",
        "change_usd": "230.40",
        "change_pct": "0.23",
    }
    # Closed trades: R, USD, holding time and the closing rule.
    assert card["trade_count"] == 3
    by_inst = {t["inst_id"]: t for t in card["trades"]}
    a, b, c = by_inst[expected["a"]], by_inst[expected["b"]], by_inst[expected["c"]]
    assert (a["side"], a["r"], a["pnl_usd"], a["holding_minutes"], a["closing_rule"]) == (
        "long",
        "2.00",
        "20.00",
        240,
        "time_stop",
    )
    assert (b["pnl_usd"], b["closing_rule"], b["r"]) == ("-12.34", "stop_loss", None)  # external: no stop
    assert (c["closing_rule"], c["holding_minutes"]) == ("manual", 540)
    assert card["best_trade"]["inst_id"] == expected["a"]
    assert card["worst_trade"]["inst_id"] == expected["b"]
    # No loss below the start equity: no breach.
    assert card["limits"]["peak_daily_loss_pct"] == "0.00"
    assert card["breaches"] == []


def test_registered_as_the_23_30_schedule_hook() -> None:
    found = discover_hooks()[SLUG]
    daily = [h for h in found.schedule if h.name == dr.JOB]
    assert len(daily) == 1 and daily[0].daily
    assert daily[0].time_of_day().strftime("%H:%M") == "23:30"


# -- AC2: the model cannot change a figure ---------------------------------------------------------


async def test_a_model_reply_that_alters_realised_pnl_is_overwritten_by_the_code_figure(world: World) -> None:
    _three_trade_day(world)
    forged = {
        "best_note": "Time stop banked +2R on the long.",
        "worst_note": "The short was stopped at the 51 level.",
        "pnl": {"realised_usd": "99999.00", "fees_usd": "0"},
        "equity": {"change_usd": "50000"},
        "trade_count": 9,
    }
    client = fake.FakeClaudeClient([_answer(forged, "Realised PnL today was $777.77.")])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    assert len(client.queries) == 1
    assert '"realised_usd": "12.34"' in client.queries[0]
    posted = _reports_posted(rec)
    assert len(posted) == 1
    card = posted[0].payload["card"]
    assert card["pnl"]["realised_usd"] == "12.34"
    assert card["pnl"]["fees_usd"] == "-0.34"
    assert card["equity"]["change_usd"] == "230.40"
    assert card["trade_count"] == 3
    assert card["best_trade"]["note"] == "Time stop banked +2R on the long."
    assert card["worst_trade"]["note"] == "The short was stopped at the 51 level."
    # The invented number in the text is flagged by the number guard.
    assert card["meta"]["unmatched_numbers"], card["meta"]
    stored = world.reports()
    assert len(stored) == 1
    assert stored[0].data["pnl"]["realised_usd"] == "12.34"
    assert stored[0].agent_run_id is not None
    # Done for the day: a later tick posts nothing more.
    rec2 = Recorder()
    sup2 = _supervisor(world, fake.FakeClaudeClient(), rec2)
    try:
        world.clock[0] = NOW + timedelta(minutes=5)
        await sup2.tick(world.now())
        await hooks.settled()
    finally:
        await sup2.close()
    assert _reports_posted(rec2) == []
    assert len(world.reports()) == 1


# -- AC3: a breach adds a row and raises a system alert --------------------------------------------


async def test_a_daily_loss_above_the_limit_adds_a_breach_row_and_publishes_a_system_alert(
    world: World,
) -> None:
    world.equity(START - timedelta(minutes=1), "100000")
    world.equity(START + timedelta(hours=6), "95500")  # -4.5% against the balanced 4% limit
    world.equity(NOW - timedelta(minutes=1), "97000")
    client = fake.FakeClaudeClient([_answer({"best_note": None, "worst_note": None})])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    card = _reports_posted(rec)[0].payload["card"]
    assert card["limits"]["peak_daily_loss_pct"] == "4.50"
    assert card["breaches"] == [
        {"limit": "max_daily_loss_pct", "used": "4.50", "cap": "4", "at": "2099-06-10T03:00:00Z"}
    ]
    alerts = [e for stream, e in rec.events if stream == "atlas.system" and e.type == "system.alert"]
    assert len(alerts) == 1
    assert alerts[0].payload["code"] == "limit_breach"
    assert alerts[0].payload["limit"] == "max_daily_loss_pct"
    room = [e for e in rec.of("warroom.message") if e.payload["kind"] == "system_alert"]
    assert len(room) == 1 and "daily loss limit" in room[0].payload["content_md"]


# -- AC4: a no-trade day still posts ---------------------------------------------------------------


async def test_a_no_trade_day_posts_a_report_with_zero_trades_and_the_equity_change(world: World) -> None:
    world.equity(START - timedelta(minutes=1), "250000")
    world.equity(NOW - timedelta(minutes=2), "249875.50", "-124.50")
    client = fake.FakeClaudeClient([_answer({"best_note": None, "worst_note": None}, "No trades today.")])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    posted = _reports_posted(rec)
    assert len(posted) == 1
    card = posted[0].payload["card"]
    assert card["trade_count"] == 0 and card["trades"] == []
    assert card["best_trade"] is None and card["worst_trade"] is None
    assert card["pnl"]["realised_usd"] == "0.00"
    assert card["pnl"]["fees_usd"] == "0.00"
    assert card["pnl"]["funding_usd"] == "0.00"
    assert card["equity"]["change_usd"] == "-124.50"
    assert card["equity"]["change_pct"] == "-0.05"
    assert card["breaches"] == []
    assert len(world.reports()) == 1


async def test_code_posts_the_report_when_the_model_answer_fails_twice(world: World) -> None:
    world.equity(START - timedelta(minutes=1), "1000")
    world.equity(NOW - timedelta(minutes=2), "1010")
    bad = [fake.assistant_text("no block"), fake.result(tokens_in=10, tokens_out=5)]
    client = fake.FakeClaudeClient([bad, bad])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    posted = _reports_posted(rec)
    assert len(posted) == 1
    assert posted[0].payload["card"]["equity"]["change_usd"] == "10.00"
    assert "No trades closed today." in posted[0].payload["content_md"]
    assert len(world.reports()) == 1
