"""ATL-169: the Portfolio Manager (brief §7.8). Runs use a FakeClaudeClient script; the world (an
account sync, one open position with its entry proposal, order, risk check and live stop, equity
points) is written here and removed after. The Risk Engine is a fake client that does what
trading_core's evaluate does to the row. Needs the checkout's test DB migrated: run
`cd backend && php artisan test` first.

NOW is far in the future so this world's account sync, equity and events are the newest.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import access
from atlas_agents import testing as fake
from atlas_agents.agents import portfolio_manager as hooks
from atlas_agents.orchestrator import approval
from atlas_agents.pm import guard, limits, manage
from atlas_agents.pm.rules import RuleAction
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    AlgoOrder,
    BalanceSnapshot,
    EquityPoint,
    Instrument,
    Order,
    PositionSnapshot,
    Proposal,
    RiskCheck,
)
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

NOW = datetime(2098, 3, 4, 9, 0, tzinfo=UTC)  # a Tuesday, 12:00 Baghdad
ENV = "demo"
SLUG = "portfolio_manager"
D = Decimal


# -- AC1: the access row -------------------------------------------------------------------------------


def test_portfolio_manager_row_cell_by_cell() -> None:
    by_server: dict[str, set[str]] = {}
    allowed = set(access.allowed_tools(SLUG))
    for name in allowed:
        _mcp, server, tool = name.split("__")
        by_server.setdefault(server, set()).add(tool)
    assert by_server["okx_market"] == {"get_mark_price", "get_candles"}
    assert by_server["atlas_data"] == {
        "get_account_summary",
        "get_positions",
        "get_open_orders",
        "get_settings_snapshot",
        "get_today_budget_left",
        "get_watchlist",
        "get_correlations",
    }
    assert "fetch_article" not in by_server["news"] and "web_search" not in by_server["news"]
    assert {"search_news", "get_story_cluster", "get_economic_calendar"} <= by_server["news"]
    assert by_server["history"] == {"volatility_forecast"}
    assert by_server["memory"] == {
        "read_journal",
        "read_notebook",
        "get_my_scores",
        "write_journal",
        "append_notebook",
    }
    assert "submit_proposal" in by_server["warroom"]
    assert "cast_vote" not in by_server["warroom"]
    assert "request_human_input" not in by_server["warroom"]
    assert "orchestration" not in by_server
    assert not {"WebSearch", "WebFetch"} & allowed


def test_registered_off_by_default_on_the_strong_model_with_a_5_minute_check() -> None:
    spec = REGISTRY[SLUG]
    assert not spec.enabled
    from atlas_shared.defaults import DEFAULTS

    assert spec.model == DEFAULTS["agents"]["models"]["strong"]
    found = discover_hooks()[SLUG]
    assert [(h.name, h.every_seconds) for h in found.schedule] == [("pm_check", 300)]
    assert set(found.on_event) == {hooks.NEWS_ALERT, hooks.FILL, hooks.SYSTEM_ALERT}


# -- the world ----------------------------------------------------------------------------------------


@dataclass
class FakeRisk:
    """trading_core's /internal/risk/evaluate: stores a risk check, moves the row to risk_checked."""

    engine: Engine
    calls: list[str] = field(default_factory=list)
    result: str = "approved"

    async def risk_evaluate(self, proposal_id: str) -> dict[str, Any]:
        self.calls.append(proposal_id)
        rid = uuid.uuid4()
        decision = None if self.result == "rejected" else "awaiting_owner"
        with Session(self.engine) as s:
            s.add(
                RiskCheck(
                    id=rid,
                    env=ENV,
                    proposal_id=uuid.UUID(proposal_id),
                    settings_version=1,
                    result=self.result,
                    checks=[],
                    decision=decision,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            s.execute(
                update(Proposal).where(Proposal.id == uuid.UUID(proposal_id)).values(status="risk_checked")
            )
            s.commit()
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "risk_check_id": str(rid),
            "result": self.result,
            "decision": decision,
            "reason": "position action",
            "checks": [],
        }


@dataclass
class World:
    engine: Engine
    inst: str
    entry_id: uuid.UUID
    clock: list[datetime]

    def now(self) -> datetime:
        return self.clock[0]

    def proposals(self, author: str = SLUG) -> list[Proposal]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(Proposal)
                    .where(Proposal.inst_id == self.inst, Proposal.author == author)
                    .order_by(Proposal.created_at)
                )
            )

    def position(self, mark: str) -> None:
        """The newest account sync: one long of 3 contracts at 100 with ``mark``."""
        taken = self.now() - timedelta(minutes=1)
        with Session(self.engine) as s:
            s.add(
                BalanceSnapshot(
                    env=ENV, account="trading", ccy="USDT", eq=D("1000000"), taken_at=taken, created_at=taken
                )
            )
            s.add(
                PositionSnapshot(
                    env=ENV,
                    inst_id=self.inst,
                    pos_side="net",
                    pos=D("3"),
                    avg_px=D("100"),
                    mark_px=D(mark),
                    upl=(D(mark) - D("100")) * 3,
                    mgn_mode="isolated",
                    c_time=NOW - timedelta(hours=2),
                    taken_at=taken,
                )
            )
            s.commit()

    def equity(self, at: datetime, value: str) -> None:
        with Session(self.engine) as s:
            s.add(EquityPoint(env=ENV, ts=at, total_eq_usd=D(value), created_at=at))
            s.commit()


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    _wipe_runs(engine)
    manage.reset()
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"P{tag}-USDT-SWAP"
    added_settings: uuid.UUID | None = None
    entry_id, rc_id, order_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
                    "t": NOW - timedelta(days=1),
                },
            )
    opened = NOW - timedelta(hours=2)
    with Session(engine) as s:
        s.add(
            Instrument(
                env=ENV,
                inst_id=inst,
                inst_type="SWAP",
                lot_sz=D("1"),
                min_sz=D("1"),
                tick_sz=D("0.1"),
                state="live",
                asset_class="crypto",
            )
        )
        s.add(
            Proposal(
                id=entry_id,
                env=ENV,
                kind="entry",
                author="strategist",
                inst_id=inst,
                side="buy",
                order_type="limit",
                entry=D("100"),
                stop=D("95"),
                take_profits=[{"price": "110", "pct": "50"}, {"price": "115", "pct": "50"}],
                time_stop_minutes=1440,
                status="filled",
                created_at=opened - timedelta(minutes=5),
                updated_at=opened,
            )
        )
        s.flush()
        s.add(
            RiskCheck(
                id=rc_id,
                env=ENV,
                proposal_id=entry_id,
                settings_version=1,
                result="approved",
                checks=[],
                decision="awaiting_owner",
                created_at=opened,
            )
        )
        s.flush()
        s.add(
            Order(
                id=order_id,
                env=ENV,
                proposal_id=entry_id,
                risk_check_id=rc_id,
                cl_ord_id=f"e{tag.lower()}",
                ord_id=f"o{tag}",
                inst_id=inst,
                side="buy",
                ord_type="limit",
                td_mode="isolated",
                px=D("100"),
                sz=D("3"),
                filled_sz=D("3"),
                state="filled",
                origin="atlas",
                purpose="entry",
                created_at=opened,
            )
        )
        s.flush()
        s.add(
            AlgoOrder(
                env=ENV,
                order_id=order_id,
                algo_id=f"a{tag}",
                algo_cl_ord_id=f"s{tag.lower()}",
                inst_id=inst,
                kind="sl",
                side="sell",
                trigger_px=D("95"),
                sz=D("3"),
                state="live",
                origin="atlas",
                created_at=opened,
            )
        )
        s.commit()
    clock = [NOW]
    yield World(engine, inst, entry_id, clock)
    with engine.begin() as conn:
        conn.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == inst))
        conn.execute(delete(Order).where(Order.inst_id == inst))
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst, Proposal.parent_id.is_not(None)))
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst))
        conn.execute(delete(Instrument).where(Instrument.inst_id == inst))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.inst_id == inst))
        conn.execute(
            delete(BalanceSnapshot).where(
                BalanceSnapshot.env == ENV, BalanceSnapshot.taken_at >= NOW - timedelta(days=2)
            )
        )
        conn.execute(
            delete(EquityPoint).where(EquityPoint.env == ENV, EquityPoint.ts >= NOW - timedelta(days=8))
        )
        if added_settings is not None:
            conn.execute(text("delete from settings where id = :id"), {"id": added_settings})
    _wipe_runs(engine)
    manage.set_risk_client(None)
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


