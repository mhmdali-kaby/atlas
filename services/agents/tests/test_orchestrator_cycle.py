"""ATL-160: the Orchestrator's analysis cycle state machine (brief §7.9, §8).

Runs on a simulated clock: ``SimClock.sleep``/``wait`` move simulated time instead of waiting, so a
5-minute research timeout takes milliseconds. Research runs go through the fake SDK (one
FakeClaudeClient per agent session); the Strategist, the Devil's Advocate and the voters are stood
in for by rows written here; trading_core is an ``httpx.MockTransport`` that records every request.
Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from atlas_agents import testing as fake
from atlas_agents.agents import orchestrator as hooks
from atlas_agents.orchestrator import cycle
from atlas_agents.orchestrator.trading_core import TradingCoreClient
from atlas_agents.registry import REGISTRY, ScheduleFire, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    Cycle,
    Proposal,
    Vote,
    Watchlist,
    WatchlistItem,
)
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
BAGHDAD = ZoneInfo("Asia/Baghdad")
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
AGENTS = ("orchestrator", "technical_analyst", "derivatives_sentiment", "strategist")
VOTERS = ("technical_analyst", "derivatives_sentiment", "news_intelligence", "historian", "devils_advocate")


def baghdad(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=BAGHDAD).astimezone(UTC)


# -- the simulated clock -------------------------------------------------------------------------------


@dataclass
class SimClock:
    t: datetime

    def now(self) -> datetime:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
        await asyncio.sleep(0.01)

    async def wait(self, tasks: set[asyncio.Task[Any]], seconds: float) -> None:
        # Real tasks (fake SDK runs, DB threads) get up to 3 real seconds; anything still running is
        # late, and simulated time jumps to the deadline.
        _done, pending = await asyncio.wait(tasks, timeout=3.0) if tasks else (set(), set())
        self.t += timedelta(seconds=seconds if pending else 1)


# -- trading_core stand-in ------------------------------------------------------------------------------


@dataclass
class FakeTradingCore:
    engine: Engine
    decision: str = "awaiting_owner"
    result: str = "approved"
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        pid = request.url.path.rsplit("/", 1)[-1]
        # What evaluate() does to the row: status risk_checked.
        with self.engine.begin() as c:
            c.execute(update(Proposal).where(Proposal.id == uuid.UUID(pid)).values(status="risk_checked"))
        body = {
            "ok": True,
            "proposal_id": pid,
            "risk_check_id": str(uuid.uuid4()),
            "env": ENV,
            "settings_version": 1,
            "result": self.result,
            "decision": None if self.result == "rejected" else self.decision,
            "reason": "All nine checks passed.",
            "failed_check": None,
            "binding_cap": "strategist_suggestion",
            "final_contracts": "3",
            "final_notional_usd": "300",
            "leverage": 2,
            "checks": [],
        }
        return httpx.Response(200, json=body)

    def client(self) -> TradingCoreClient:
        return TradingCoreClient(
            base_url="http://trading-core.test", secret=SECRET, transport=httpx.MockTransport(self.handler)
        )

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


# -- the DB world --------------------------------------------------------------------------------------


@dataclass
class World:
    engine: Engine
    day: date
    inst: str
    thread_id: uuid.UUID
    rec: Recorder = field(default_factory=Recorder)
    clients: list[fake.FakeClaudeClient] = field(default_factory=list)

    def factory(self) -> Any:
        def make(options: Any) -> fake.FakeClaudeClient:
            run: list[Any] = [
                fake.assistant_text("Research done."),
                fake.result(tokens_in=10, tokens_out=5, turns=1),
            ]
            client = fake.FakeClaudeClient([run] * 20, options)
            self.clients.append(client)
            return client

        return make

    def supervisor(self, clock: SimClock) -> Supervisor:
        registry = {slug: dataclasses.replace(REGISTRY[slug], enabled=True) for slug in AGENTS}
        return Supervisor(
            env=ENV,
            engine=self.engine,
            registry=registry,
            client_factory=self.factory(),
            publisher=self.rec,
            clock=clock.now,
        )

    def set_autonomy(self, level: int) -> None:
        with self.engine.begin() as c:
            c.execute(
                text(
                    "UPDATE settings SET autonomy_level = :l, data = data || "
                    "jsonb_build_object('autonomy_level', :l, 'min_expected_r', '1.8', "
                    "'max_instruments_per_day', 5) WHERE env = :e"
                ),
                {"l": level, "e": ENV},
            )

    def set_state(self, agent: str, state: str, error: str | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO agent_states (id, agent, env, state, last_error, state_since, created_at, "
                    "updated_at) VALUES (:id, :a, :e, :s, :err, now(), now(), now()) "
                    "ON CONFLICT (agent, env) DO UPDATE SET state = EXCLUDED.state, "
                    "last_error = EXCLUDED.last_error"
                ),
                {"id": uuid.uuid4(), "a": agent, "e": ENV, "s": state, "err": error},
            )

    def propose(self, cycle_id: str, at: datetime) -> uuid.UUID:
        pid = uuid.uuid4()
        with Session(self.engine) as s:
            s.add(
                Proposal(
                    id=pid,
                    env=ENV,
                    cycle_id=uuid.UUID(cycle_id),
                    version=1,
                    kind="entry",
                    author="strategist",
                    inst_id=self.inst,
                    side="buy",
                    order_type="limit",
                    entry=Decimal(100),
                    stop=Decimal(96),
                    take_profits=[{"price": "110", "pct": "100"}],
                    size_suggestion_pct=Decimal(2),
                    leverage_suggestion=2,
                    expected_r=Decimal("2.5"),
                    probability=Decimal("0.55"),
                    status="under_review",
                    created_at=at,
                    updated_at=at,
                )
            )
            s.commit()
        return pid

    def review_and_vote(self, pid: uuid.UUID, at: datetime, rating: int = 2) -> None:
        with self.engine.begin() as c:
            c.execute(
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
                    "card": json.dumps({"type": "da_review", "proposal_id": str(pid), "risk_rating": rating}),
                    "ts": at,
                },
            )
        with Session(self.engine) as s:
            for voter in VOTERS:
                s.add(
                    Vote(
                        env=ENV,
                        proposal_id=pid,
                        agent=voter,
                        vote="take",
                        confidence=Decimal("0.8"),
                        reason="ok",
                        created_at=at,
                        updated_at=at,
                    )
                )
            s.commit()

    def cycles(self) -> list[Cycle]:
        with Session(self.engine) as s:
            return list(
                s.scalars(
                    select(Cycle).where(
                        Cycle.env == ENV,
                        Cycle.started_at >= baghdad(self.day, 0, 0),
                        Cycle.started_at < baghdad(self.day, 0, 0) + timedelta(days=1),
                    )
                )
            )

    def proposal(self, pid: uuid.UUID) -> Proposal:
        with Session(self.engine) as s:
            row = s.get(Proposal, pid)
            assert row is not None
            return row


def _wipe_agents(engine: Engine) -> None:
    with engine.begin() as c:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            c.execute(delete(model).where(model.agent.in_(AGENTS), model.env == ENV))


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    # A far-future day of its own, so this world's watchlist and cycles are the only ones on it.
    day = date(2099, 1, 1) + timedelta(days=uuid.uuid4().int % 3000)
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"O{tag}-USDT-SWAP"
    thread_id = uuid.uuid4()
    _wipe_agents(engine)
    with engine.begin() as c:
        saved = c.execute(
            text("SELECT data, autonomy_level FROM settings WHERE env = :e"), {"e": ENV}
        ).first()
        added = None
        if saved is None:
            added = uuid.uuid4()
            c.execute(
                text(
                    "INSERT INTO settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "VALUES (:id, :e, 1, cast(:d as jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                ),
                {"id": added, "e": ENV, "d": json.dumps({"risk_profile": "balanced", "autonomy_level": 2})},
            )
        c.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :e, 'agent', :k, :k, now(), now())"
            ),
            {"id": thread_id, "e": ENV, "k": f"o{tag}"},
        )
    at = baghdad(day, 7, 0)
    with Session(engine) as s:
        wl = Watchlist(
            env=ENV,
            date=day,
            version=1,
            created_by="market_scout",
            status="published",
            data={},
            created_at=at,
            updated_at=at,
        )
        s.add(wl)
        s.flush()
        s.add(
            WatchlistItem(
                watchlist_id=wl.id,
                inst_id=inst,
                rank=1,
                asset_class="crypto",
                bias="long",
                conviction=Decimal("0.6"),
                thesis="t",
                catalysts=[],
                key_levels={},
                invalidation="i",
                data_as_of=at,
            )
        )
        s.commit()
    w = World(engine, day, inst, thread_id)
    w.set_autonomy(2)
    yield w
    with engine.begin() as c:
        ids = [r.id for r in w.cycles()]
        c.execute(delete(Proposal).where(Proposal.inst_id == inst, Proposal.parent_id.is_not(None)))
        c.execute(delete(Proposal).where(Proposal.inst_id == inst))
        if ids:
            c.execute(delete(Cycle).where(Cycle.id.in_(ids)))
        c.execute(delete(Watchlist).where(Watchlist.env == ENV, Watchlist.date == day))
        c.execute(text("DELETE FROM warroom_messages WHERE thread_id = :id"), {"id": thread_id})
        c.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})
        if added is not None:
            c.execute(text("DELETE FROM settings WHERE id = :id"), {"id": added})
        elif saved is not None:
            c.execute(
                text("UPDATE settings SET data = cast(:d as jsonb), autonomy_level = :l WHERE env = :e"),
                {"d": json.dumps(saved[0]), "l": saved[1], "e": ENV},
            )
    _wipe_agents(engine)


def _host_research(slug: str) -> cycle.ResearchFn:
    """A research run through the fake SDK, like the analysts' analyse() does."""

    async def run(host: Any, inst_id: str, *, trigger: Any, correlation_id: str | None, why: str) -> Any:
        return await host.run(
            slug,
            f"{why} ({inst_id})",
            trigger=trigger,
            task=f"{slug} {inst_id}",
            correlation_id=correlation_id,
        )

    return run


