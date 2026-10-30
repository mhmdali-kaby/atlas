"""ATL-158: the Strategist (brief §7.6). Runs use a FakeClaudeClient script; the DB world (watchlist,
research cards, settings, a cycle) is written here. Needs the checkout's test DB migrated: run
`cd backend && php artisan test` first.

The world is set far in the future (NOW) so its account sync and watchlist are the newest, and every
row it adds names its own instrument.
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
from atlas_agents import access
from atlas_agents import testing as fake
from atlas_agents.agents import strategist as hooks
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.strategist import inputs as inp
from atlas_agents.strategist import sizing
from atlas_agents.strategist.rules import apply_with
from atlas_agents.supervisor import Supervisor
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    Proposal,
    Watchlist,
    WatchlistItem,
    owner_day,
)
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

NOW = datetime(2097, 5, 6, 9, 0, tzinfo=UTC)
ENV = "demo"
SLUG = "strategist"
D = Decimal


# -- AC1: the access row --------------------------------------------------------------------------------


def test_strategist_row_has_submit_proposal_but_not_cast_vote() -> None:
    allowed = set(access.allowed_tools(SLUG))
    by_server: dict[str, set[str]] = {}
    for name in allowed:
        _mcp, server, tool = name.split("__")
        by_server.setdefault(server, set()).add(tool)
    assert by_server["okx_market"] == {"get_mark_price", "get_instrument_spec"}
    assert by_server["history"] == {"backtest_rule", "find_analogs"}
    assert by_server["memory"] == {"read_journal", "read_notebook", "get_my_scores"}
    assert "fetch_article" not in by_server["news"] and "web_search" not in by_server["news"]
    assert "submit_proposal" in by_server["warroom"]
    assert "cast_vote" not in by_server["warroom"]
    assert not {"WebSearch", "WebFetch"} & allowed


def test_disabled_by_default_and_registers_a_mention_handler() -> None:
    assert not REGISTRY[SLUG].enabled
    assert discover_hooks()[SLUG].on_mention is hooks.answer_mention


# -- AC2: sizing guidance golden cases --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("conviction", "percentile", "expected"),
    [
        (D("0.6"), D("70"), D("2.8125")),  # AC2: 5 x 0.75 x 0.75
        (D("0.5"), D("30"), D("2.5")),  # 5 x 0.5 x 1.0
        (D("0.55"), D("60"), D("2.8125")),  # both lower bounds are in the middle band
        (D("0.7"), D("85"), D("2.8125")),  # both upper bounds too
        (D("0.71"), D("85.1"), D("2.5")),  # 5 x 1.0 x 0.5
        (D("0.9"), D("10"), D("5")),
        (None, None, D("1.25")),  # unknowns take the cautious multipliers
    ],
)
def test_size_suggestion_golden(
    conviction: Decimal | None, percentile: Decimal | None, expected: Decimal
) -> None:
    assert sizing.size_suggestion(D("5"), conviction, percentile).size_pct == expected


def test_lowest_leverage_that_fits() -> None:
    # 2.8125% of 1,000,000 = 28,125 notional; 10,000 available x 0.9 = 9,000 -> 28,125 / 9,000 -> 4
    assert sizing.lowest_leverage(D("2.8125"), D("1000000"), D("10000"), 5, D("100")) == 4
    assert sizing.lowest_leverage(D("2.8125"), D("1000000"), D("10000"), 3, D("100")) == 3  # owner cap
    assert sizing.lowest_leverage(D("2.8125"), D("1000000"), D("10000"), 5, D("2")) == 2  # instrument cap
    assert sizing.lowest_leverage(D("1"), D("1000"), D("1000"), 5, None) == 1  # fits unlevered
    assert sizing.lowest_leverage(D("2"), None, None, 5, None) == 1  # unknown equity


def test_probability_clamp_and_hit_rate() -> None:
    card = {"distribution": {"24h": {"median": 0.1, "p10": -1, "p90": 1, "pct_positive": 50}}}
    assert sizing.analog_hit_rate(card, "buy") == (D("0.5"), "24h")
    card70 = {"distribution": {"4h": {"median": 0.1, "p10": -1, "p90": 1, "pct_positive": 70}}}
    assert sizing.analog_hit_rate(card70, "sell") == (D("0.3"), "4h")
    assert sizing.analog_hit_rate({"verdict": "supports"}, "buy") is None
    assert sizing.clamp_probability(D("0.80"), D("0.5")) == D("0.65")
    assert sizing.clamp_probability(D("0.20"), D("0.5")) == D("0.35")
    assert sizing.clamp_probability(D("0.55"), D("0.5")) == D("0.55")
    assert sizing.clamp_probability(D("0.99"), D("0.95")) == D("0.99")  # never above 1


def test_take_profit_split_and_time_stop() -> None:
    assert sizing.split_take_profits(3) == [D("50"), D("30"), D("20")]
    assert sizing.split_take_profits(2) == [D("50"), D("50")]
    assert sizing.split_take_profits(1) == [D("100")]
    monday_9_baghdad = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
    assert sizing.default_time_stop_minutes("crypto", monday_9_baghdad) == 1440
    assert sizing.default_time_stop_minutes("commodity", monday_9_baghdad) == 1440
    # Before Monday's open (EST, UTC-5): sessions Mon and Tue; Tue 16:00 ET = Tue 21:00 UTC.
    assert sizing.default_time_stop_minutes("stock", monday_9_baghdad) == (39 * 60)
    # Inside Friday's session (EDT, UTC-4) on 2026-10-30 18:00 UTC: Fri counts, then Mon 16:00 EST
    # (DST ends on 1 Nov) = Mon 21:00 UTC.
    friday = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
    assert sizing.default_time_stop_minutes("etf", friday) == int(
        (datetime(2026, 11, 2, 21, 0, tzinfo=UTC) - friday).total_seconds() // 60
    )


# -- the DB world -----------------------------------------------------------------------------------------


@dataclass
class World:
    engine: Engine
    inst: str
    tag: str
    thread_id: uuid.UUID
    cycle_id: uuid.UUID

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
        with Session(self.engine) as s:
            return list(s.scalars(select(Proposal).where(Proposal.inst_id == self.inst)))


def _setup_card(inst: str) -> dict[str, Any]:
    return {
        "type": "technical_setup",
        "inst_id": inst,
        "setup_type": "breakout",
        "direction": "long",
        "entry_zone": ["100.0", "100.2"],
        "stop": "96.7",
        "data_as_of": (NOW - timedelta(minutes=10)).isoformat(),
    }


def _verdict_card(inst: str, pct_positive: float = 50) -> dict[str, Any]:
    return {
        "type": "history_verdict",
        "inst_id": inst,
        "verdict": "supports",
        "n": 42,
        "distribution": {"24h": {"median": 0.2, "p10": -2.0, "p90": 2.5, "pct_positive": pct_positive}},
    }


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


@pytest.fixture
def world(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    _wipe_runs(engine)
    # No candles in this world: the ATR% percentile is the AC2 case's 70.
    monkeypatch.setattr(inp, "atr_percentile", lambda *_a, **_k: D("70"))
    tag = uuid.uuid4().hex[:8].upper()
    inst = f"S{tag}-USDT-SWAP"
    thread_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
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
            {"id": thread_id, "env": ENV, "key": f"s{tag}", "ts": NOW - timedelta(hours=1)},
        )
        conn.execute(
            text(
                "INSERT INTO cycles (id, env, kind, started_at, created_at, updated_at) "
                "VALUES (:id, :e, 'scheduled', :t, :t, :t)"
            ),
            {"id": cycle_id, "e": ENV, "t": NOW - timedelta(minutes=5)},
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
        s.add(
            WatchlistItem(
                watchlist_id=wl.id,
                inst_id=inst,
                rank=1,
                asset_class="crypto",
                bias="long",
                conviction=D("0.6"),
                thesis="t",
                catalysts=[],
                key_levels={},
                invalidation="i",
                data_as_of=NOW - timedelta(hours=2),
            )
        )
        s.commit()
    yield World(engine, inst, tag, thread_id, cycle_id)
    with engine.begin() as conn:
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst, Proposal.parent_id.is_not(None)))
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst))
        conn.execute(delete(Watchlist).where(Watchlist.env == ENV, Watchlist.date == owner_day(NOW)))
        conn.execute(text("DELETE FROM warroom_messages WHERE thread_id = :id"), {"id": thread_id})
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})
        conn.execute(text("DELETE FROM cycles WHERE id = :id"), {"id": cycle_id})
        if added_settings is not None:
            conn.execute(text("delete from settings where id = :id"), {"id": added_settings})
    _wipe_runs(engine)


def _supervisor(engine: Engine, client: fake.FakeClaudeClient, rec: Recorder) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)  # disabled by default
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: NOW,
    )


def _proposal(inst: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "inst_id": inst,
        "side": "buy",
        "order_type": "limit",
        "entry_zone": [100.0, 100.2],
        "stop": 96.7,
        "take_profits": [{"price": 105.0}, {"price": 108.0}, {"price": 110.5}],
        "time_stop_minutes": None,
        "size_suggestion_pct_equity": 9,
        "leverage_suggestion": 7,
        "expected_r_multiple": 2.4,
        "probability_estimate": 0.55,
        "rationale_md": "Breakout with positioning support.",
        "invalidation": "1H close below 99",
        "evidence": [],
    }
    body.update(over)
    return body


def _answer(block: dict[str, Any], text: str = "Synthesis: trend, level and flow agree.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(block)}\n```"),
        fake.result(tokens_in=500, tokens_out=200, turns=1),
    ]


def _expected_size(w: World) -> Decimal:
    factory = sessionmaker(bind=w.engine)
    from atlas_agents.tools.framework import ToolContext

    ctx = ToolContext(env=ENV, session_factory=factory, agent=SLUG, clock=lambda: NOW)
    with factory() as s:
        got = inp.gather(s, ctx, w.inst)
    assert got.sizing is not None
    return got.sizing.size_pct


# -- AC3: full inputs -> one submit_proposal with the computed size and the cycle id ----------------------


async def test_cycle_run_submits_once_with_the_computed_size_and_the_cycle_id(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst), NOW - timedelta(minutes=10))
    world.card("historian", _verdict_card(world.inst), NOW - timedelta(minutes=5))
    client = fake.FakeClaudeClient([_answer({"decision": "trade", "proposal": _proposal(world.inst)})])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)

    results = await hooks.run_cycle(sup, str(world.cycle_id))

    assert [r.status for r in results] == ["ok"]
    assert len(client.queries) == 1 and world.inst in client.queries[0]
    opened = rec.of("proposal.opened")
    assert len(opened) == 1  # submit_proposal called exactly once
    rows = world.proposals()
    assert len(rows) == 1
    row = rows[0]
    expected = _expected_size(world)
    # balanced profile: base 5 (or the owner's max_position_pct) x 0.75 (conviction 0.6) x 0.75 (pct 70)
    assert row.size_suggestion_pct == expected
    assert row.cycle_id == world.cycle_id
    assert opened[0].payload["cycle_id"] == str(world.cycle_id)
    card = opened[0].payload["card"]
    assert D(card["size_suggestion_pct_equity"]) == expected
    rules = {c["rule"] for c in card["meta"]["corrections"]}
    assert {
        "size_from_guidance",
        "leverage_lowest_that_fits",
        "take_profit_split_default",
        "time_stop_default",
    } <= rules
    assert [t["pct"] for t in card["take_profits"]] == ["50", "30", "20"]
    assert row.time_stop_minutes == 1440
    assert card["meta"]["sizing"]["conviction_multiplier"] == "0.75"
    assert card["meta"]["sizing"]["volatility_multiplier"] == "0.75"
    # the run's final message is the synthesis with the proposal pointer, as text
    final = [e for e in rec.of("warroom.message") if e.payload.get("kind") == "text"]
    assert final and "submitted" in final[-1].payload["content_md"]
    await sup.close()


# -- AC4: probability clamp ------------------------------------------------------------------------------


async def test_probability_080_against_hit_rate_050_is_clamped_to_065(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst), NOW - timedelta(minutes=10))
    world.card("historian", _verdict_card(world.inst, pct_positive=50), NOW - timedelta(minutes=5))
    client = fake.FakeClaudeClient(
        [_answer({"decision": "trade", "proposal": _proposal(world.inst, probability_estimate=0.80)})]
    )
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await hooks.run_cycle(sup, str(world.cycle_id))

    (row,) = world.proposals()
    assert row.probability == D("0.65")
    card = rec.of("proposal.opened")[0].payload["card"]
    assert card["probability_estimate"] == "0.65"
    clamp = [c for c in card["meta"]["corrections"] if c["rule"] == "probability_clamped"]
    assert clamp == [
        {"rule": "probability_clamped", "model": "0.8", "code": "0.65", "hit_rate": "0.5", "horizon": "24h"}
    ]
    assert card["meta"]["analog_hit_rate"] == {"hit_rate": "0.5", "horizon": "24h", "max_adjust_points": "15"}
    await sup.close()


# -- AC5: no TechnicalSetup -> "no trade" naming it, nothing submitted -----------------------------------


async def test_no_technical_setup_posts_no_trade_naming_it_and_submits_nothing(world: World) -> None:
    world.card("historian", _verdict_card(world.inst), NOW - timedelta(minutes=5))
    client = fake.FakeClaudeClient([])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)

    results = await hooks.run_cycle(sup, str(world.cycle_id))

    assert results == []
    assert client.queries == []  # no model run without the Technical setup
    assert world.proposals() == []
    assert rec.of("proposal.opened") == []
    (msg,) = rec.of("warroom.message")
    assert msg.payload["kind"] == "text" and msg.payload["author"] == SLUG
    assert f"No trade on {world.inst}" in msg.payload["content_md"]
    assert "technical_setup" in msg.payload["content_md"]
    assert msg.correlation_id == f"cycle-{world.cycle_id}"
    await sup.close()


async def test_model_no_trade_posts_reasons_and_submits_nothing(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst), NOW - timedelta(minutes=10))
    client = fake.FakeClaudeClient(
        [_answer({"decision": "no_trade", "reasons": ["Positioning opposes the long: crowded longs."]})]
    )
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    await hooks.run_cycle(sup, str(world.cycle_id))

    assert world.proposals() == []
    texts = [e for e in rec.of("warroom.message") if e.payload.get("kind") == "text"]
    assert texts and "crowded longs" in texts[-1].payload["content_md"]
    assert "No trade on" in texts[-1].payload["content_md"]
    # the model was told which inputs were missing
    assert '"missing": ["positioning_report", "news_brief", "history_verdict"]' in client.queries[0]
    await sup.close()


async def test_checklist_refusal_goes_back_once_then_posts_no_trade(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst), NOW - timedelta(minutes=10))
    bad = _proposal(world.inst, expected_r_multiple=1.0)  # below min R: checklist item 5 fails
    client = fake.FakeClaudeClient(
        [_answer({"decision": "trade", "proposal": bad}), _answer({"decision": "trade", "proposal": bad})]
    )
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    (result,) = await hooks.run_cycle(sup, str(world.cycle_id))

    assert result.status == "ok"
    assert len(client.queries) == 2 and "checklist_failed" in client.queries[1]
    assert world.proposals() == []
    texts = [e for e in rec.of("warroom.message") if e.payload.get("kind") == "text"]
    assert "the proposal was refused: checklist_failed" in texts[-1].payload["content_md"]
    await sup.close()


async def test_mention_plans_the_named_instrument(world: World) -> None:
    world.card("technical_analyst", _setup_card(world.inst), NOW - timedelta(minutes=10))
    client = fake.FakeClaudeClient([_answer({"decision": "no_trade", "reasons": ["No edge."]})])
    rec = Recorder()
    sup = _supervisor(world.engine, client, rec)
    envelope = EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="human:owner",
        payload={
            "thread": {"kind": "main", "key": "main"},
            "thread_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "author_type": "human",
            "author": "Owner",
            "kind": "text",
            "content_md": f"@Strategist plan a trade on {world.inst.lower()}",
            "reply_to": None,
            "mentions": [SLUG],
        },
    )
    await sup.handle_envelope(envelope)
    await hooks.settled()
    assert len(client.queries) == 1 and world.inst in client.queries[0]
    assert "The owner asks" in client.queries[0]
    await sup.close()


# -- the rules on their own -----------------------------------------------------------------------------


def test_rules_keep_a_matching_size_and_record_nothing_for_it() -> None:
    size = sizing.size_suggestion(D("5"), D("0.6"), D("70"))
    inputs = inp.StrategistInputs(
        inst_id="X-USDT-SWAP",
        as_of=NOW,
        asset_class="crypto",
        watchlist_pick=None,
        items={name: None for name in inp.INPUT_NAMES},
        portfolio={},
        settings={},
        sizing=size,
        leverage=2,
        hit_rates={"buy": None, "sell": None},
        time_stop_minutes=1440,
        take_profit_split=[D("50"), D("30"), D("20")],
    )
    raw = _proposal(
        "X-USDT-SWAP", size_suggestion_pct_equity="2.8125", leverage_suggestion=2, time_stop_minutes=600
    )
    raw["take_profits"] = [{"price": 105.0, "pct": 60}]
    out, meta = apply_with(raw, inputs)
    assert meta["corrections"] == []
    assert out["take_profits"] == [{"price": 105.0, "pct": 60}]
    assert out["time_stop_minutes"] == 600
    assert meta["analog_hit_rate"] is None  # no Historian card: the model's p stands
    assert out["probability_estimate"] == 0.55