def _answer(block: dict[str, Any], text: str = "BTC long +1.05R; thesis intact.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(block)}\n```"),
        fake.result(tokens_in=400, tokens_out=150, turns=1),
    ]


def _breakeven(inst: str, stop: str = "100.1") -> dict[str, Any]:
    return {
        "actions": [
            {
                "kind": "amend_stop",
                "rule": "breakeven",
                "stop": stop,
                "close_pct": None,
                "rationale_md": "+1R reached: lock in breakeven plus fees.",
            }
        ],
        "status_md": f"{inst} long, +1.05R.",
    }


def _messages(rec: Recorder) -> list[EventEnvelope]:
    return rec.of("warroom.message")


# -- AC2: the cheap path ------------------------------------------------------------------------------


async def test_a_tick_with_no_rule_firing_calls_no_model_and_posts_nothing(world: World) -> None:
    world.position(mark="101")  # +0.2R: nothing fires
    client, rec = fake.FakeClaudeClient(), Recorder()
    sup = _supervisor(world, client, rec)
    try:
        results = await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    assert results == []
    assert client.queries == []
    assert rec.events == []
    assert world.proposals() == []


# -- AC3: +1.05R -> one run, amend_stop to breakeven + fees ------------------------------------------


async def test_plus_1_05r_makes_one_run_that_submits_amend_stop_to_breakeven_plus_fees(world: World) -> None:
    world.position(mark="105.25")  # (105.25 - 100) / (100 - 95) = 1.05R
    risk = FakeRisk(world.engine)
    manage.set_risk_client(risk)  # type: ignore[arg-type]
    client, rec = fake.FakeClaudeClient([_answer(_breakeven(world.inst))]), Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    assert len(client.queries) == 1
    assert "breakeven" in client.queries[0] and "100.1" in client.queries[0]
    rows = world.proposals()
    assert len(rows) == 1
    row = rows[0]
    # 100 + 100 x 0.0005 x 2 = 100.1 (breakeven + round-trip taker fees)
    assert (row.kind, row.stop, row.invalidation) == ("amend_stop", D("100.1"), "breakeven")
    # The vote is skipped: straight to the Risk Engine, then autonomy (level 2 -> the owner).
    assert risk.calls == [str(row.id)]
    with Session(world.engine) as s:
        level = approval.autonomy_level(
            s, ToolContext(env=ENV, session_factory=sessionmaker(world.engine), agent=SLUG)
        )
    assert row.status == ("awaiting_owner" if level >= 2 else "advise_only")
    if level >= 2:
        assert row.approval_expires_at is not None
        assert len(rec.of("proposal.awaiting_owner")) == 1
    assert [e.payload["kind"] for e in _messages(rec)].count("proposal") == 1
    assert any(e.payload["kind"] == "risk_check" for e in _messages(rec))
    final = [e for e in _messages(rec) if e.payload["kind"] == "position_update"]
    assert len(final) == 1 and "Submitted **amend_stop** (breakeven)" in final[0].payload["content_md"]