def _runner(
    sup: Supervisor, clock: SimClock, tc: FakeTradingCore, strategist: Any, **research: Any
) -> cycle.CycleRunner:
    fns = {
        "technical_analyst": _host_research("technical_analyst"),
        "derivatives_sentiment": _host_research("derivatives_sentiment"),
        **research,
    }
    return cycle.CycleRunner(sup, clock=clock, research=fns, strategist=strategist, risk=tc.client())


async def _run(runner: cycle.CycleRunner, kind: str = "manual") -> uuid.UUID:
    cid = runner.create(kind)
    assert cid is not None
    await runner.run(cid)
    return cid


def _row(world: World, cid: uuid.UUID) -> Cycle:
    with Session(world.engine) as s:
        row = s.get(Cycle, cid)
        assert row is not None
        return row


# -- AC1: slots 08:10, 08:40 start; 23:10 does not ------------------------------------------------------


def test_due_slot_every_30_min_from_0810_to_2300() -> None:
    day = date(2099, 6, 1)
    assert cycle.due_slot(baghdad(day, 8, 9)) is None
    assert cycle.due_slot(baghdad(day, 8, 10)) == baghdad(day, 8, 10)
    assert cycle.due_slot(baghdad(day, 8, 39)) == baghdad(day, 8, 10)
    assert cycle.due_slot(baghdad(day, 8, 40)) == baghdad(day, 8, 40)
    assert cycle.due_slot(baghdad(day, 22, 59)) == baghdad(day, 22, 40)
    assert cycle.due_slot(baghdad(day, 23, 10)) is None


