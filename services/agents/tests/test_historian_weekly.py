"""ATL-199: the Historian's weekly review (brief §7.3), every Sunday 18:00 Baghdad.

Figures come from journal_entries post-mortems seeded here; runs use a FakeClaudeClient script.
Needs the checkout's test DB migrated: run `cd backend && php artisan test` first. The week is in
2097 so no other test's journal entries or reports fall inside it.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import historian, historian_weekly
from atlas_agents import testing as fake
from atlas_agents.registry import REGISTRY, ScheduleHook, discover_hooks, parse_schedule
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import AgentRun, AgentScore, AgentState, AgentUsageDaily, JournalEntry, ResearchReport
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

SUNDAY = date(2097, 6, 16)
MONDAY = date(2097, 6, 10)
SUN_1800 = datetime(2097, 6, 16, 15, 0, tzinfo=UTC)  # 18:00 Baghdad (UTC+3)
ENV = "demo"
SLUG = historian.SLUG
D = Decimal


def _baghdad(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC) - timedelta(hours=3)


@dataclasses.dataclass
class World:
    engine: Engine
    tag: str
    clock: list[datetime]

    def now(self) -> datetime:
        return self.clock[0]

    def ctx(self) -> ToolContext:
        return ToolContext(env=ENV, session_factory=sessionmaker(self.engine), agent=SLUG, clock=self.now)

    def post_mortem(
        self,
        *,
        setup: str,
        asset: str,
        author: str,
        outcome: str,
        r: str | None,
        usd: str,
        closed_at: datetime,
        agent: str = "portfolio_manager",
    ) -> None:
        pid = uuid.uuid4()
        data = {
            "env": ENV,
            "inst_id": f"W{self.tag}-USDT-SWAP",
            "asset_class": asset,
            "proposal_id": str(pid),
            "position_ref": f"{uuid.uuid4().hex[:8]}:{closed_at.isoformat()}",
            "outcome": outcome,
            "expected": {"author": author, "stop": "95"},
            "actual": {
                "realised_r": r,
                "realised_pnl_usd": usd,
                "closed_at": closed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "tags": {
                "setup": setup,
                "asset": asset,
                "regime": "trend",
                "catalyst": "none",
                "outcome": outcome,
            },
        }
        with Session(self.engine) as s:
            s.add(
                JournalEntry(
                    env=ENV,
                    agent=agent,
                    inst_id=f"W{self.tag}-USDT-SWAP",
                    proposal_id=pid,
                    title=f"W{self.tag} {outcome}",
                    data=data,
                    tags=[f"setup:{setup}", f"asset:{asset}", f"outcome:{outcome}", outcome],
                    created_at=closed_at + timedelta(minutes=3),
                    updated_at=closed_at + timedelta(minutes=3),
                )
            )
            s.commit()

    def score(self, agent: str, day: date, n: int, hit_rate: str) -> None:
        with Session(self.engine) as s:
            s.add(
                AgentScore(
                    env=ENV,
                    agent=agent,
                    day=day,
                    n=n,
                    hit_rate=D(hit_rate),
                    data={"metrics": {"avg_realised_r": 0.5}, "proposals": [{"id": "x"}]},
                    created_at=SUN_1800,
                    updated_at=SUN_1800,
                )
            )
            s.commit()

    def reports(self) -> list[ResearchReport]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(ResearchReport).where(
                        ResearchReport.env == ENV,
                        ResearchReport.kind == historian_weekly.KIND,
                        ResearchReport.data["week_start"].astext == MONDAY.isoformat(),
                    )
                )
            )


def _wipe(engine: Engine, tag: str | None = None) -> None:
    lo, hi = date(2097, 1, 1), date(2097, 12, 31)
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))
        conn.execute(
            delete(ResearchReport).where(
                ResearchReport.env == ENV, ResearchReport.kind == historian_weekly.KIND
            )
        )
        conn.execute(
            delete(JournalEntry).where(
                JournalEntry.env == ENV,
                JournalEntry.created_at >= datetime(2097, 1, 1, tzinfo=UTC),
                JournalEntry.created_at < datetime(2098, 1, 1, tzinfo=UTC),
            )
        )
        conn.execute(delete(AgentScore).where(AgentScore.env == ENV, AgentScore.day.between(lo, hi)))


@pytest.fixture
def world(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    async def no_regime_table(ctx: Any, top_n: int | None = None) -> None:
        return None

    monkeypatch.setattr(historian, "build_regime_table", no_regime_table)  # the 07:30 hook stays quiet
    _wipe(engine)
    w = World(engine, uuid.uuid4().hex[:6].upper(), [SUN_1800])
    yield w
    _wipe(engine)


def _supervisor(w: World, client: fake.FakeClaudeClient, rec: Recorder) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)
    return Supervisor(
        env=ENV,
        engine=w.engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=w.now,
    )


def _reviews(rec: Recorder) -> list[EventEnvelope]:
    return [
        e
        for e in rec.of("warroom.message")
        if e.payload.get("kind") == "research_report"
        and (e.payload.get("card") or {}).get("kind") == historian_weekly.KIND
    ]


def _answer(block: dict[str, Any], body: str = "Breakouts carried the week.") -> list[Any]:
    return [
        fake.assistant_text(f"{body}\n\n```json\n{json.dumps(block)}\n```"),
        fake.result(tokens_in=400, tokens_out=100, turns=1),
    ]


ONE = {
    "verdict": "History weakly supports the process: breakout setups made 0.50R over 2 trades.",
    "suggested_changes": [{"change": "Raise min R for breakout setups to 2.0", "reason": "n is 2"}],
}
TWO = {
    "verdict": "History weakly supports the process.",
    "suggested_changes": [
        {"change": "Raise min R for breakout setups to 2.0", "reason": "n is 2"},
        {"change": "Block XAU on Fridays", "reason": "one loss"},
    ],
}


def _seed_week(w: World) -> None:
    # In the window (Mon 00:00 Baghdad .. Sun 18:00):
    w.post_mortem(
        setup="breakout",
        asset="crypto",
        author="strategist",
        outcome="win",
        r="1.50",
        usd="30.00",
        closed_at=_baghdad(MONDAY, 0, 5),
    )
    w.post_mortem(
        setup="breakout",
        asset="commodity",
        author="strategist",
        outcome="loss",
        r="-1.00",
        usd="-20.55",
        closed_at=_baghdad(date(2097, 6, 12), 14),
    )
    w.post_mortem(
        setup="pullback_to_support",
        asset="crypto",
        author="strategist",
        outcome="win",
        r="2.25",
        usd="45.10",
        closed_at=_baghdad(date(2097, 6, 14), 9),
    )
    w.post_mortem(
        setup="pullback_to_support",
        asset="stock",
        author="portfolio_manager",
        outcome="scratch",
        r=None,
        usd="0.40",
        closed_at=_baghdad(SUNDAY, 17, 30),
    )
    # Outside: the Sunday before, after 18:00 today, and a journal entry that is not a PM post-mortem.
    w.post_mortem(
        setup="breakout",
        asset="crypto",
        author="strategist",
        outcome="win",
        r="3.00",
        usd="99.00",
        closed_at=_baghdad(date(2097, 6, 9), 23, 59),
    )
    w.post_mortem(
        setup="breakout",
        asset="crypto",
        author="strategist",
        outcome="loss",
        r="-1.00",
        usd="-9.00",
        closed_at=_baghdad(SUNDAY, 18, 1),
    )
    w.post_mortem(
        setup="breakout",
        asset="crypto",
        author="strategist",
        outcome="win",
        r="5.00",
        usd="500.00",
        closed_at=_baghdad(date(2097, 6, 13), 9),
        agent="historian",
    )


# -- the schedule ---------------------------------------------------------------------------------


def test_parse_schedule_reads_daily_and_weekly_forms() -> None:
    assert parse_schedule("07:30") == (None, time(7, 30))
    assert parse_schedule("Sun 18:00") == (6, time(18, 0))
    assert parse_schedule("monday 09:15") == (0, time(9, 15))
    with pytest.raises(ValueError):
        parse_schedule("Funday 18:00")
    with pytest.raises(ValueError):
        parse_schedule("Sun 18:00 extra")


def test_registered_as_the_sunday_18_00_hook() -> None:
    assert DEFAULTS["agents"]["schedule"][historian_weekly.JOB] == "Sun 18:00"
    (hook,) = [h for h in discover_hooks()[SLUG].schedule if h.name == historian_weekly.JOB]
    assert isinstance(hook, ScheduleHook) and hook.daily
    assert hook.weekday() == 6 and hook.time_of_day() == time(18, 0)


async def _noop(host: Any, fire: Any) -> None:
    return None


def test_due_only_on_the_weekday_from_its_time(engine: Engine) -> None:
    sup = Supervisor(
        env=ENV, engine=engine, registry={}, client_factory=fake.factory(fake.FakeClaudeClient())
    )
    hook = ScheduleHook(name="x", run=_noop, at="Sun 18:00")
    at, wd = hook.time_of_day(), hook.weekday()
    assert sup.due("x", SUN_1800, at, wd) == SUNDAY
    assert sup.due("x", SUN_1800 - timedelta(minutes=1), at, wd) is None  # Sun 17:59
    assert sup.due("x", SUN_1800 - timedelta(days=1), at, wd) is None  # Sat 18:00
    assert sup.due("x", _baghdad(SUNDAY, 23, 59), at, wd) == SUNDAY
    assert sup.due("x", _baghdad(date(2097, 6, 17), 18), at, wd) is None  # Monday


async def test_the_hook_fires_once_on_sunday_18_00_and_never_on_saturday_or_at_17_59(world: World) -> None:
    client = fake.FakeClaudeClient([])  # a no-trade week: no model call at all
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        for at in (SUN_1800 - timedelta(days=1), SUN_1800 - timedelta(minutes=1)):
            world.clock[0] = at
            await sup.tick(at)
        assert _reviews(rec) == [] and world.reports() == []
        world.clock[0] = SUN_1800
        await sup.tick(SUN_1800)
        assert len(_reviews(rec)) == 1
        later = SUN_1800 + timedelta(minutes=30)
        world.clock[0] = later
        await sup.tick(later)  # a second tick the same Sunday adds nothing
    finally:
        await sup.close()
    assert len(_reviews(rec)) == 1
    assert len(world.reports()) == 1
    assert client.queries == []


# -- the figures ----------------------------------------------------------------------------------


def test_golden_per_setup_asset_and_agent_figures(world: World) -> None:
    _seed_week(world)
    world.score("strategist", date(2097, 6, 13), 3, "0.5")
    world.score("strategist", date(2097, 6, 15), 4, "0.75")
    world.score("strategist", date(2097, 6, 17), 9, "0.1")  # after the review day: ignored
    with Session(world.engine) as s:
        card = historian_weekly.compute(s, world.ctx(), SUNDAY, SUN_1800)
    assert card["week_start"] == "2097-06-10" and card["day"] == "2097-06-16"
    assert card["window"] == {"from": "2097-06-09T21:00:00Z", "to": "2097-06-16T15:00:00Z"}
    assert card["trade_count"] == 4 and card["no_trades"] is False
    assert card["by_setup"] == [
        {
            "key": "breakout",
            "count": 2,
            "wins": 1,
            "losses": 1,
            "scratches": 0,
            "total_r": "0.50",
            "r_n": 2,
            "total_usd": "9.45",
        },
        {
            "key": "pullback_to_support",
            "count": 2,
            "wins": 1,
            "losses": 0,
            "scratches": 1,
            "total_r": "2.25",
            "r_n": 1,
            "total_usd": "45.50",
        },
    ]
    assert card["by_asset_class"] == [
        {
            "key": "commodity",
            "count": 1,
            "wins": 0,
            "losses": 1,
            "scratches": 0,
            "total_r": "-1.00",
            "r_n": 1,
            "total_usd": "-20.55",
        },
        {
            "key": "crypto",
            "count": 2,
            "wins": 2,
            "losses": 0,
            "scratches": 0,
            "total_r": "3.75",
            "r_n": 2,
            "total_usd": "75.10",
        },
        {
            "key": "stock",
            "count": 1,
            "wins": 0,
            "losses": 0,
            "scratches": 1,
            "total_r": None,
            "r_n": 0,
            "total_usd": "0.40",
        },
    ]
    assert [(g["key"], g["count"], g["total_r"], g["total_usd"]) for g in card["by_agent"]] == [
        ("portfolio_manager", 1, None, "0.40"),
        ("strategist", 3, "2.75", "54.55"),
    ]
    assert card["totals"] == {
        "count": 4,
        "wins": 2,
        "losses": 1,
        "scratches": 1,
        "total_r": "2.75",
        "r_n": 3,
        "total_usd": "54.95",
    }
    scores = {s["agent"]: s for s in card["agent_scores"]}
    assert scores["strategist"]["day"] == "2097-06-15" and scores["strategist"]["n"] == 4
    assert scores["strategist"]["hit_rate"] == "0.75"
    assert scores["strategist"]["metrics"] == {"avg_realised_r": 0.5}
    with Session(world.engine) as s:
        has_paper = s.execute(text("select to_regclass('paper_trades')")).scalar() is not None
    assert ("paper_trades" in card) is has_paper  # the section only exists with ATL-196's table


async def test_the_card_carries_the_golden_figures_and_one_suggested_change(world: World) -> None:
    _seed_week(world)
    client = fake.FakeClaudeClient([_answer(ONE)])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        await sup.tick(SUN_1800)
    finally:
        await sup.close()
    assert len(client.queries) == 1 and '"total_usd": "9.45"' in client.queries[0]
    (posted,) = _reviews(rec)
    card = posted.payload["card"]
    assert card["type"] == card["kind"] == "weekly_review" and card["status"] == "ok"
    assert card["by_setup"][0]["total_r"] == "0.50" and card["by_setup"][0]["total_usd"] == "9.45"
    assert card["suggestion"] == {"change": "Raise min R for breakout setups to 2.0", "reason": "n is 2"}
    assert card["verdict"].startswith("History weakly supports")
    assert card["meta"]["unmatched_numbers"] == [], card["meta"]
    assert "**Suggested change:** Raise min R" in posted.payload["content_md"]
    (stored,) = world.reports()
    assert stored.agent == SLUG and stored.data["week_start"] == "2097-06-10"
    assert stored.data["suggestion"]["change"].startswith("Raise min R")
    assert stored.agent_run_id is not None


async def test_two_suggestions_are_retried_once_then_one_is_accepted(world: World) -> None:
    _seed_week(world)
    client = fake.FakeClaudeClient([_answer(TWO), _answer(ONE)])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        (result,) = await sup.tick(SUN_1800)
    finally:
        await sup.close()
    assert result.status == "ok" and len(client.queries) == 2
    assert "exactly one" in client.queries[1]
    (posted,) = _reviews(rec)
    assert posted.payload["card"]["suggestion"]["change"] == "Raise min R for breakout setups to 2.0"


async def test_two_suggestions_twice_degrade_the_historian_and_code_posts_the_figures(world: World) -> None:
    _seed_week(world)
    client = fake.FakeClaudeClient([_answer(TWO), _answer(TWO)])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        (result,) = await sup.tick(SUN_1800)
        await sup.tick(SUN_1800 + timedelta(minutes=1))
    finally:
        await sup.close()
    assert result.status == "degraded" and result.error and "2 changes" in result.error
    with Session(world.engine) as s:
        state = s.scalars(select(AgentState).where(AgentState.agent == SLUG, AgentState.env == ENV)).one()
    assert state.state == "degraded"
    (posted,) = _reviews(rec)  # code's card, never one carrying two suggestions
    card = posted.payload["card"]
    assert card["suggestion"] is None and card["verdict"] is None and card["status"] == "degraded"
    assert card["totals"]["total_usd"] == "54.95"
    assert len(world.reports()) == 1 and len(client.queries) == 2


def test_parse_answer_requires_exactly_one_change() -> None:
    assert isinstance(historian_weekly.parse_answer(ONE), tuple)
    assert "2 changes" in str(historian_weekly.parse_answer(TWO))
    assert "exactly one" in str(historian_weekly.parse_answer({**ONE, "suggested_changes": []}))
    assert "verdict" in str(historian_weekly.parse_answer({"suggested_changes": ONE["suggested_changes"]}))


async def test_a_week_without_closed_trades_posts_the_code_card_with_no_model_call(world: World) -> None:
    client = fake.FakeClaudeClient([])
    rec = Recorder()
    sup = _supervisor(world, client, rec)
    try:
        results = await sup.tick(SUN_1800)
    finally:
        await sup.close()
    assert results == [] and client.queries == []
    (posted,) = _reviews(rec)
    card = posted.payload["card"]
    assert card["no_trades"] is True and card["trade_count"] == 0 and card["status"] == "no_trades"
    assert card["suggestion"] is None
    assert "No closed trades this week." in posted.payload["content_md"]
    assert posted.payload["author"] == SLUG
    with Session(world.engine) as s:
        runs = s.scalars(select(AgentRun).where(AgentRun.agent == SLUG, AgentRun.env == ENV)).all()
    assert runs == []
    (stored,) = world.reports()
    assert stored.agent_run_id is None and stored.data["week_start"] == "2097-06-10"
