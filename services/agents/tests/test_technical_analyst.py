"""ATL-135: the Technical Analyst (brief §7.4). Runs use a FakeClaudeClient script; candles, the
instrument and positions are seeded in this checkout's test DB (`cd backend && php artisan test`
first).

AC2 watchlist card -> one run per pick; AC3 code rules (stop < 1.0 x ATR(1H) -> "none", tick
rounding); AC4 stale 1H candles -> data alert and no run, two invalid cards -> degraded; AC5
candle.closed starts a run only for a held instrument.
"""

from __future__ import annotations

import dataclasses
import json
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import technical_analyst as ta_agent
from atlas_agents import testing as fake
from atlas_agents.agents import technical_analyst as hooks
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools.ta_table import bars_late, build_ta_table, stale_timeframes
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    BalanceSnapshot,
    Candle,
    Instrument,
    PositionSnapshot,
)
from atlas_shared.events import EventEnvelope
from atlas_shared.technical import TechnicalSetup
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
SLUG = "technical_analyst"
RUN = uuid.uuid4().hex[:8].upper()
FRESH = f"T{RUN}-USDT-SWAP"
FRESH2 = f"U{RUN}-USDT-SWAP"
STALE = f"S{RUN}-USDT-SWAP"
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
FAR = datetime(2099, 1, 1, tzinfo=UTC)  # position snapshot newer than any other test's
TICK = Decimal("0.1")


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


def _instrument(inst_id: str) -> Instrument:
    return Instrument(
        env=ENV,
        inst_id=inst_id,
        inst_type="SWAP",
        asset_class="crypto",
        base_ccy=inst_id.split("-")[0],
        quote_ccy="USDT",
        settle_ccy="USDT",
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("0.1"),
        min_sz=Decimal("0.1"),
        tick_sz=TICK,
        max_lever=Decimal("50"),
        state="live",
        list_time=NOW - timedelta(days=400),
        created_at=NOW - timedelta(days=400),
        updated_at=NOW - timedelta(days=400),
        last_seen_at=NOW - timedelta(hours=1),
    )


def _candles(inst_id: str, last_open: datetime, bars: int = 300) -> list[Candle]:
    rows = []
    for i in range(bars):
        ts = last_open - timedelta(hours=bars - 1 - i)
        mid = 100 + 5 * math.sin(i / 9) + i * 0.02
        c = Decimal(f"{mid:.4f}")
        rows.append(
            Candle(
                env=ENV,
                inst_id=inst_id,
                bar="1H",
                ts=ts,
                o=c - Decimal("0.2"),
                h=c + Decimal("0.8"),
                l=c - Decimal("0.8"),
                c=c,
                vol=Decimal(100 + (i % 7) * 10),
                vol_ccy=Decimal("1"),
                vol_ccy_quote=Decimal("10000"),
                confirmed=True,
            )
        )
    return rows


SETTINGS = {"risk_profile": "balanced", "min_expected_r": "1.8", "max_position_pct": "5", "autonomy_level": 2}


def _ensure_settings(engine: Engine) -> uuid.UUID | None:
    """The prompt's {{MIN_R}} needs the env's settings row; add one when the test DB has none."""
    with engine.begin() as conn:
        if conn.execute(text("select 1 from settings where env = :e"), {"e": ENV}).first():
            return None
        sid = uuid.uuid4()
        conn.execute(
            text(
                "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                "values (:id, :env, 1, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
            ),
            {"id": sid, "env": ENV, "data": json.dumps(SETTINGS), "t": NOW - timedelta(days=1)},
        )
        return sid


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    _wipe_runs(engine)
    added = _ensure_settings(engine)
    with Session(engine) as s:
        for inst in (FRESH, FRESH2, STALE):
            s.add(_instrument(inst))
        s.add_all(_candles(FRESH, NOW - timedelta(hours=1)))  # newest bar closes at NOW
        s.add_all(_candles(FRESH2, NOW - timedelta(hours=1)))
        s.add_all(_candles(STALE, NOW - timedelta(hours=4)))  # closes at NOW - 3h: 3 bars late
        s.commit()
    yield engine
    with engine.begin() as conn:
        ids = [FRESH, FRESH2, STALE]
        conn.execute(delete(Candle).where(Candle.inst_id.in_(ids)))
        conn.execute(delete(Instrument).where(Instrument.inst_id.in_(ids)))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.taken_at == FAR))
        conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.taken_at == FAR))
        if added is not None:
            conn.execute(text("delete from settings where id = :id"), {"id": added})
    _wipe_runs(engine)