def test_orchestrator_registers_the_schedule_and_cycle_now_hooks() -> None:
    got = discover_hooks()["orchestrator"]
    assert [h.name for h in got.schedule] == [hooks.SCHEDULE_JOB]
    assert hooks.CYCLE_NOW_EVENT in got.on_event


async def test_a_simulated_clock_starts_cycles_at_0810_and_0840_but_not_2310(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[uuid.UUID] = []
    monkeypatch.setattr(cycle, "launch", lambda runner, cid: launched.append(cid))
    clock = SimClock(baghdad(world.day, 8, 10))
    sup = world.supervisor(clock)
    for hh, mm in ((8, 10), (8, 12), (8, 40), (23, 10)):
        clock.t = baghdad(world.day, hh, mm)
        fire = ScheduleFire(name=hooks.SCHEDULE_JOB, at=clock.t, day=world.day, correlation_id="x")
        await hooks.on_schedule(sup, fire)
    rows = sorted(world.cycles(), key=lambda r: r.started_at)
    assert [r.slot_at for r in rows] == [baghdad(world.day, 8, 10), baghdad(world.day, 8, 40)]
    assert {r.kind for r in rows} == {"scheduled"}
    assert len(launched) == 2  # 08:12 is the 08:10 slot again: not started twice
    await sup.close()


# -- AC2: a full cycle walks every state in order and stamps each -------------------------------------


async def test_a_full_cycle_walks_every_state_in_order_and_stamps_each(world: World) -> None:
    clock = SimClock(baghdad(world.day, 8, 10))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine)
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = world.propose(cycle_id, clock.now())
        world.review_and_vote(pid, clock.now())
        made.append(pid)

    cid = await _run(_runner(sup, clock, tc, strategist))

    row = _row(world, cid)
    expected = [
        "cycle_start",
        "research_requests_sent",
        "research_collected",
        "proposals_open",
        "reviews_done",
        "voting",
        "vote_closed",
        "risk_check",
        "awaiting_owner",
        "cycle_closed",
    ]
    assert set(row.transitions) == set(expected)  # jsonb keeps no key order; events give the order
    stamps = [datetime.fromisoformat(row.transitions[s]) for s in expected]
    assert stamps == sorted(stamps)
    assert row.state == "cycle_closed" and row.outcome == "awaiting_owner" and row.closed_at is not None
    assert row.timeouts == []
    assert set(row.deadlines) == {"research", "review", "vote"}
    # every research agent ran once through the fake SDK
    assert sum(len(c.queries) for c in world.clients) == 2
    # every transition is an event and a system line
    states = [e.payload["state"] for e in world.rec.of("cycle.state_changed")]
    assert states == expected
    lines = [
        e
        for e in world.rec.of("warroom.message")
        if e.payload["author_type"] == "system" and e.payload["card"]["type"] == "cycle_state"
    ]
    assert [e.payload["card"]["state"] for e in lines] == expected
    assert all(e.source == "system:orchestrator" for e in lines)
    assert world.proposal(made[0]).status == "awaiting_owner"
    await sup.close()


