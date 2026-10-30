"""ATL-93: the Historian answers @Historian with a HistoryVerdict reply and posts the 07:30 regime
table. Every run uses a FakeClaudeClient script; ClaudeSDKClient is made to raise in this module.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`). The mention test
reads atlas.warroom from the local Redis on DB 15 (REDIS_TEST_DB), like test_bus; it is skipped when
Redis is not running.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import redis
from atlas_agents import historian
from atlas_agents import supervisor as supervisor_module
from atlas_agents import testing as fake
from atlas_agents.registry import REGISTRY
from atlas_agents.schemas import HistoryVerdict, RegimeTable
from atlas_agents.supervisor import Supervisor
from atlas_shared import bus
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, Candle, Instrument
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from .conftest import Recorder

ENV = "demo"
SLUG = "historian"
QUESTION = "@Historian what usually happens to BTC after a 3-day 10% drop?"
NOW = datetime(2026, 10, 30, 4, 30, tzinfo=UTC)  # 07:30 Asia/Baghdad
ANALOGS_OUTPUT = {
    "as_of": "2026-10-30T04:00:00+00:00",
    "source": "atlas.candles",
    "truncated": False,
    "data": {
        "sample": {"inst_id": "BTC-USDT-SWAP", "bar": "1H", "n": 34, "unrepaired_gap": False},
        "status": "ok",
        "note": None,
        "result": {
            "forward": {"24h": {"median": 0.012, "p10": -0.031, "p90": 0.054, "pct_positive": 61.8}},
            "base_rate": {"24h": {"median": 0.002, "p10": -0.035, "p90": 0.04, "pct_positive": 52.1}},
        },
    },
}


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing the real CLI client in this module is a failure."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test constructed the real ClaudeSDKClient")

    monkeypatch.setattr("atlas_agents.session.ClaudeSDKClient", refuse)


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    _wipe_runs(engine)
    yield engine
    _wipe_runs(engine)


def _supervisor(
    engine: Engine, client: fake.FakeClaudeClient, rec: Recorder, now: datetime = NOW
) -> Supervisor:
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: REGISTRY[SLUG]},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: now,
    )


def _card(**changes: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "history_verdict",
        "question": "BTC after a 3-day 10% drop",
        "inst_id": "BTC-USDT-SWAP",
        "bar": "1H",
        "period": "2024-10-30..2026-10-30",
        "n": 34,
        "distribution": {"24h": {"median": 0.012, "p10": -0.031, "p90": 0.054, "pct_positive": 61.8}},
        "base_rate": {"24h": {"median": 0.002, "p10": -0.035, "p90": 0.04, "pct_positive": 52.1}},
        "regime": None,
        "stop_noise_check": None,
        "overfit_warning": False,
        "verdict": "supports",
        "key_stat": "24h median +1.2% vs base +0.2%",
        "data_quality_notes": [],
        "data_as_of": "2026-10-30T04:00:00Z",
    }
    card.update(changes)
    return card


ANSWER = "History supports a bounce: 24h median +1.2%, 61.8% positive."


def _answer(card: dict[str, Any], text: str = ANSWER) -> str:
    return f"{text}\n\n```json\n{json.dumps(card)}\n```"


def _attempt(
    card: dict[str, Any], *, output: dict[str, Any] = ANALOGS_OUTPUT, tool_id: str = "toolu_1"
) -> list[Any]:
    return [
        fake.thinking("Find analogs of a 3-day 10% drop."),
        fake.tool_use(
            "mcp__history__find_analogs", {"inst_id": "BTC-USDT-SWAP", "bar": "1H"}, tool_use_id=tool_id
        ),
        fake.tool_result(json.dumps(output), tool_use_id=tool_id),
        fake.assistant_text(_answer(card)),
        fake.result(tokens_in=900, tokens_out=200, turns=2),
    ]


def _owner_message(text: str = QUESTION) -> EventEnvelope:
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
            "mentions": ["historian"],
        },
    )


def _deltas(rec: Recorder, kind: str) -> list[EventEnvelope]:
    return [e for e in rec.of("warroom.thinking") if e.payload["delta_type"] == kind]


def _state(engine: Engine) -> str | None:
    with Session(engine) as db:
        query = select(AgentState).where(AgentState.agent == SLUG, AgentState.env == ENV)
        row = db.scalars(query).one_or_none()
        return None if row is None else row.state


# -- @Historian --------------------------------------------------------------------------------------


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.skip("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete("atlas.warroom")
    yield conn
    conn.delete("atlas.warroom")
    conn.close()


async def test_an_owner_mention_on_the_stream_gets_one_history_verdict_reply(
    clean: Engine, redis_client: redis.Redis
) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(_card())])
    sup = _supervisor(clean, client, rec)
    owner = _owner_message()
    # Something already on the stream before the consumer starts is not answered.
    bus.publish("atlas.warroom", _owner_message("@Historian old question"), client=redis_client)

    stop = asyncio.Event()
    handled: list[EventEnvelope] = []
    original = sup.handle_envelope

    async def handle(envelope: EventEnvelope) -> list[Any]:
        handled.append(envelope)
        results = await original(envelope)
        stop.set()
        return results

    sup.handle_envelope = handle  # type: ignore[method-assign]
    consumer = asyncio.create_task(sup.consume_warroom(redis_client, stop=stop, block_ms=50))
    await asyncio.sleep(0.2)
    bus.publish("atlas.warroom", owner, client=redis_client)
    await asyncio.wait_for(consumer, timeout=20)

    assert [e.id for e in handled] == [owner.id]
    assert len(client.queries) == 1 and QUESTION in client.queries[0]
    (reply,) = rec.of("warroom.message")
    assert reply.source == "agent:historian"
    p = reply.payload
    assert p["kind"] == "research_report" and p["author"] == "historian"
    assert p["reply_to"] == owner.payload["message_id"]
    assert p["thread_id"] == owner.payload["thread_id"] and "thread" not in p
    verdict = HistoryVerdict.model_validate(p["card"])
    assert verdict.verdict == "supports" and verdict.n == 34
    assert verdict.meta is not None and verdict.meta.verdict_corrections == []
    assert verdict.meta.unmatched_numbers == []
    assert p["content_md"].startswith("History supports a bounce")
    assert "```" not in p["content_md"]
    # The reply is the run's only message and carries the run's record.
    assert p["reasoning_md"] == "Find analogs of a 3-day 10% drop."
    assert [c["name"] for c in p["tool_calls"]] == ["mcp__history__find_analogs"]
    (end,) = _deltas(rec, "run_end")
    assert end.payload["status"] == "ok" and end.payload["run_id"] == p["run_id"]
    with Session(clean) as db:
        run = db.get(AgentRun, uuid.UUID(p["run_id"]))
        assert run is not None and run.trigger == "mention" and run.status == "ok"


async def test_every_thinking_event_of_a_mention_run_carries_the_threads_reply_to(clean: Engine) -> None:
    """ATL-98: every warroom.thinking event of a run started from an @mention, run_end included,
    carries the mention's thread_id and message id as reply_to, same as the final reply."""
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(_card())])
    sup = _supervisor(clean, client, rec)
    owner = _owner_message()

    (result,) = await sup.handle_envelope(owner)

    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    assert reply.payload["thread_id"] == owner.payload["thread_id"]
    assert reply.payload["reply_to"] == owner.payload["message_id"]
    thinking = rec.of("warroom.thinking")
    assert len(thinking) > 1  # at least one streamed delta plus run_end
    for event in thinking:
        assert event.payload["thread_id"] == reply.payload["thread_id"]
        assert event.payload["reply_to"] == reply.payload["reply_to"]
    (end,) = _deltas(rec, "run_end")
    assert end.payload["thread_id"] == owner.payload["thread_id"]
    assert end.payload["reply_to"] == owner.payload["message_id"]


