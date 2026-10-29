"""The warroom MCP tools (ATL-88). reply() reads warroom_messages, so run `cd backend && php artisan
test` first; the other tests need no DB."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_agents.tools.warroom import AGENT_KINDS
from atlas_shared.defaults import DEFAULTS
from atlas_shared.schemas import HistoryVerdict, RegimeTable
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

RUN = uuid.uuid4().hex[:10]
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


def _ctx(
    agent: str = "historian",
    *,
    recorder: Recorder | None = None,
    factory: Any = None,
    replay_as_of: datetime | None = None,
    default_publish: bool = False,
) -> ToolContext:
    kwargs: dict[str, Any] = {}
    if not default_publish:
        kwargs["publish"] = recorder if recorder is not None else Recorder()
    return ToolContext(
        env="demo",
        session_factory=factory or (lambda: None),
        agent=agent,
        correlation_id="cycle-2026-10-29",
        replay_as_of=replay_as_of,
        clock=lambda: NOW,
        **kwargs,
    )


async def _call(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await fw.make_handler(fw.get_tool(fq_name("warroom", name)), ctx)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    return bool(result.get("is_error")), body


def _verdict(**over: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "history_verdict",
        "question": "Does a 20-day breakout on XAU follow through over 24h?",
        "inst_id": "XAU-USDT-SWAP",
        "bar": "1H",
        "period": "2024-10-29..2026-10-29",
        "n": 42,
        "distribution": {"24h": {"median": 0.004, "p10": -0.011, "p90": 0.019, "pct_positive": 61.9}},
        "base_rate": {"24h": {"median": 0.001, "p10": -0.013, "p90": 0.015, "pct_positive": 52.0}},
        "regime": {"label": "trend", "probability": 0.78, "days_in_regime": 6},
        "stop_noise_check": {"stop_distance_atr": 1.2, "median_4h_range_atr": 1.0, "verdict": "ok"},
        "overfit_warning": False,
        "verdict": "supports",
        "key_stat": "62% positive at 24h vs 52% base rate (n=42)",
        "data_quality_notes": [],
        "data_as_of": "2026-10-29T08:00:00Z",
    }
    card.update(over)
    return card


# -- post_message --------------------------------------------------------------------------------------


async def test_historian_post_message_publishes_one_envelope_with_a_valid_card() -> None:
    rec = Recorder()
    error, body = await _call(
        _ctx("historian", recorder=rec),
        "post_message",
        {"kind": "research_report", "content_md": "History supports the breakout.", "card": _verdict()},
    )
    assert error is False
    assert len(rec.events) == 1
    stream, env = rec.events[0]
    assert stream == "atlas.warroom"
    assert env.type == "warroom.message"
    assert env.source == "agent:historian"
    assert env.env == "demo"
    assert env.correlation_id == "cycle-2026-10-29"
    p = env.payload
    assert p["author_type"] == "agent"
    assert p["author"] == "historian"
    assert p["kind"] == "research_report"
    assert p["thread"] == {"kind": "main", "key": "main"}
    assert p["content_md"] == "History supports the breakout."
    assert HistoryVerdict.model_validate(p["card"]).n == 42
    assert p["card"]["type"] == "history_verdict"
    assert p["mentions"] == [] and p["reply_to"] is None
    assert body["data"]["message_id"] == str(env.id)
    assert body["source"] == "atlas.warroom"


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"verdict": "supports", "n": 12}, "weakly_supports"),
        ({"bar": "2H"}, "bar"),
        ({"distribution": {"24h": {"median": 0.1, "p10": 0.2, "p90": 0.3, "pct_positive": 50}}}, "p10"),
        ({"regime": {"label": "trend", "probability": 1.4, "days_in_regime": 3}}, "regime.probability"),
        ({"data_as_of": "2026-10-29T08:00:00"}, "data_as_of"),
        ({"surprise": 1}, "surprise"),
    ],
)
async def test_an_invalid_history_verdict_is_refused_with_the_validation_errors(
    over: dict[str, Any], needle: str
) -> None:
    rec = Recorder()
    error, body = await _call(
        _ctx(recorder=rec),
        "post_message",
        {"kind": "research_report", "content_md": "x", "card": _verdict(**over)},
    )
    assert error is True
    assert body["error"] == "invalid_card"
    assert needle in body["message"]
    assert rec.events == []


async def test_a_missing_required_field_is_named() -> None:
    card = _verdict()
    del card["key_stat"]
    error, body = await _call(
        _ctx(), "post_message", {"kind": "research_report", "content_md": "x", "card": card}
    )
    assert error and body["error"] == "invalid_card" and "key_stat" in body["message"]


async def test_a_registered_card_on_the_wrong_kind_is_refused() -> None:
    error, body = await _call(_ctx(), "post_message", {"kind": "text", "content_md": "x", "card": _verdict()})
    assert error and body["error"] == "invalid_card"


async def test_a_regime_table_card_validates() -> None:
    rec = Recorder()
    card = {
        "type": "regime_table",
        "rows": [
            {
                "inst_id": "BTC-USDT-SWAP",
                "label": "trend",
                "probability": 0.81,
                "days_in_regime": 4,
                "data_as_of": "2026-10-29T04:00:00Z",
            },
            {
                "inst_id": "XAU-USDT-SWAP",
                "label": "range",
                "probability": 0.66,
                "days_in_regime": 11,
                "data_as_of": "2026-10-29T04:00:00Z",
            },
        ],
    }
    error, _ = await _call(
        _ctx(recorder=rec), "post_message", {"kind": "research_report", "content_md": "Regimes", "card": card}
    )
    assert error is False
    assert RegimeTable.model_validate(rec.events[0][1].payload["card"]).rows[1].label == "range"
    bad = {**card, "rows": [{**card["rows"][0], "days_in_regime": -1}]}
    error, body = await _call(
        _ctx(), "post_message", {"kind": "research_report", "content_md": "x", "card": bad}
    )
    assert error and "rows.0.days_in_regime" in body["message"]


async def test_an_unregistered_card_type_passes_through() -> None:
    rec = Recorder()
    card = {"type": "watchlist", "picks": []}
    error, _ = await _call(
        _ctx(recorder=rec), "post_message", {"kind": "watchlist", "content_md": "x", "card": card}
    )
    assert error is False and rec.events[0][1].payload["card"] == card


@pytest.mark.parametrize("kind", ["proposal", "risk_check", "order", "fill", "position_update", "nonsense"])
async def test_kinds_an_agent_may_not_author_are_refused(kind: str) -> None:
    rec = Recorder()
    error, body = await _call(_ctx(recorder=rec), "post_message", {"kind": kind, "content_md": "x"})
    assert error and body["error"] == "invalid_input" and rec.events == []


def test_agent_kinds_are_the_brief_list() -> None:
    assert set(AGENT_KINDS) == {
        "text",
        "watchlist",
        "research_report",
        "news_alert",
        "vote_summary",
        "post_mortem",
        "system_alert",
    }


async def test_content_over_the_cap_is_refused_and_empty_content_too() -> None:
    limit = int(DEFAULTS["warroom"]["content_max_chars"])
    rec = Recorder()
    error, body = await _call(
        _ctx(recorder=rec), "post_message", {"kind": "text", "content_md": "a" * (limit + 1)}
    )
    assert error and body["error"] == "content_too_long" and str(limit) in body["message"]
    error, body = await _call(_ctx(recorder=rec), "post_message", {"kind": "text", "content_md": "  \n"})
    assert error and body["error"] == "empty_content"
    assert rec.events == []
    error, _ = await _call(_ctx(recorder=rec), "post_message", {"kind": "text", "content_md": "a" * limit})
    assert error is False


# -- mention ---------------------------------------------------------------------------------------------


async def test_mention_sets_mentions_to_the_target_slug() -> None:
    rec = Recorder()
    error, body = await _call(
        _ctx("market_scout", recorder=rec),
        "mention",
        {"agent": "historian", "request": "Regime and analog check on XAU-USDT-SWAP, please."},
    )
    assert error is False
    p = rec.events[0][1].payload
    assert rec.events[0][1].source == "agent:market_scout"
    assert p["author"] == "market_scout"
    assert p["mentions"] == ["historian"]
    assert p["kind"] == "text"
    assert p["content_md"] == "@Historian Regime and analog check on XAU-USDT-SWAP, please."
    assert body["data"]["mentions"] == ["historian"]


async def test_mention_refuses_unknown_agents_and_itself() -> None:
    rec = Recorder()
    error, body = await _call(_ctx("historian", recorder=rec), "mention", {"agent": "owner", "request": "hi"})
    assert error and body["error"] == "unknown_agent"
    error, body = await _call(
        _ctx("historian", recorder=rec), "mention", {"agent": "historian", "request": "hi"}
    )
    assert error and body["error"] == "unknown_agent"
    assert rec.events == []


# -- Phase 2 stubs ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("cast_vote", {"proposal_id": str(uuid.uuid4()), "vote": "take", "confidence": 0.7, "reason": "ok"}),
        ("submit_proposal", {"proposal": {"inst_id": "BTC-USDT-SWAP"}}),
        ("request_human_input", {"question": "Proceed?"}),
    ],
)
async def test_phase_2_tools_answer_not_available_in_phase_1(name: str, args: dict[str, Any]) -> None:
    rec = Recorder()
    error, body = await _call(_ctx("strategist", recorder=rec), name, args)
    assert error is True
    assert body["error"] == "not_available_in_phase_1"
    assert rec.events == []


# -- replay ----------------------------------------------------------------------------------------------


async def test_replay_refuses_the_real_bus_but_accepts_a_recording_publisher() -> None:
    error, body = await _call(
        _ctx(replay_as_of=NOW, default_publish=True), "post_message", {"kind": "text", "content_md": "x"}
    )
    assert error and body["error"] == "not_available_in_replay"
    rec = Recorder()
    error, body = await _call(
        _ctx(replay_as_of=NOW, recorder=rec), "post_message", {"kind": "text", "content_md": "x"}
    )
    assert error is False and len(rec.events) == 1
    assert rec.events[0][1].ts == NOW


# -- reply (DB) ------------------------------------------------------------------------------------------


@pytest.fixture
def parent(engine: Engine) -> Iterator[tuple[sessionmaker[Session], str, str, str]]:
    """(factory, thread id, message row id, message event id) for a message in an agent thread."""
    thread_id, row_id, event_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    created = NOW - timedelta(minutes=5)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, 'demo', 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "key": f"t{RUN}", "ts": created},
        )
        conn.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, created_at, updated_at) VALUES (:id, 'demo', :thread, :event, 'agent', "
                "'market_scout', 'text', 'question', :ts, :ts)"
            ),
            {"id": row_id, "thread": thread_id, "event": event_id, "ts": created},
        )
    yield sessionmaker(bind=engine), thread_id, row_id, event_id
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM warroom_threads WHERE id = :id"), {"id": thread_id})


async def test_reply_goes_to_the_parents_thread_by_row_or_envelope_id(
    parent: tuple[sessionmaker[Session], str, str, str],
) -> None:
    factory, thread_id, row_id, event_id = parent
    for named in (row_id, event_id):
        rec = Recorder()
        error, body = await _call(
            _ctx("historian", recorder=rec, factory=factory),
            "reply",
            {"message_id": named, "content_md": "n=42"},
        )
        assert error is False, body
        p = rec.events[0][1].payload
        assert p["thread_id"] == thread_id and "thread" not in p
        assert p["reply_to"] == row_id
        assert p["kind"] == "text" and p["author"] == "historian"
        assert rec.events[0][1].source == "agent:historian"


async def test_reply_to_an_unknown_or_future_message_is_refused(
    parent: tuple[sessionmaker[Session], str, str, str],
) -> None:
    factory, _thread, row_id, _event = parent
    rec = Recorder()
    for missing in (str(uuid.uuid4()), "not-a-uuid"):
        error, body = await _call(
            _ctx(recorder=rec, factory=factory), "reply", {"message_id": missing, "content_md": "x"}
        )
        assert error and body["error"] == "unknown_message"
    # In replay a message created after the cutoff does not exist yet.
    error, body = await _call(
        _ctx(recorder=rec, factory=factory, replay_as_of=NOW - timedelta(minutes=10)),
        "reply",
        {"message_id": row_id, "content_md": "x"},
    )
    assert error and body["error"] == "unknown_message"
    assert rec.events == []