def _supervisor(engine: Engine, client: fake.FakeClaudeClient, rec: Recorder) -> Supervisor:
    # Disabled by default (the owner turns it on); enabled here.
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: NOW,
    )


def _no_setup_card(inst_id: str, **changes: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "technical_setup",
        "inst_id": inst_id,
        "timeframe_bias": {"1H": "range"},
        "setup_type": "none",
        "direction": "none",
        "entry_zone": None,
        "trigger": "",
        "stop": None,
        "targets": [],
        "expected_r": None,
        "invalidation": "",
        "quality_score": 0.2,
        "est_slippage_bps": None,
        "levels_used": [],
        "no_setup_reason": "A 1H close above the range top turns this into a breakout long.",
        "data_as_of": NOW.isoformat(),
    }
    card.update(changes)
    return card


def _reply(card: dict[str, Any], text: str = "No setup: range-bound.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(card)}\n```"),
        fake.result(tokens_in=400, tokens_out=120, turns=1),
    ]


def _mention(text: str) -> EventEnvelope:
    return EventEnvelope(
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
            "content_md": text,
            "reply_to": None,
            "mentions": [SLUG],
        },
    )


def _reports(rec: Recorder) -> list[EventEnvelope]:
    return [e for e in rec.of("warroom.message") if e.payload.get("kind") == "research_report"]


def _runs(engine: Engine) -> list[AgentRun]:
    with Session(engine) as s:
        return list(s.scalars(select(AgentRun).where(AgentRun.agent == SLUG, AgentRun.env == ENV)))


async def _table(sup: Supervisor, inst_id: str) -> dict[str, Any]:
    table, _as_of = await build_ta_table(sup.tool_context(SLUG), inst_id)
    return table


# -- registration -------------------------------------------------------------------------------------


def test_disabled_by_default_and_registers_the_three_triggers() -> None:
    assert not REGISTRY[SLUG].enabled
    found = discover_hooks()[SLUG]
    assert found.on_mention is hooks.answer_mention
    assert set(found.on_event) == {"warroom.message.watchlist", "candle.closed"}


# -- AC2: a watchlist card with 2 picks starts 2 runs ------------------------------------------------


async def test_watchlist_card_with_two_picks_starts_two_runs_each_posting_a_technical_setup(
    db: Engine,
) -> None:
    client = fake.FakeClaudeClient([_reply(_no_setup_card(FRESH)), _reply(_no_setup_card(FRESH2))])
    rec = Recorder()
    sup = _supervisor(db, client, rec)
    card = {
        "type": "watchlist",
        "watchlist_id": str(uuid.uuid4()),
        "picks": [{"inst_id": FRESH2, "rank": 2}, {"inst_id": FRESH, "rank": 1}],
    }
    envelope = EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="agent:market_scout",
        payload={"author": "market_scout", "author_type": "agent", "kind": "watchlist", "card": card},
    )
    await sup.handle_envelope(envelope)
    await hooks.settled()

    assert len(client.queries) == 2
    assert FRESH in client.queries[0] and FRESH2 in client.queries[1]  # best rank first
    reports = _reports(rec)
    assert len(reports) == 2
    setups = [TechnicalSetup.model_validate(r.payload["card"]) for r in reports]
    assert [s.inst_id for s in setups] == [FRESH, FRESH2]
    assert all(s.setup_type == "none" and s.no_setup_reason for s in setups)
    assert len(_runs(db)) == 2
    await sup.close()


async def test_watchlist_card_from_anyone_but_the_scout_starts_nothing(db: Engine) -> None:
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, Recorder())
    envelope = EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="human:owner",
        payload={
            "author": "Owner",
            "author_type": "human",
            "kind": "watchlist",
            "card": {"picks": [{"inst_id": FRESH}]},
        },
    )
    await sup.handle_envelope(envelope)
    await hooks.settled()
    assert client.queries == []
    await sup.close()


# -- AC3: code rules after the model -----------------------------------------------------------------