# -- AC3: Derivatives never answers -> research times out at 5 min, the cycle goes on --------------------


async def test_research_times_out_at_5_min_without_derivatives_and_the_cycle_continues(world: World) -> None:
    clock = SimClock(baghdad(world.day, 8, 40))
    start = clock.t
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine)
    never = asyncio.Event()
    strategist_called: list[str] = []

    async def silent(host: Any, inst_id: str, **_: Any) -> None:
        await never.wait()

    async def strategist(host: Any, cycle_id: str) -> None:
        strategist_called.append(cycle_id)

    runner = _runner(sup, clock, tc, strategist, derivatives_sentiment=silent)
    cid = await _run(runner)

    row = _row(world, cid)
    assert row.deadlines["research"] == (start + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert datetime.fromisoformat(row.transitions["research_collected"]) == start + timedelta(minutes=5)
    assert [(t["step"], t["agent"], t["inst_ids"]) for t in row.timeouts] == [
        ("research", "derivatives_sentiment", [world.inst])
    ]
    assert strategist_called == [str(cid)]  # the cycle continued to the Strategist
    assert row.outcome == "no_proposals"
    collected = [e for e in world.rec.of("cycle.state_changed") if e.payload["state"] == "research_collected"]
    assert collected[0].payload["timed_out"] == ["derivatives_sentiment"]
    never.set()
    await asyncio.gather(*runner.late, return_exceptions=True)
    await sup.close()


# -- AC4: one risk check; autonomy 2 -> awaiting_owner + expiry; autonomy 1 -> advise_only ----------------


@pytest.mark.parametrize(("autonomy", "decision"), [(2, "awaiting_owner"), (1, "advise_only")])
async def test_a_passed_vote_calls_the_risk_check_once_and_never_the_executor(
    world: World, autonomy: int, decision: str
) -> None:
    world.set_autonomy(autonomy)
    clock = SimClock(baghdad(world.day, 9, 10))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine, decision=decision)
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = world.propose(cycle_id, clock.now())
        world.review_and_vote(pid, clock.now())
        made.append(pid)

    cid = await _run(_runner(sup, clock, tc, strategist))

    (pid,) = made
    assert tc.paths == [f"/internal/risk/evaluate/{pid}"]  # exactly one call, and no /internal/execute
    req = tc.requests[0]
    assert req.method == "POST" and {"x-atlas-signature", "x-atlas-nonce", "x-atlas-timestamp"} <= set(
        req.headers
    )
    row = world.proposal(pid)
    risk_at = datetime.fromisoformat(_row(world, cid).transitions["risk_check"])
    if autonomy == 2:
        assert row.status == "awaiting_owner"
        assert row.approval_expires_at is not None
        assert row.approval_expires_at == risk_at + timedelta(minutes=15)
        (ev,) = world.rec.of("proposal.awaiting_owner")
        assert ev.payload["approval_expires_at"] and ev.payload["proposal_id"] == str(pid)
    else:
        assert row.status == "advise_only" and row.approval_expires_at is None
        assert world.rec.of("proposal.awaiting_owner") == []
        assert _row(world, cid).outcome == "advise_only"
        assert "awaiting_owner" not in _row(world, cid).transitions
    cards = [e for e in world.rec.of("warroom.message") if e.payload["kind"] == "risk_check"]
    assert len(cards) == 1 and cards[0].payload["card"]["type"] == "risk_check"
    await sup.close()