async def test_a_small_sample_is_capped_and_the_correction_is_in_meta(clean: Engine) -> None:
    rec = Recorder()
    small = json.loads(json.dumps(ANALOGS_OUTPUT))
    small["data"]["sample"]["n"] = 12
    card = _card(n=12, key_stat="24h median +1.2% vs base +0.2%; +7.7% best case")
    client = fake.FakeClaudeClient([_attempt(card, output=small)])
    sup = _supervisor(clean, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    verdict = HistoryVerdict.model_validate(reply.payload["card"])
    assert verdict.verdict == "weakly_supports"
    assert reply.payload["card"]["meta"]["verdict_corrections"] == [
        {"from": "supports", "to": "weakly_supports", "rule": "n_below_20", "n": 12}
    ]
    unmatched = reply.payload["card"]["meta"]["unmatched_numbers"]
    assert [(u["raw"], u["location"], u["path"]) for u in unmatched] == [("+7.7%", "card", "key_stat")]


async def test_an_invalid_card_is_retried_once_with_the_errors_then_accepted(clean: Engine) -> None:
    rec = Recorder()
    bad = _card()
    del bad["verdict"]
    second = [fake.assistant_text(_answer(_card())), fake.result(tokens_in=100, tokens_out=50, turns=1)]
    client = fake.FakeClaudeClient([_attempt(bad), second])
    sup = _supervisor(clean, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "ok" and result.attempts == 2
    assert len(client.queries) == 2
    assert "not accepted" in client.queries[1] and "verdict: Field required" in client.queries[1]
    (reply,) = rec.of("warroom.message")
    assert reply.payload["kind"] == "research_report"
    HistoryVerdict.model_validate(reply.payload["card"])
    # The tool output from the first attempt still backs the corrected card's numbers.
    assert reply.payload["card"]["meta"]["unmatched_numbers"] == []
    assert reply.payload["tokens_in"] == 1000 and reply.payload["tokens_out"] == 250
    assert len(_deltas(rec, "run_end")) == 1
    with Session(clean) as db:
        runs = db.scalars(select(AgentRun).where(AgentRun.agent == SLUG, AgentRun.env == ENV)).all()
        assert [(r.status, r.turns) for r in runs] == [("ok", 3)]


async def test_two_invalid_cards_publish_nothing_and_degrade_the_historian(clean: Engine) -> None:
    rec = Recorder()
    bad = _card(n=-1)
    second = [fake.assistant_text("No card this time."), fake.result()]
    client = fake.FakeClaudeClient([_attempt(bad), second])
    sup = _supervisor(clean, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "degraded" and result.error and "no ```json card" in result.error
    assert rec.of("warroom.message") == []
    assert _state(clean) == "degraded"
    states = [e.payload["state"] for e in rec.of("system.agent_state")]
    assert states[-1] == "degraded"
    (end,) = _deltas(rec, "run_end")
    assert end.payload["status"] == "degraded" and end.payload["run_id"] == str(result.run_id)


async def test_a_rate_limited_run_ends_with_run_end_and_no_message(clean: Engine) -> None:
    rec = Recorder()
    steps: list[Any] = [fake.thinking_delta("Let me"), RuntimeError("Claude usage limit reached")]
    client = fake.FakeClaudeClient([steps])
    sup = _supervisor(clean, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "rate_limited"
    assert rec.of("warroom.message") == []
    (end,) = _deltas(rec, "run_end")
    assert end.payload["status"] == "rate_limited" and end.payload["run_id"] == str(result.run_id)
    assert rec.of("warroom.thinking")[-1] is end


async def test_messages_not_for_the_historian_start_no_run(clean: Engine) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([])
    sup = _supervisor(clean, client, rec)
    other = _owner_message("@Scout anything?")
    other.payload["mentions"] = ["market_scout"]
    own = _owner_message()
    own_from_self = own.model_copy(update={"source": "agent:historian"})
    live = _owner_message().model_copy(update={"env": "live"})

    for envelope in (other, own_from_self, live):
        assert await sup.handle_envelope(envelope) == []
    assert client.queries == []


# -- the 07:30 regime table -----------------------------------------------------------------------

REGIME_INSTS = ("ATL93A-USDT-SWAP", "ATL93B-USDT-SWAP", "ATL93C-USDT-SWAP")
# Quote volume per 1H bar: far above anything else in the test DB, so these three rank first.
VOLUMES = {"ATL93A-USDT-SWAP": "3e15", "ATL93B-USDT-SWAP": "9e15", "ATL93C-USDT-SWAP": "1e15"}


def _wipe_market(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Candle).where(Candle.inst_id.in_(REGIME_INSTS)))
        conn.execute(delete(Instrument).where(Instrument.inst_id.in_(REGIME_INSTS)))


@pytest.fixture
def market(clean: Engine) -> Iterator[Engine]:
    import numpy as np

    _wipe_market(clean)
    rows: list[dict[str, Any]] = []
    instruments: list[dict[str, Any]] = []
    for k, inst in enumerate(REGIME_INSTS):
        instruments.append(
            {
                "env": ENV,
                "inst_id": inst,
                "inst_type": "SWAP",
                "asset_class": "crypto",
                "base_ccy": inst.split("-")[0],
                "quote_ccy": "USDT",
                "settle_ccy": "USDT",
                "ct_val": Decimal("0.01"),
                "lot_sz": Decimal("0.1"),
                "min_sz": Decimal("0.1"),
                "tick_sz": Decimal("0.1"),
                "max_lever": Decimal("50"),
                "state": "live",
                "list_time": NOW - timedelta(days=400),
                "created_at": NOW - timedelta(days=400),
                "updated_at": NOW - timedelta(days=1),
                "last_seen_at": NOW - timedelta(hours=1),
            }
        )
        rng = np.random.default_rng(k + 1)
        day0 = datetime(2026, 1, 1, tzinfo=UTC)
        days = (NOW - day0).days
        close = 100 * np.exp(np.cumsum(rng.normal(0.001 * (k - 1), 0.02, days)))
        for i in range(days):
            c = Decimal(f"{close[i]:.4f}")
            o = Decimal(f"{(close[i - 1] if i else close[0]):.4f}")
            rows.append(_bar(inst, "1D", day0 + timedelta(days=i), o, c, "1000"))
        for h in range(1, 25):
            rows.append(_bar(inst, "1H", NOW - timedelta(hours=h + 1), c, c, VOLUMES[inst]))
    with clean.begin() as conn:
        conn.execute(Instrument.__table__.insert(), instruments)
        conn.execute(Candle.__table__.insert(), rows)
    yield clean
    _wipe_market(clean)


def _bar(inst: str, bar: str, ts: datetime, o: Decimal, c: Decimal, vol_quote: str) -> dict[str, Any]:
    return {
        "env": ENV,
        "inst_id": inst,
        "bar": bar,
        "ts": ts,
        "o": o,
        "h": max(o, c) * Decimal("1.01"),
        "l": min(o, c) * Decimal("0.99"),
        "c": c,
        "vol": Decimal(100),
        "vol_ccy": None,
        "vol_ccy_quote": Decimal(vol_quote),
        "confirmed": True,
    }


async def test_the_regime_table_is_posted_once_at_0730_and_not_again_after_a_restart(
    market: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert historian.regime_top_n() == 60
    assert DEFAULTS["agents"]["schedule"]["historian_regime_table"] == "07:30"
    monkeypatch.setattr(historian, "regime_top_n", lambda defaults=DEFAULTS: 2)
    rec = Recorder()
    commentary = [fake.assistant_text("ATL93B leads the table."), fake.result(tokens_in=50, tokens_out=10)]
    client = fake.FakeClaudeClient([commentary])

    early = _supervisor(market, client, rec, now=NOW - timedelta(minutes=1))
    assert await early.tick() == []  # 07:29 Baghdad: not yet

    sup = _supervisor(market, client, rec)
    (result,) = await sup.tick()
    assert result.status == "ok"
    (message,) = rec.of("warroom.message")
    p = message.payload
    assert message.source == "agent:historian" and p["kind"] == "regime_table"
    assert p["thread"] == {"kind": "main", "key": "main"} and p["run_id"] == str(result.run_id)
    assert p["content_md"] == "ATL93B leads the table."
    table = RegimeTable.model_validate(p["card"])
    # Top 2 of the three by 24h quote volume, highest first.
    assert [r.inst_id for r in table.rows] == ["ATL93B-USDT-SWAP", "ATL93A-USDT-SWAP"]
    assert len(table.rows) <= 60
    for row in table.rows:
        assert row.label and set(p["card"]["rows"][0]) == {
            "inst_id",
            "label",
            "probability",
            "days_in_regime",
            "method",
        }
    assert table.data_as_of <= NOW
    assert "ATL93B" in client.queries[0] and "regime" in client.queries[0]
    with Session(market) as db:
        run = db.get(AgentRun, result.run_id)
        assert run is not None and run.trigger == "schedule"
        assert run.correlation_id == "historian_regime_table:2026-10-30"

    # The same supervisor ticking again, and a restarted one at 07:40, post nothing more.
    assert await sup.tick() == []
    restarted = _supervisor(market, fake.FakeClaudeClient([]), rec, now=NOW + timedelta(minutes=10))
    assert await restarted.tick() == []
    assert len(rec.of("warroom.message")) == 1


async def test_regime_table_thinking_events_have_null_thread_and_reply(
    market: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ATL-98: a scheduled run (the 07:30 regime table) is not a reply to anything, so every
    warroom.thinking event, run_end included, carries null thread_id and reply_to (shown in Main)."""
    monkeypatch.setattr(historian, "regime_top_n", lambda defaults=DEFAULTS: 2)
    rec = Recorder()
    commentary = [fake.assistant_text("ATL93B leads the table."), fake.result(tokens_in=50, tokens_out=10)]
    client = fake.FakeClaudeClient([commentary])
    sup = _supervisor(market, client, rec)

    (result,) = await sup.tick()

    assert result.status == "ok"
    thinking = rec.of("warroom.thinking")
    assert len(thinking) >= 1
    for event in thinking:
        assert event.payload["thread_id"] is None
        assert event.payload["reply_to"] is None
    (end,) = _deltas(rec, "run_end")
    assert end.payload["thread_id"] is None and end.payload["reply_to"] is None


async def test_a_failed_regime_run_still_posts_the_table_without_commentary(
    market: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(historian, "regime_top_n", lambda defaults=DEFAULTS: 3)
    rec = Recorder()
    client = fake.FakeClaudeClient([[RuntimeError("Claude usage limit reached")]])
    sup = _supervisor(market, client, rec)

    (result,) = await sup.tick()

    assert result.status == "rate_limited"
    (message,) = rec.of("warroom.message")
    assert message.payload["kind"] == "regime_table" and message.payload["run_id"] == str(result.run_id)
    assert "No commentary today" in message.payload["content_md"]
    assert [r.inst_id for r in RegimeTable.model_validate(message.payload["card"]).rows] == [
        "ATL93B-USDT-SWAP",
        "ATL93A-USDT-SWAP",
        "ATL93C-USDT-SWAP",
    ]
    assert supervisor_module.job_correlation_id("historian_regime_table", NOW.date()).endswith("2026-10-30")
