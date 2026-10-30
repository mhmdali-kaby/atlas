"""ATL-108: resolve_parent waits for atlas:bridge to store an agent-written mention.

The supervisor and the bridge read the same stream, so a mention may reach its handler before its
warroom_messages row exists. Without the wait, the reply would go to Main without reply_to and
Market Scout (which matches replies by reply_to) would time every teammate out.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.tools import ToolContext
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

NOW = datetime(2034, 6, 1, 5, 0, tzinfo=UTC)


@pytest.fixture
def thread_id(engine: Engine) -> str:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, 'demo', 'main', 'main', 'Main', :ts, :ts) "
                "ON CONFLICT (env, kind, key) DO NOTHING"
            ),
            {"id": str(uuid.uuid4()), "ts": NOW},
        )
        return str(
            conn.execute(
                text("SELECT id FROM warroom_threads WHERE env='demo' AND kind='main' AND key='main'")
            ).scalar_one()
        )


@pytest.fixture
def rows(engine: Engine) -> Iterator[list[str]]:
    created: list[str] = []
    yield created
    with engine.begin() as conn:
        for row in created:
            conn.execute(text("DELETE FROM warroom_messages WHERE id = :id"), {"id": row})


def _ctx(engine: Engine, replay_as_of: datetime | None = None) -> ToolContext:
    return ToolContext(
        env="demo",
        session_factory=sessionmaker(engine),
        agent="historian",
        correlation_id="test",
        replay_as_of=replay_as_of,
        clock=lambda: NOW,
        publish=lambda stream, envelope: None,
    )


def _request(event_id: str, author_type: str = "agent") -> MentionRequest:
    return MentionRequest(
        event_id=event_id,
        env="demo",
        author="market_scout" if author_type == "agent" else "owner",
        author_type=author_type,
        content_md="@Historian regime check",
        correlation_id="scout_watchlist:2034-06-01",
        ts=NOW,
        depth=1 if author_type == "agent" else 0,
    )


def _store(engine: Engine, thread: str, event_id: str, rows: list[str]) -> str:
    row_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, created_at, updated_at) VALUES (:id, 'demo', :thread, :event, 'agent', "
                "'market_scout', 'text', '@Historian regime check', :ts, :ts)"
            ),
            {"id": row_id, "thread": thread, "event": event_id, "ts": NOW - timedelta(seconds=1)},
        )
    rows.append(row_id)
    return row_id


def test_an_agent_mention_stored_late_by_the_bridge_is_found(
    engine: Engine, thread_id: str, rows: list[str]
) -> None:
    event_id = str(uuid.uuid4())
    stored: list[str] = []

    def bridge() -> None:
        time.sleep(0.3)
        stored.append(_store(engine, thread_id, event_id, rows))

    worker = threading.Thread(target=bridge)
    worker.start()
    try:
        started = time.monotonic()
        found = resolve_parent(_ctx(engine), _request(event_id), wait_seconds=2)
        elapsed = time.monotonic() - started
    finally:
        worker.join()
    assert found == (stored[0], thread_id)
    assert 0.25 <= elapsed < 2


def test_a_mention_never_stored_gives_up_after_the_wait(engine: Engine) -> None:
    started = time.monotonic()
    found = resolve_parent(_ctx(engine), _request(str(uuid.uuid4())), wait_seconds=0.3)
    elapsed = time.monotonic() - started
    assert found == (None, None)
    assert 0.3 <= elapsed < 0.6 + 0.2  # one poll interval of slack


@pytest.mark.parametrize("case", ["owner", "replay"])
def test_owner_mentions_and_replays_do_not_wait(engine: Engine, case: str) -> None:
    if case == "owner":
        ctx, request = _ctx(engine), _request(str(uuid.uuid4()), author_type="human")
    else:
        ctx, request = _ctx(engine, replay_as_of=NOW), _request(str(uuid.uuid4()))
    started = time.monotonic()
    found = resolve_parent(ctx, request, wait_seconds=2)
    assert found == (None, None)
    assert time.monotonic() - started < 0.2


def test_a_mention_carrying_its_row_is_returned_without_a_lookup() -> None:
    def no_db() -> None:
        raise AssertionError("looked up a mention that already carried its row")

    ctx = ToolContext(
        env="demo",
        session_factory=no_db,  # type: ignore[arg-type]
        agent="historian",
        correlation_id="test",
        clock=lambda: NOW,
        publish=lambda stream, envelope: None,
    )
    request = MentionRequest(
        event_id=str(uuid.uuid4()),
        env="demo",
        author="owner",
        author_type="human",
        content_md="@Historian hi",
        correlation_id=None,
        ts=NOW,
        row_id="row",
        thread_id="thread",
    )
    assert resolve_parent(ctx, request) == ("row", "thread")