async def test_autonomy_1_wins_even_if_the_risk_engine_says_awaiting_owner(world: World) -> None:
    world.set_autonomy(1)
    clock = SimClock(baghdad(world.day, 9, 40))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine, decision="awaiting_owner")
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = world.propose(cycle_id, clock.now())
        world.review_and_vote(pid, clock.now())
        made.append(pid)

    await _run(_runner(sup, clock, tc, strategist))
    assert world.proposal(made[0]).status == "advise_only"
    await sup.close()


async def test_a_risk_rejection_closes_the_proposal_rejected(world: World) -> None:
    clock = SimClock(baghdad(world.day, 10, 10))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine, result="rejected")
    made: list[uuid.UUID] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = world.propose(cycle_id, clock.now())
        world.review_and_vote(pid, clock.now())
        made.append(pid)

    cid = await _run(_runner(sup, clock, tc, strategist))
    assert world.proposal(made[0]).status == "rejected"
    assert _row(world, cid).outcome == "risk_rejected"
    await sup.close()


async def test_a_failed_vote_never_reaches_the_risk_engine(world: World) -> None:
    clock = SimClock(baghdad(world.day, 10, 40))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine)

    async def strategist(host: Any, cycle_id: str) -> None:
        pid = world.propose(cycle_id, clock.now())
        world.review_and_vote(pid, clock.now(), rating=5)  # DA rating 5 fails the vote

    cid = await _run(_runner(sup, clock, tc, strategist))
    assert tc.requests == []
    assert _row(world, cid).outcome == "nothing_passed"
    await sup.close()


async def test_a_missing_da_review_times_out_at_2_min_and_is_recorded(world: World) -> None:
    clock = SimClock(baghdad(world.day, 11, 10))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine)

    async def strategist(host: Any, cycle_id: str) -> None:
        world.propose(cycle_id, clock.now())  # no review, no votes

    cid = await _run(_runner(sup, clock, tc, strategist))
    row = _row(world, cid)
    opened = datetime.fromisoformat(row.transitions["proposals_open"])
    assert datetime.fromisoformat(row.transitions["reviews_done"]) == opened + timedelta(minutes=2)
    steps = [(t["step"], t["agent"]) for t in row.timeouts]
    assert ("review", "devils_advocate") in steps
    assert [a for s, a in steps if s == "vote"] == list(VOTERS)
    assert row.outcome == "nothing_passed" and tc.requests == []
    await sup.close()


# -- AC5: Strategist degraded -> closes after research with "no new proposals" and the reason -----------


async def test_a_degraded_strategist_closes_the_cycle_after_research(world: World) -> None:
    world.set_state("strategist", "degraded", "invalid output twice")
    clock = SimClock(baghdad(world.day, 12, 10))
    sup = world.supervisor(clock)
    tc = FakeTradingCore(world.engine)
    called: list[str] = []

    async def strategist(host: Any, cycle_id: str) -> None:
        called.append(cycle_id)

    cid = await _run(_runner(sup, clock, tc, strategist))
    row = _row(world, cid)
    assert called == []
    assert set(row.transitions) == {
        "cycle_start",
        "research_requests_sent",
        "research_collected",
        "cycle_closed",
    }
    assert row.outcome == "no_new_proposals"
    assert row.outcome_reason == "no new proposals: strategist is degraded: invalid output twice"
    last = world.rec.of("warroom.message")[-1]
    assert (
        "no new proposals" in last.payload["content_md"]
        and "strategist is degraded" in last.payload["content_md"]
    )
    await sup.close()


async def test_cycle_now_starts_a_manual_cycle(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[uuid.UUID] = []
    monkeypatch.setattr(cycle, "launch", lambda runner, cid: launched.append(cid))
    clock = SimClock(baghdad(world.day, 13, 0))
    sup = world.supervisor(clock)
    from atlas_shared.events import EventEnvelope

    env = EventEnvelope(type=hooks.CYCLE_NOW_EVENT, env=ENV, source="system:warroom", payload={})
    await hooks.on_cycle_now(sup, env)
    rows = world.cycles()
    assert len(launched) == 1 and [r.kind for r in rows] == ["manual"] and rows[0].slot_at is None
    await sup.close()