async def test_a_scripted_widening_stop_is_refused_by_code(world: World) -> None:
    world.position(mark="105.25")
    manage.set_risk_client(FakeRisk(world.engine))  # type: ignore[arg-type]
    wide = _breakeven(world.inst, stop="94")  # below the 95 stop resting on OKX: wider
    client, rec = fake.FakeClaudeClient([_answer(wide), _answer(wide)]), Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    assert len(client.queries) == 2  # the answer and its one retry
    assert "widens_stop" in client.queries[1]
    stops = [p.stop for p in world.proposals()]
    assert D("94") not in stops
    # The rule is mandatory: code proposes the computed stop, once.
    assert stops == [D("100.1")]
    text_out = " ".join(e.payload["content_md"] for e in _messages(rec))
    assert "Refused by code: widens_stop" in text_out


async def test_the_tool_refuses_a_widening_stop_and_anything_not_computed(world: World) -> None:
    world.position(mark="105.25")
    ctx = ToolContext(
        env=ENV,
        session_factory=sessionmaker(world.engine),
        agent=SLUG,
        correlation_id="pm:test",
        clock=world.now,
        publish=Recorder(),
    )
    handler = fw.make_handler(fw.get_tool(fq_name("warroom", "submit_proposal")), ctx)

    async def call(proposal: dict[str, Any], kind: str = "amend_stop") -> dict[str, Any]:
        out = await handler({"kind": kind, "proposal": {"inst_id": world.inst, **proposal}})
        body: dict[str, Any] = json.loads(out["content"][0]["text"])
        return body

    computed = RuleAction("amend_stop", "breakeven", world.inst, "+1R", stop=D("100.1"))
    expected = guard.Expected(world.inst, "long", D("95"), D("105.25"), (computed,))
    with guard.expecting(ENV, "pm:test", expected):
        wide = await call({"stop": 94, "rule": "breakeven", "rationale_md": "x"})
        other = await call({"stop": 101, "rule": "breakeven", "rationale_md": "x"})
        close = await call({"rule": "time_stop", "rationale_md": "x"}, kind="close")
        entry = await handler({"proposal": {"inst_id": world.inst, "side": "buy"}})
    assert wide["error"] == "widens_stop"
    assert other["error"] == "not_computed_action"
    assert close["error"] == "not_computed_action"
    assert json.loads(entry["content"][0]["text"])["error"] == "kind_not_allowed"  # AC1: no entries
    assert world.proposals() == []


