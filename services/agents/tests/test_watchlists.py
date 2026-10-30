"""save_watchlist and atlas_data.get_watchlist (ATL-102) against this checkout's test DB.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from atlas_agents.schemas import WatchlistPick
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_agents.watchlists import WatchlistError, baghdad_today, save_watchlist
from atlas_shared.db import Watchlist, WatchlistItem
from pydantic import ValidationError
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
CONFIG = {"max_result_tokens": 1_000_000, "chars_per_token": 4, "trim_rows": 10_000}
# 22:30 UTC on the 30th is 01:30 on the 31st in Asia/Baghdad (UTC+3).
LATE_UTC = datetime(2026, 10, 30, 22, 30, tzinfo=UTC)
DAY_30 = date(2026, 10, 30)
DAY_31 = date(2026, 10, 31)
AS_OF = datetime(2026, 10, 31, 4, 55, tzinfo=UTC)


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with factory() as s:
            # Watchlists are env-wide per date; start every test from an empty pair of tables.
            s.execute(delete(WatchlistItem))
            s.execute(delete(Watchlist))
            s.commit()
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _ctx(
    factory: sessionmaker[Session], *, now: datetime = AS_OF, replay_as_of: datetime | None = None
) -> ToolContext:
    return ToolContext(
        env=ENV, session_factory=factory, agent="test_atl102", replay_as_of=replay_as_of, clock=lambda: now
    )


async def _get(ctx: ToolContext, args: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = fw.get_tool(fq_name("atlas_data", "get_watchlist"))
    result = await fw.make_handler(tool, ctx, CONFIG)(args or {})
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    assert result.get("is_error") is not True, body
    return body


def _pick(rank: int, inst_id: str, **over: Any) -> dict[str, Any]:
    pick: dict[str, Any] = {
        "inst_id": inst_id,
        "rank": rank,
        "asset_class": "crypto",
        "bias": "long",
        "conviction": 0.62,
        "thesis": f"{inst_id} broke its 20-day high on rising OI.",
        "catalysts": [
            {
                "name": "US CPI",
                "time": "2026-10-31T12:30:00Z",
                "source": "forexfactory",
                "importance": 5,
            }
        ],
        "key_levels": {"support": 100.5, "resistance": 120.25, "invalidation": 98.0},
        "invalidation": "Daily close back below 98.0",
        "data_as_of": "2026-10-31T04:50:00Z",
    }
    pick.update(over)
    return pick


THREE = [
    _pick(2, "ETH-USDT-SWAP", bias="short", conviction=0.55),
    _pick(1, "BTC-USDT-SWAP", conviction=0.71),
    _pick(3, "XAU-USDT-SWAP", asset_class="commodity", bias="neutral", catalysts=[], key_levels={}),
]


def _count(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as s:
        return (
            s.scalar(select(func.count()).select_from(Watchlist)) or 0,
            s.scalar(select(func.count()).select_from(WatchlistItem)) or 0,
        )


async def test_saves_three_picks_and_reads_them_back_in_rank_order(db: sessionmaker[Session]) -> None:
    msg = uuid.uuid4()
    window = datetime(2026, 10, 31, 5, 10, tzinfo=UTC)
    with db() as s:
        saved = save_watchlist(
            s,
            ENV,
            DAY_31,
            THREE,
            "published",
            edit_window_ends_at=window,
            message_id=msg,
            data={"backdrop": "BTC trending up"},
            created_at=datetime(2026, 10, 31, 5, 0, tzinfo=UTC),
        )
        s.commit()
        saved_id = saved.id

    body = await _get(_ctx(db, now=datetime(2026, 10, 31, 6, 0, tzinfo=UTC)))
    assert body["source"] == "postgres:watchlists,watchlist_items"
    assert datetime.fromisoformat(body["as_of"]) == datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
    data = body["data"]
    assert data["available"] is True
    assert data["date"] == "2026-10-31"
    assert data["watchlist_id"] == str(saved_id)
    assert data["version"] == 1
    assert data["status"] == "published"
    assert data["created_by"] == "market_scout"
    assert data["message_id"] == str(msg)
    assert datetime.fromisoformat(data["edit_window_ends_at"]) == window
    assert data["data"] == {"backdrop": "BTC trending up"}
    assert [i["rank"] for i in data["items"]] == [1, 2, 3]
    assert [i["inst_id"] for i in data["items"]] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "XAU-USDT-SWAP"]

    by_inst = {p["inst_id"]: p for p in THREE}
    for item in data["items"]:
        want = WatchlistPick.model_validate(by_inst[item["inst_id"]])
        assert item["asset_class"] == want.asset_class
        assert item["bias"] == want.bias
        assert item["conviction"] == pytest.approx(want.conviction)
        assert item["thesis"] == want.thesis
        assert item["catalysts"] == [c.model_dump(mode="json") for c in want.catalysts]
        assert item["key_levels"] == want.key_levels
        assert item["invalidation"] == want.invalidation
        assert item["pinned_by_owner"] is False
        assert datetime.fromisoformat(item["data_as_of"]) == want.data_as_of
    assert data["items"][0]["catalysts"][0]["importance"] == 5


async def test_default_date_is_today_in_baghdad_not_utc(db: sessionmaker[Session]) -> None:
    assert baghdad_today(LATE_UTC) == DAY_31
    with db() as s:
        save_watchlist(
            s,
            ENV,
            DAY_30,
            [_pick(1, "SOL-USDT-SWAP")],
            "published",
            created_at=LATE_UTC - timedelta(hours=14),
        )
        save_watchlist(
            s,
            ENV,
            DAY_31,
            [_pick(1, "BTC-USDT-SWAP")],
            "published",
            created_at=LATE_UTC - timedelta(minutes=5),
        )
        s.commit()

    data = (await _get(_ctx(db, now=LATE_UTC)))["data"]
    assert data["date"] == "2026-10-31"
    assert [i["inst_id"] for i in data["items"]] == ["BTC-USDT-SWAP"]

    explicit = (await _get(_ctx(db, now=LATE_UTC), {"date": "2026-10-30"}))["data"]
    assert [i["inst_id"] for i in explicit["items"]] == ["SOL-USDT-SWAP"]


async def test_replay_does_not_see_a_watchlist_created_after_the_cutoff(db: sessionmaker[Session]) -> None:
    cutoff = datetime(2026, 10, 31, 6, 0, tzinfo=UTC)
    with db() as s:
        save_watchlist(
            s,
            ENV,
            DAY_31,
            [_pick(1, "LATE-USDT-SWAP")],
            "published",
            created_at=cutoff + timedelta(minutes=1),
        )
        s.commit()

    body = await _get(_ctx(db, replay_as_of=cutoff))
    assert body["data"]["available"] is False
    assert body["data"]["items"] == []
    assert body["data"]["reason"]
    assert datetime.fromisoformat(body["as_of"]) == cutoff

    # A morning version (v1) and an intraday re-run after the cutoff (v2): replay reads v1, live v2.
    with db() as s:
        save_watchlist(
            s, ENV, DAY_30, [_pick(1, "EARLY-USDT-SWAP")], "published", created_at=cutoff - timedelta(days=1)
        )
        save_watchlist(
            s,
            ENV,
            DAY_30,
            [_pick(1, "RERUN-USDT-SWAP")],
            "published",
            created_at=cutoff + timedelta(minutes=1),
        )
        s.commit()
    data = (await _get(_ctx(db, replay_as_of=cutoff), {"date": "2026-10-30"}))["data"]
    assert data["version"] == 1
    assert [i["inst_id"] for i in data["items"]] == ["EARLY-USDT-SWAP"]
    live = (await _get(_ctx(db, now=cutoff + timedelta(hours=1)), {"date": "2026-10-30"}))["data"]
    assert live["version"] == 2
    assert [i["inst_id"] for i in live["items"]] == ["RERUN-USDT-SWAP"]


def test_invalid_pick_writes_nothing(db: sessionmaker[Session]) -> None:
    with db() as s, pytest.raises(ValidationError, match="conviction"):
        save_watchlist(
            s,
            ENV,
            DAY_31,
            [_pick(1, "BTC-USDT-SWAP"), _pick(2, "ETH-USDT-SWAP", conviction=1.2)],
            "published",
        )
    assert _count(db) == (0, 0)


@pytest.mark.parametrize(
    ("day", "picks", "status", "match"),
    [
        (
            datetime(2026, 10, 31, 1, 0, tzinfo=UTC),
            [_pick(1, "BTC-USDT-SWAP")],
            "published",
            "not a timestamp",
        ),
        (DAY_31, [_pick(1, "BTC-USDT-SWAP"), _pick(1, "ETH-USDT-SWAP")], "published", "ranks must be unique"),
        (
            DAY_31,
            [_pick(1, "BTC-USDT-SWAP"), _pick(2, "BTC-USDT-SWAP")],
            "published",
            "instruments must be unique",
        ),
        (DAY_31, [_pick(1, "BTC-USDT-SWAP")], "empty", "no picks"),
        (DAY_31, [], "published", "at least one pick"),
        (DAY_31, [_pick(1, "BTC-USDT-SWAP")], "superseded", "status must be"),
    ],
)
def test_refuses_bad_watchlists(
    db: sessionmaker[Session], day: date, picks: list[dict[str, Any]], status: Any, match: str
) -> None:
    with db() as s, pytest.raises(WatchlistError, match=match):
        save_watchlist(s, ENV, day, picks, status)
    assert _count(db) == (0, 0)


async def test_reruns_add_versions_and_removed_items_are_hidden(db: sessionmaker[Session]) -> None:
    t0 = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
    with db() as s:
        first = save_watchlist(
            s,
            ENV,
            DAY_31,
            [_pick(1, "BTC-USDT-SWAP")],
            "partial_evidence",
            reason="News timed out",
            created_at=t0,
        )
        empty = save_watchlist(
            s,
            ENV,
            DAY_31,
            [],
            "empty",
            reason="Shortlist empty after filters",
            created_at=t0 + timedelta(hours=2),
        )
        third = save_watchlist(s, ENV, DAY_31, THREE, "published", created_at=t0 + timedelta(hours=3))
        s.commit()
        assert (first.version, empty.version, third.version) == (1, 2, 3)
        removed_at = t0 + timedelta(hours=4)
        s.execute(
            WatchlistItem.__table__.update()  # type: ignore[attr-defined]
            .where(WatchlistItem.watchlist_id == third.id, WatchlistItem.inst_id == "ETH-USDT-SWAP")
            .values(removed_by_owner_at=removed_at)
        )
        s.commit()

    live = (await _get(_ctx(db, now=t0 + timedelta(hours=5))))["data"]
    assert live["version"] == 3
    assert [i["inst_id"] for i in live["items"]] == ["BTC-USDT-SWAP", "XAU-USDT-SWAP"]

    # Before the owner removed it, the pick was still on the list.
    before = (await _get(_ctx(db, replay_as_of=removed_at - timedelta(minutes=1))))["data"]
    assert [i["inst_id"] for i in before["items"]] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "XAU-USDT-SWAP"]

    at_empty = (await _get(_ctx(db, replay_as_of=t0 + timedelta(hours=2, minutes=30))))["data"]
    assert at_empty["available"] is True
    assert at_empty["status"] == "empty"
    assert at_empty["items"] == []
    assert at_empty["reason"] == "Shortlist empty after filters"


def test_live_and_demo_versions_are_independent(db: sessionmaker[Session]) -> None:
    with db() as s:
        demo = save_watchlist(s, "demo", DAY_31, [_pick(1, "BTC-USDT-SWAP")], "published")
        live = save_watchlist(s, "live", DAY_31, [_pick(1, "BTC-USDT-SWAP")], "published")
        s.commit()
        assert (demo.version, live.version) == (1, 1)
    with db() as s, pytest.raises(WatchlistError, match="env"):
        save_watchlist(s, "paper", DAY_31, [_pick(1, "BTC-USDT-SWAP")], "published")
