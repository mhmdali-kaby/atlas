"""Code-computed news cluster status and price move since first seen (ATL-106, decision #247 (4)).

Needs the checkout's test DB migrated (`cd backend && php artisan test` first).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents.schemas import NewsBrief
from atlas_agents.tools import framework as fw
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_agents.tools.news_status import cluster_statuses, decide_status, price_move_since
from atlas_shared.db import Candle, NewsCluster, NewsItem, NewsSource
from sqlalchemy import Engine, delete, event
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

RUN = uuid.uuid4().hex[:10]
OFFICIAL = f"test.{RUN}.official"
REP_A = f"test.{RUN}.rep_a"
REP_B = f"test.{RUN}.rep_b"
LOW_A = f"test.{RUN}.low_a"
LOW_B = f"test.{RUN}.low_b"
LOW_C = f"test.{RUN}.low_c"
INST = f"N{RUN}-USDT-SWAP"
NO_CANDLES = f"M{RUN}-USDT-SWAP"
NO_HOURLY = f"H{RUN}-USDT-SWAP"
DAY = datetime(2026, 10, 20, tzinfo=UTC)
FIRST_SEEN = DAY + timedelta(hours=15, minutes=10)
NOW = DAY + timedelta(hours=17, minutes=5)


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    factory = sessionmaker(bind=engine)
    flags = {
        OFFICIAL: (False, True),
        REP_A: (True, False),
        REP_B: (True, False),
        LOW_A: (False, False),
        LOW_B: (False, False),
        LOW_C: (False, False),
    }
    with factory() as s:
        for key, (reputable, official) in flags.items():
            s.add(
                NewsSource(
                    key=key,
                    provider=f"test{RUN}",
                    kind="rss",
                    category="crypto_news",
                    name=key,
                    poll_seconds=300,
                    reputable=reputable,
                    official=official,
                )
            )
        s.commit()
    yield factory
    with engine.begin() as conn:
        conn.execute(delete(NewsItem).where(NewsItem.url.like(f"%{RUN}%")))
        conn.execute(delete(NewsCluster).where(NewsCluster.headline.like(f"%{RUN}%")))
        conn.execute(delete(NewsSource).where(NewsSource.key.like(f"test.{RUN}.%")))
        conn.execute(delete(Candle).where(Candle.inst_id.in_([INST, NO_CANDLES, NO_HOURLY])))


def _ctx(
    factory: sessionmaker[Session], recorder: Recorder | None = None, agent: str = "news_intelligence"
) -> ToolContext:
    return ToolContext(
        env="demo",
        session_factory=factory,
        agent=agent,
        replay_as_of=NOW,
        publish=recorder if recorder is not None else Recorder(),
    )


async def _call(ctx: ToolContext, server: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await fw.make_handler(fw.get_tool(fq_name(server, name)), ctx)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    body["is_error"] = bool(result.get("is_error"))
    return body


def _cluster(
    factory: sessionmaker[Session],
    sources: list[str],
    instruments: list[str] | None = None,
    first_seen: datetime = FIRST_SEEN,
) -> str:
    with factory() as s:
        cluster = NewsCluster(
            headline=f"Story {RUN}",
            first_seen=first_seen,
            last_seen=first_seen,
            item_count=0,
            max_importance=3,
        )
        s.add(cluster)
        s.flush()
        for i, source in enumerate(sources):
            slug = uuid.uuid4().hex[:8]
            published = first_seen + timedelta(minutes=5 * i)
            s.add(
                NewsItem(
                    source=source,
                    url=f"https://news.test/{RUN}/{slug}",
                    canonical_url=f"https://news.test/{RUN}/{slug}",
                    title=f"Exchange lists token {RUN}",
                    summary="A listing.",
                    published_at=published,
                    fetched_at=published + timedelta(minutes=1),
                    importance=3,
                    instruments=instruments if instruments is not None else [INST],
                    cluster_id=cluster.id,
                    created_at=published,
                    updated_at=published,
                )
            )
        s.commit()
        return str(cluster.id)


def _candle(s: Session, inst: str, bar: str, ts: datetime, h: str, lo: str, c: str) -> None:
    s.add(
        Candle(
            env="demo",
            inst_id=inst,
            bar=bar,
            ts=ts,
            o=Decimal(c),
            h=Decimal(h),
            l=Decimal(lo),
            c=Decimal(c),
            vol=Decimal("10"),
            confirmed=True,
        )
    )


def _candles(factory: sessionmaker[Session]) -> None:
    """1H bars 00:00..14:00 on DAY: close 100, range 99-101 (TR 2), except 14:00 at 98-102 (TR 4).
    A 15:00 bar (closes 16:00, after first_seen 15:10) has a huge range and must not count. Price
    now: the 1m bar 17:03 (closes 17:04 <= NOW 17:05) closes at 103; the 17:04 bar closes at 17:05
    too and wins; the 17:05 bar closes after NOW and must not count."""
    with factory() as s:
        for hour in range(14):
            _candle(s, INST, "1H", DAY + timedelta(hours=hour), "101", "99", "100")
        _candle(s, INST, "1H", DAY + timedelta(hours=14), "102", "98", "100")
        _candle(s, INST, "1H", DAY + timedelta(hours=15), "150", "50", "140")
        _candle(s, INST, "1m", DAY + timedelta(hours=17, minutes=3), "103", "102", "102.5")
        _candle(s, INST, "1m", DAY + timedelta(hours=17, minutes=4), "104", "102", "103")
        _candle(s, INST, "1m", DAY + timedelta(hours=17, minutes=5), "999", "998", "999")
        _candle(s, NO_HOURLY, "1m", DAY + timedelta(hours=17), "10", "9", "9.5")
        s.commit()


# -- criterion 1: status ------------------------------------------------------------------------------


def test_decide_status_rules() -> None:
    trust = {"o": (False, True), "r1": (True, False), "r2": (True, False), "n1": (False, False)}
    assert decide_status(["o"], trust).status == "CONFIRMED"
    assert decide_status(["r1", "r2"], trust).status == "CONFIRMED"
    assert decide_status(["r1", "r1"], trust).status == "REPORTED"  # one source twice is one source
    assert decide_status(["r1", "n1", "unknown"], trust).status == "REPORTED"
    rumour = decide_status(["n1", "unknown"], trust)
    assert rumour.status == "RUMOUR" and rumour.decided_by == ["n1", "unknown"]


@pytest.mark.parametrize(
    ("sources", "status", "rule", "decided_by"),
    [
        ([OFFICIAL], "CONFIRMED", "official_source", [OFFICIAL]),
        ([REP_A, REP_B], "CONFIRMED", "two_reputable_sources", [REP_A, REP_B]),
        ([REP_A, LOW_A, LOW_B], "REPORTED", "one_reputable_source", [REP_A]),
        ([LOW_A, LOW_B, LOW_C], "RUMOUR", "no_reputable_source", [LOW_A, LOW_B, LOW_C]),
    ],
)
async def test_cluster_status_from_source_flags(
    db: sessionmaker[Session], sources: list[str], status: str, rule: str, decided_by: list[str]
) -> None:
    cid = _cluster(db, sources)
    ctx = _ctx(db)

    one = await _call(ctx, "news", "get_story_cluster", {"cluster_id": cid})
    assert one["is_error"] is False
    assert one["data"]["status"] == status
    assert one["data"]["status_rule"] == rule
    assert one["data"]["status_sources"] == sorted(decided_by)

    found = await _call(ctx, "news", "search_news", {"inst_ids": [INST], "limit": 20})
    clusters = {c["cluster_id"]: c for c in found["data"]["clusters"]}
    assert clusters[cid]["status"] == status
    assert clusters[cid]["status_sources"] == sorted(decided_by)
    assert clusters[cid]["sources"] == sorted(set(sources))


async def test_status_in_replay_counts_only_sources_known_by_then(db: sessionmaker[Session]) -> None:
    cid = _cluster(db, [REP_A, REP_B])  # REP_B published at FIRST_SEEN + 5 min, fetched +6 min
    early = ToolContext(
        env="demo",
        session_factory=db,
        agent="news_intelligence",
        replay_as_of=FIRST_SEEN + timedelta(minutes=3),
        publish=Recorder(),
    )
    body = await _call(early, "news", "get_story_cluster", {"cluster_id": cid})
    assert body["data"]["status"] == "REPORTED"


# -- criterion 2: price move since first seen ---------------------------------------------------------


async def test_price_move_matches_a_hand_calculation(db: sessionmaker[Session]) -> None:
    _candles(db)
    cid = _cluster(db, [LOW_A], instruments=[INST, NO_CANDLES, NO_HOURLY])

    body = await _call(_ctx(db), "news", "get_story_cluster", {"cluster_id": cid})
    moves = {m["inst_id"]: m for m in body["data"]["price_move_since_first_seen_atr"]}

    # Reference: the last 1H bar closed by 15:10 is 14:00 (close 100). TR = 2 for bars 00:00..13:00
    # and 4 for 14:00, so Wilder ATR(14) = (2 * 13 + 4) / 14 = 30/14. Price now = 103 (1m bar 17:04).
    # (103 - 100) / (30/14) = 1.4
    m = moves[INST]
    assert m["reason"] is None
    assert m["ref_close"] == 100.0
    assert m["atr"] == pytest.approx(30 / 14)
    assert m["price_now"] == 103.0
    assert m["value"] == pytest.approx(1.4)
    assert datetime.fromisoformat(m["ref_ts"]) == DAY + timedelta(hours=14)

    assert moves[NO_CANDLES]["value"] is None
    assert "no closed 1m candle" in moves[NO_CANDLES]["reason"]
    assert moves[NO_HOURLY]["value"] is None
    assert "1H" in moves[NO_HOURLY]["reason"] and "missing" in moves[NO_HOURLY]["reason"]

    found = await _call(_ctx(db), "news", "search_news", {"inst_ids": [INST], "limit": 20})
    cluster = next(c for c in found["data"]["clusters"] if c["cluster_id"] == cid)
    by_inst = {m["inst_id"]: m for m in cluster["price_move_since_first_seen_atr"]}
    assert by_inst[INST]["value"] == pytest.approx(1.4)


def test_clusters_on_one_instrument_query_candles_once(db: sessionmaker[Session], engine: Engine) -> None:
    """Two clusters on INST (first seen 15:10 and 14:40) plus one on NO_HOURLY: the values equal the
    one-at-a-time calculation, and candles are read in two statements (price now for every
    instrument, then one 1H load per instrument with hourly history)."""
    _candles(db)
    early = FIRST_SEEN - timedelta(minutes=30)
    a = _cluster(db, [LOW_A], instruments=[INST])
    b = _cluster(db, [LOW_B], instruments=[INST, NO_HOURLY], first_seen=early)
    ctx = _ctx(db)

    with db() as s:
        expected = {
            (a, INST): price_move_since(s, ctx, INST, FIRST_SEEN),
            (b, INST): price_move_since(s, ctx, INST, early),
            (b, NO_HOURLY): price_move_since(s, ctx, NO_HOURLY, early),
        }

    candle_queries: list[str] = []

    def count(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if "FROM candles" in statement:
            candle_queries.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        out = cluster_statuses(ctx, [a, b])
    finally:
        event.remove(engine, "before_cursor_execute", count)

    got = {(cid, m.inst_id): m for cid in (a, b) for m in out[cid].price_move_since_first_seen_atr}
    assert got == expected
    assert got[(a, INST)].value == pytest.approx(1.4)
    # 1 DISTINCT ON price-now query + 1H loads for INST and NO_HOURLY (one each, not one per cluster).
    assert len(candle_queries) == 3, candle_queries


# -- criterion 3: the card validator overwrites the model's labels --------------------------------------


def _brief(cid: str, **cluster_over: Any) -> dict[str, Any]:
    cluster: dict[str, Any] = {
        "cluster_id": cid,
        "headline": "Exchange lists token",
        "status": "CONFIRMED",
        "status_sources": ["reuters"],
        "sources": [
            {"name": "Some blog", "url": "https://news.test/x", "published_at": "2026-10-20T15:10:00Z"}
        ],
        "event_type": "listing",
        "importance": 3,
        "sentiment": 0.4,
        "first_seen": "2026-10-20T09:00:00Z",
        "price_move_since_first_seen_atr": [{"inst_id": INST, "value": 0.1}],
        **cluster_over,
    }
    return {
        "type": "news_brief",
        "inst_ids": [INST],
        "clusters": [cluster],
        "upcoming_events": [
            {
                "time": "2026-10-21T12:30:00Z",
                "name": "US CPI",
                "forecast": "0.3%",
                "previous": "0.2%",
                "sensitive_instruments": [INST],
            }
        ],
        "net_sentiment_24h": 0.2,
        "net_sentiment_72h": 0.1,
        "priced_in": "partly",
        "what_to_watch": "Follow-through above the listing-day high.",
        "data_as_of": "2026-10-20T17:04:00Z",
    }


async def test_news_brief_card_stores_the_code_status_not_the_models(db: sessionmaker[Session]) -> None:
    _candles(db)
    cid = _cluster(db, [LOW_A, LOW_B])
    rec = Recorder()

    body = await _call(
        _ctx(db, rec),
        "warroom",
        "post_message",
        {"kind": "research_report", "content_md": "Listing story is CONFIRMED.", "card": _brief(cid)},
    )

    assert body["is_error"] is False, body
    (_stream, envelope), *rest = rec.events
    assert rest == []
    card = NewsBrief.model_validate(envelope.payload["card"])
    stored = card.clusters[0]
    assert stored.status == "RUMOUR"
    assert stored.status_sources == sorted([LOW_A, LOW_B])
    assert stored.first_seen == FIRST_SEEN
    assert [(m.inst_id, m.value) for m in stored.price_move_since_first_seen_atr] == [
        (INST, pytest.approx(1.4))
    ]
    # What the model wrote that code does not own is kept.
    assert stored.headline == "Exchange lists token" and stored.sentiment == 0.4
    assert card.priced_in == "partly"


async def test_news_brief_card_with_an_unknown_cluster_is_refused(db: sessionmaker[Session]) -> None:
    rec = Recorder()
    body = await _call(
        _ctx(db, rec),
        "warroom",
        "post_message",
        {"kind": "research_report", "content_md": "x", "card": _brief(str(uuid.uuid4()))},
    )
    assert body["is_error"] is True
    assert "invalid_card" in json.dumps(body)
    assert rec.events == []


async def test_news_brief_is_only_a_research_report_card(db: sessionmaker[Session]) -> None:
    cid = _cluster(db, [LOW_A])
    body = await _call(
        _ctx(db), "warroom", "post_message", {"kind": "text", "content_md": "x", "card": _brief(cid)}
    )
    assert body["is_error"] is True