# -- AC4: news alerts ---------------------------------------------------------------------------------


def _alert(inst_ids: list[str]) -> EventEnvelope:
    return EventEnvelope(
        type="news.alert",
        ts=NOW,
        env=ENV,
        source="system:news",
        correlation_id="news:c1",
        payload={
            "news_item_id": str(uuid.uuid4()),
            "cluster_id": str(uuid.uuid4()),
            "headline": "Exchange hack",
            "importance": 5,
            "inst_ids": inst_ids,
            "held_inst_ids": [],
        },
    )


async def test_a_news_alert_on_a_held_instrument_starts_a_run(world: World) -> None:
    world.position(mark="101")  # no rule fires: the run is the alert's
    client = fake.FakeClaudeClient([_answer({"actions": [], "status_md": "Thesis intact."})])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.handle_envelope(_alert([world.inst, "BTC-USDT-SWAP"]))
        await hooks.settled()
    finally:
        await sup.close()
    assert len(client.queries) == 1
    assert "News alert" in client.queries[0] and world.inst in client.queries[0]
    assert world.proposals() == []


async def test_a_news_alert_on_an_unheld_instrument_starts_nothing(world: World) -> None:
    world.position(mark="101")
    client, rec = fake.FakeClaudeClient(), Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.handle_envelope(_alert(["NOTHELD-USDT-SWAP"]))
        await hooks.settled()
    finally:
        await sup.close()
    assert client.queries == []
    assert rec.events == []


# -- AC5: one limit warning per hour ------------------------------------------------------------------


async def test_daily_loss_at_72_pct_of_the_limit_warns_once_per_hour(world: World) -> None:
    world.position(mark="101")  # quiet position: only the limit speaks
    with Session(world.engine) as s:
        ctx = ToolContext(env=ENV, session_factory=sessionmaker(world.engine), agent=SLUG, clock=world.now)
        cap = limits.limit_values(s, ctx)["max_daily_loss_pct"]
    start = D("1000000")
    loss = start * cap * D("0.72") / 100  # 72 % of the daily limit
    day_start = datetime(2098, 3, 3, 21, 0, tzinfo=UTC)  # 00:00 Baghdad on the 4th
    world.equity(day_start + timedelta(minutes=1), str(start))
    world.equity(NOW - timedelta(minutes=2), str(start - loss))
    client, rec = fake.FakeClaudeClient(), Recorder()
    sup = _supervisor(world, client, rec)

    def warnings() -> list[EventEnvelope]:
        return [e for e in _messages(rec) if (e.payload.get("card") or {}).get("type") == "limit_warning"]

    try:
        for minutes in (0, 5, 10, 15, 55):
            world.clock[0] = NOW + timedelta(minutes=minutes)
            await manage.tick(sup)
        assert len(warnings()) == 1
        card = warnings()[0].payload["card"]
        assert card["limit"] == "max_daily_loss_pct" and card["usage_pct"] == "72.0"
        assert "daily loss limit is at 72%" in warnings()[0].payload["content_md"]
        world.clock[0] = NOW + timedelta(minutes=61)
        await manage.tick(sup)
        assert len(warnings()) == 2
    finally:
        await sup.close()
    assert client.queries == []  # warnings are code, not model runs


