"""The memory MCP tools against the real test DB (ATL-86). Run `cd backend && php artisan test` first."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_shared.db import AgentNotebookEntry, AgentScore, JournalEntry, usage_day
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session, sessionmaker

RUN = uuid.uuid4().hex[:10]
INST = f"T{RUN}-USDT-SWAP"
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)  # 12:00 in Baghdad


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=engine)
    yield factory
    with engine.begin() as conn:
        conn.execute(delete(AgentNotebookEntry).where(AgentNotebookEntry.note_md.like(f"%{RUN}%")))
        conn.execute(delete(JournalEntry).where(JournalEntry.title.like(f"%{RUN}%")))
        conn.execute(delete(AgentScore).where(AgentScore.data["run"].astext == RUN))


def _ctx(
    factory: sessionmaker[Session], agent: str, *, replay_as_of: datetime | None = None, clock: Any = None
) -> ToolContext:
    return ToolContext(
        env="demo",
        session_factory=factory,
        agent=agent,
        replay_as_of=replay_as_of,
        clock=clock or (lambda: NOW),
        publish=lambda stream, envelope: None,
    )


async def _call(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await fw.make_handler(fw.get_tool(fq_name("memory", name)), ctx)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    return body


def _score(factory: sessionmaker[Session], agent: str, day: date, hit_rate: str, env: str = "demo") -> None:
    with factory() as s:
        s.add(
            AgentScore(
                env=env,
                agent=agent,
                day=day,
                n=10,
                hit_rate=Decimal(hit_rate),
                brier_score=Decimal("0.2"),
                data={"run": RUN},
                created_at=datetime.combine(day, datetime.min.time(), UTC) + timedelta(days=1),
                updated_at=NOW,
            )
        )
        s.commit()


# -- scores -----------------------------------------------------------------------------------------


async def test_get_my_scores_returns_only_the_calling_agents_rows(db: sessionmaker[Session]) -> None:
    # Unique "agents" so other tests' rows on the same (agent, env, day) cannot collide.
    me, other = f"historian_{RUN}", f"market_scout_{RUN}"
    today = usage_day(NOW)
    for back in range(1, 4):
        _score(db, me, today - timedelta(days=back), "0.6")
        _score(db, other, today - timedelta(days=back), "0.9")
    _score(db, me, today - timedelta(days=1), "0.1", env="live")
    _score(db, me, today - timedelta(days=30), "0.6")

    body = await _call(_ctx(db, me), "get_my_scores", {"days": 7})
    data = body["data"]
    assert data["agent"] == me
    assert [s["day"] for s in data["scores"]] == [str(today - timedelta(days=b)) for b in (1, 2, 3)]
    assert all(s["hit_rate"] == 0.6 for s in data["scores"])


async def test_get_my_scores_as_historian_never_shows_other_agents(db: sessionmaker[Session]) -> None:
    today = usage_day(NOW) + timedelta(days=4000)  # a day no real test writes
    clock = datetime.combine(today, datetime.min.time(), UTC) + timedelta(hours=12)
    _score(db, "market_scout", today - timedelta(days=1), "0.9")
    _score(db, "historian", today - timedelta(days=1), "0.55")
    body = await _call(_ctx(db, "historian", clock=lambda: clock), "get_my_scores", {"days": 3})
    assert body["data"]["agent"] == "historian"
    assert [s["hit_rate"] for s in body["data"]["scores"]] == [0.55]
    assert "agent" not in fw.get_tool(fq_name("memory", "get_my_scores")).input_schema()["properties"]


async def test_get_my_scores_in_replay_excludes_today_and_later(db: sessionmaker[Session]) -> None:
    me = f"historian_{RUN}"
    today = usage_day(NOW)
    _score(db, me, today - timedelta(days=1), "0.6")
    _score(db, me, today, "0.7")
    body = await _call(_ctx(db, me, replay_as_of=NOW + timedelta(days=1)), "get_my_scores", {"days": 7})
    assert [s["day"] for s in body["data"]["scores"]] == [str(today), str(today - timedelta(days=1))]
    early = await _call(_ctx(db, me, replay_as_of=NOW), "get_my_scores", {"days": 7})
    assert [s["day"] for s in early["data"]["scores"]] == [str(today - timedelta(days=1))]


# -- notebook ---------------------------------------------------------------------------------------


async def test_append_notebook_then_read_notebook_round_trips_todays_note(db: sessionmaker[Session]) -> None:
    clock = datetime.now(UTC)
    written = await _call(
        _ctx(db, "historian", clock=lambda: clock), "append_notebook", {"note": f"BTC regime flipped {RUN}"}
    )
    assert written["data"]["day"] == str(usage_day(clock))

    read = await _call(_ctx(db, "market_scout", clock=lambda: clock), "read_notebook", {})
    data = read["data"]
    assert data["day"] == str(usage_day(clock))
    mine = [n for n in data["notes"] if RUN in n["note_md"]]
    assert len(mine) == 1
    assert mine[0]["id"] == written["data"]["id"]
    assert mine[0]["note_md"] == f"BTC regime flipped {RUN}"
    assert mine[0]["agent"] == "historian"


async def test_read_notebook_by_date_and_replay_bound(db: sessionmaker[Session]) -> None:
    ctx = _ctx(db, "historian")
    await _call(ctx, "append_notebook", {"note": f"morning {RUN}"})
    later = _ctx(db, "historian", clock=lambda: NOW + timedelta(hours=2))
    await _call(later, "append_notebook", {"note": f"afternoon {RUN}"})
    body = await _call(ctx, "read_notebook", {"date": str(usage_day(NOW))})
    assert [n["note_md"] for n in body["data"]["notes"] if RUN in n["note_md"]] == [
        f"afternoon {RUN}",
        f"morning {RUN}",
    ]
    replay = await _call(_ctx(db, "historian", replay_as_of=NOW + timedelta(hours=1)), "read_notebook", {})
    assert [n["note_md"] for n in replay["data"]["notes"] if RUN in n["note_md"]] == [f"morning {RUN}"]


async def test_memory_writes_are_refused_in_replay(db: sessionmaker[Session]) -> None:
    ctx = _ctx(db, "portfolio_manager", replay_as_of=NOW)
    assert (await _call(ctx, "append_notebook", {"note": f"x {RUN}"}))["error"] == "read_only_in_replay"
    entry = {"title": f"t {RUN}"}
    assert (await _call(ctx, "write_journal", {"entry": entry}))["error"] == "read_only_in_replay"


# -- journal ----------------------------------------------------------------------------------------


async def test_write_journal_then_read_journal_filters_by_instrument_and_tags(
    db: sessionmaker[Session],
) -> None:
    pm = _ctx(db, "portfolio_manager")
    first = await _call(
        pm,
        "write_journal",
        {
            "entry": {
                "title": f"XAU long stopped {RUN}",
                "inst_id": INST,
                "lessons_md": "Wait for CPI.",
                "tags": ["loss", "breakout", "loss"],
            }
        },
    )
    assert "error" not in first
    later = _ctx(db, "portfolio_manager", clock=lambda: NOW + timedelta(minutes=5))
    await _call(
        later, "write_journal", {"entry": {"title": f"XAU win {RUN}", "inst_id": INST, "tags": ["win"]}}
    )
    await _call(
        pm,
        "write_journal",
        {"entry": {"title": f"BTC loss {RUN}", "inst_id": "BTC-USDT-SWAP", "tags": ["loss"]}},
    )

    body = await _call(_ctx(db, "historian"), "read_journal", {"inst_id": INST})
    assert [e["title"] for e in body["data"]["entries"]] == [f"XAU win {RUN}", f"XAU long stopped {RUN}"]
    losses = await _call(_ctx(db, "historian"), "read_journal", {"inst_id": INST, "tags": ["loss"]})
    [entry] = losses["data"]["entries"]
    assert entry["tags"] == ["breakout", "loss"]
    assert entry["agent"] == "portfolio_manager"
    assert entry["lessons_md"] == "Wait for CPI."
    replay = await _call(
        _ctx(db, "historian", replay_as_of=NOW + timedelta(minutes=1)), "read_journal", {"inst_id": INST}
    )
    assert [e["title"] for e in replay["data"]["entries"]] == [f"XAU long stopped {RUN}"]


async def test_write_journal_is_refused_for_agents_the_matrix_does_not_grant(
    db: sessionmaker[Session],
) -> None:
    for agent in ("historian", "strategist", "orchestrator", "not_an_agent"):
        body = await _call(_ctx(db, agent), "write_journal", {"entry": {"title": f"t {RUN}"}})
        assert body["error"] == "forbidden", agent


async def test_write_journal_validates_the_entry(db: sessionmaker[Session]) -> None:
    pm = _ctx(db, "portfolio_manager")
    body = await _call(pm, "write_journal", {"entry": {"title": "", "bogus": 1}})
    assert body["error"] == "invalid_input"
    assert "bogus" in body["message"] and "title" in body["message"]