async def test_stop_half_an_atr_from_entry_becomes_none_with_a_reason(db: Engine) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, rec)
    table = await _table(sup, FRESH)
    atr = Decimal(str(table["timeframes"]["1H"]["volatility"]["atr"]))
    entry = Decimal("100.0")
    stop = entry - atr / 2
    card = _no_setup_card(
        FRESH,
        setup_type="pullback_to_support",
        direction="long",
        entry_zone=[str(entry - Decimal("0.05")), str(entry + Decimal("0.05"))],
        stop=str(stop),
        targets=[{"price": str(entry + atr * 10), "r_multiple": 20, "reason": "1H:z2"}],
        expected_r=20,
        no_setup_reason=None,
    )
    client.add_run(_reply(card, "Long pullback to support."))
    await sup.handle_envelope(_mention(f"@Technical what about {FRESH}?"))
    await hooks.settled()

    [report] = _reports(rec)
    setup = TechnicalSetup.model_validate(report.payload["card"])
    assert setup.setup_type == "none" and setup.direction == "none"
    assert setup.stop is None and setup.targets == [] and setup.entry_zone is None
    assert setup.no_setup_reason and "ATR(1H)" in setup.no_setup_reason
    rules = [c["rule"] for c in (setup.meta or {})["corrections"]]
    assert "stop_too_tight" in rules
    assert "No setup after Atlas's checks" in report.payload["content_md"]
    await sup.close()


async def test_prices_are_rounded_to_tick_and_r_recomputed(db: Engine) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, rec)
    table = await _table(sup, FRESH)
    atr = Decimal(str(table["timeframes"]["1H"]["volatility"]["atr"]))
    card = _no_setup_card(
        FRESH,
        setup_type="breakout",
        direction="long",
        entry_zone=["100.03", "100.17"],
        stop=str(Decimal("100.1") - atr * 2 + Decimal("0.04")),
        targets=[
            {"price": str(Decimal("100.1") + atr * 6 + Decimal("0.06")), "r_multiple": 3, "reason": "1H:z4"},
        ],
        expected_r=3,
        no_setup_reason=None,
    )
    client.add_run(_reply(card, "Long breakout."))
    await sup.handle_envelope(_mention(f"@Technical {FRESH}"))
    await hooks.settled()

    [report] = _reports(rec)
    setup = TechnicalSetup.model_validate(report.payload["card"])
    assert setup.setup_type == "breakout"
    assert setup.entry_zone is not None and setup.stop is not None
    for price in (*setup.entry_zone, setup.stop, *(t.price for t in setup.targets)):
        assert price == price.quantize(TICK) and (price / TICK) % 1 == 0
    assert setup.entry_zone == (Decimal("100.0"), Decimal("100.2"))
    raw_stop = Decimal(card["stop"])
    assert setup.stop <= raw_stop  # a long stop rounds down, never tighter
    entry = (setup.entry_zone[0] + setup.entry_zone[1]) / 2
    r = (abs(setup.targets[0].price - entry) / abs(entry - setup.stop)).quantize(Decimal("0.01"))
    assert setup.targets[0].r_multiple == r and setup.expected_r == r
    assert "tick_rounding" in [c["rule"] for c in (setup.meta or {})["corrections"]]
    await sup.close()


def test_enforce_setup_short_side_min_r_and_thin_liquidity() -> None:
    table = {
        "inst_id": "X-USDT-SWAP",
        "data_as_of": NOW.isoformat(),
        "spec": {"tick_sz": "0.5"},
        "timeframes": {"1H": {"volatility": {"atr": 2.0}}},
        "book": {"slippage": {"sell": {"slippage_bps": "14.2", "filled": True}}},
    }
    base = TechnicalSetup.model_validate(
        _no_setup_card(
            "X-USDT-SWAP",
            setup_type="breakdown",
            direction="short",
            entry_zone=["100.2", "100.6"],
            stop="104.1",
            targets=[{"price": "88.2", "reason": "1D:z0"}],
            no_setup_reason=None,
        )
    )
    final, _corr = ta_agent.enforce_setup(base, table, Decimal("1.5"))
    assert final.stop == Decimal("104.5")  # a short stop rounds up
    assert final.entry_zone == (Decimal("100.0"), Decimal("100.5"))
    assert final.est_slippage_bps == Decimal("14.20")
    assert final.notes and "Thin liquidity" in final.notes[0]

    low_r, corrections = ta_agent.enforce_setup(base, table, Decimal("5"))
    assert low_r.setup_type == "none" and "below the owner's minimum 5" in (low_r.no_setup_reason or "")
    assert corrections[-1]["rule"] == "below_min_r"

    wrong = base.model_copy(update={"stop": Decimal("99")})
    out, corrections = ta_agent.enforce_setup(wrong, table, Decimal("1"))
    assert out.setup_type == "none" and corrections[-1]["rule"] == "stop_wrong_side"


# -- AC4: stale candles refuse; two invalid cards degrade ---------------------------------------------