def test_limit_usage_is_a_percentage_of_sizing_equity(world: World) -> None:
    """Decision #250 (ATL-156 review): with a 10,000 cap and a 2 % daily limit, a 150 USD loss on a
    1,000,000 account is 1.5 % of sizing equity = 75 % of the limit, so the 70 % warning fires."""
    day_start = datetime(2098, 3, 3, 21, 0, tzinfo=UTC)  # 00:00 Baghdad on the 4th
    world.equity(day_start + timedelta(minutes=1), "1000000")
    world.equity(NOW - timedelta(minutes=2), "999850")
    with world.engine.begin() as conn:
        before = conn.execute(text("select data from settings where env = :e"), {"e": ENV}).scalar_one()
        conn.execute(
            text("update settings set data = data || cast(:d as jsonb) where env = :e"),
            {"d": json.dumps({"bot_capital_cap_usdt": "10000", "max_daily_loss_pct": "2"}), "e": ENV},
        )
    try:
        ctx = ToolContext(env=ENV, session_factory=sessionmaker(world.engine), agent=SLUG, clock=world.now)
        with Session(world.engine) as s:
            daily = {u.limit: u for u in limits.usage(s, ctx)}["max_daily_loss_pct"]
            assert daily.used == Decimal("1.5") and daily.usage_pct == Decimal(75)
            cfg = {"limit_warning_pct": 70, "limit_warning_interval_minutes": 60}
            assert "max_daily_loss_pct" in {w.limit for w in limits.due_warnings(s, ctx, cfg)}
    finally:
        with world.engine.begin() as conn:
            conn.execute(
                text("update settings set data = cast(:d as jsonb) where env = :e"),
                {"d": json.dumps(before), "e": ENV},
            )


async def test_a_risk_engine_refusal_rejects_the_proposal_with_the_reason(world: World) -> None:
    from atlas_agents.orchestrator.trading_core import TradingCoreError

    class Refusing:
        async def risk_evaluate(self, proposal_id: str) -> dict[str, Any]:
            raise TradingCoreError("risk_engine_error", "only entries are risk-checked here")

    world.position(mark="105.25")
    manage.set_risk_client(Refusing())  # type: ignore[arg-type]
    client, rec = fake.FakeClaudeClient([_answer(_breakeven(world.inst))]), Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(NOW)
        await hooks.settled()
    finally:
        await sup.close()
    [row] = world.proposals()
    assert (row.status, row.status_reason) == ("rejected", "risk_risk_engine_error")
    assert any("risk_engine_error" in e.payload["content_md"] for e in _messages(rec))


def test_the_in_process_memory_forgets_closed_positions_and_past_events() -> None:
    manage.reset()
    manage._advised[(ENV, "HELD")] = NOW
    manage._advised[(ENV, "GONE")] = NOW
    manage._pre_event[(ENV, "HELD", "future")] = NOW + timedelta(minutes=20)
    manage._pre_event[(ENV, "HELD", "past")] = NOW - timedelta(minutes=1)
    manage._pre_event[(ENV, "GONE", "future")] = NOW + timedelta(minutes=20)
    manage._pre_event[("live", "GONE", "future")] = NOW + timedelta(minutes=20)  # other env untouched
    manage._prune(ENV, {"HELD"}, NOW)
    assert set(manage._advised) == {(ENV, "HELD")}
    assert set(manage._pre_event) == {(ENV, "HELD", "future"), ("live", "GONE", "future")}
    manage.reset()