async def test_three_bars_late_1h_candle_posts_a_data_alert_and_no_setup(db: Engine) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, rec)
    await sup.handle_envelope(_mention(f"@Technical {STALE}"))
    await hooks.settled()

    assert client.queries == []  # the model is never asked
    assert _reports(rec) == []
    [alert] = [e for e in rec.of("warroom.message") if e.payload.get("kind") == "system_alert"]
    assert alert.payload["card"]["type"] == "data_alert"
    assert alert.payload["card"]["stale"] == {"1H": 3}
    assert "Data alert" in alert.payload["content_md"]
    assert _runs(db) == []
    await sup.close()


async def test_two_invalid_cards_mark_the_agent_degraded(db: Engine) -> None:
    bad = [fake.assistant_text("Setup:\n\n```json\n{not json\n```"), fake.result(turns=1)]
    client = fake.FakeClaudeClient([bad, bad])
    rec = Recorder()
    sup = _supervisor(db, client, rec)
    await sup.handle_envelope(_mention(f"@Technical {FRESH}"))
    await hooks.settled()

    assert len(client.queries) == 2  # the answer and its one retry
    assert "not accepted" in client.queries[1]
    assert _reports(rec) == []
    [run] = _runs(db)
    assert run.status == "degraded"
    with Session(db) as s:
        state = s.scalars(select(AgentState).where(AgentState.agent == SLUG, AgentState.env == ENV)).one()
    assert state.state == "degraded"
    await sup.close()


def test_bars_late_and_stale_rule() -> None:
    assert bars_late(NOW, NOW, "1H") == 0
    assert bars_late(NOW - timedelta(hours=2, minutes=59), NOW, "1H") == 2
    assert bars_late(NOW - timedelta(hours=3), NOW, "1H") == 3
    assert bars_late(None, NOW, "1H") is None
    assert stale_timeframes({"timeframes": {"1H": {"bars_late": 2}}}) == {}
    assert stale_timeframes({"timeframes": {"1H": {"bars_late": 3}}}) == {"1H": 3}
    assert stale_timeframes({"timeframes": {"1H": {"bars_late": None}}}) == {"1H": None}


# -- AC5: candle.closed only for a held instrument -----------------------------------------------------


def _hold(engine: Engine, inst_id: str) -> None:
    with Session(engine) as s:
        s.add(
            BalanceSnapshot(
                env=ENV, account="trading", ccy="USDT", eq=Decimal("1"), eq_usd=Decimal("1"), taken_at=FAR
            )
        )
        s.add(PositionSnapshot(env=ENV, inst_id=inst_id, pos_side="net", pos=Decimal("3"), taken_at=FAR))
        s.commit()


def _closed(inst_id: str, bar: str = "1H") -> EventEnvelope:
    return EventEnvelope(
        type="candle.closed",
        env=ENV,
        source="service:trading_core.market_ws",
        payload={"inst_id": inst_id, "bar": bar, "ts": (NOW - timedelta(hours=1)).isoformat(), "c": "100"},
    )


async def test_candle_closed_runs_only_for_a_held_instrument(db: Engine) -> None:
    _hold(db, FRESH)
    client = fake.FakeClaudeClient([_reply(_no_setup_card(FRESH), "Structure intact.")])
    rec = Recorder()
    sup = _supervisor(db, client, rec)

    await sup.handle_market_envelope(_closed(FRESH))
    await hooks.settled()
    assert len(client.queries) == 1 and FRESH in client.queries[0]
    assert "holds a position" in client.queries[0]

    await sup.handle_market_envelope(_closed(FRESH2))  # no position
    await sup.handle_market_envelope(_closed(FRESH, bar="5m"))  # not the position bar
    await hooks.settled()
    assert len(client.queries) == 1
    assert len(_reports(rec)) == 1
    await sup.close()


async def test_market_events_nobody_registered_are_dropped(db: Engine) -> None:
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, Recorder())
    tick = EventEnvelope(type="market.tick", env=ENV, source="service:trading_core.market_ws", payload={})
    assert await sup.handle_market_envelope(tick) == []
    await sup.close()


def test_mentioned_instruments() -> None:
    assert ta_agent.mentioned_instruments("@Technical btc-usdt-swap and XAU-USDT-SWAP, BTC-USDT-SWAP") == [
        "BTC-USDT-SWAP",
        "XAU-USDT-SWAP",
    ]
    assert ta_agent.mentioned_instruments("@Technical look at BTC-USDT spot") == ["BTC-USDT"]
    assert ta_agent.mentioned_instruments("A-B1-SWAP C-D1-SWAP E-F1-SWAP G-H1-SWAP") == [
        "A-B1-SWAP",
        "C-D1-SWAP",
        "E-F1-SWAP",
    ]
