"""ATL-64 DB tests: clustering against news_items / news_clusters with pgvector (FakeEmbedder only).

Runs on this checkout's test database (decision #239); run `cd backend && php artisan test` first so it
is migrated.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_ingest.clustering import assign, cluster_items
from atlas_ingest.embedding import FakeEmbedder
from atlas_ingest.items import PreparedItem
from atlas_shared import testing
from atlas_shared.db import NewsCluster, NewsItem, make_engine
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import OperationalError

T0 = datetime(2026, 10, 4, 3, 12, tzinfo=UTC)
SOURCE = "atl64.test"


@pytest.fixture(scope="module")
def engine() -> Engine:
    settings = testing.db_settings()
    eng = make_engine(settings)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{settings['DB_DATABASE']} is not reachable: {exc}")
    return eng


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(NewsItem))
            conn.execute(delete(NewsCluster))

    wipe()
    yield engine
    wipe()


def prepared(title: str, at: datetime, **kw: object) -> PreparedItem:
    slug = uuid.uuid4().hex
    return PreparedItem(
        source=SOURCE,
        url=f"https://example.test/{slug}",
        canonical_url=f"https://example.test/{slug}",
        title=title,
        published_at=at,
        fetched_at=at + timedelta(minutes=1),
        **kw,  # type: ignore[arg-type]
    )


def test_three_items_of_one_story_and_one_unrelated_make_two_clusters(clean: Engine) -> None:
    items = [
        # Fed out of order: the earliest-published one arrives last.
        prepared(
            "Bridge exploit: $48M stolen from Ethereum-Arbitrum liquidity bridge",
            T0 + timedelta(hours=2),
            instruments=["ETH-USDT", "ARB-USDT"],
            event_type="hack_exploit",
            importance=3,
        ),
        prepared(
            "Hacker drains $48 million from Ethereum-Arbitrum bridge in exploit",
            T0 + timedelta(hours=4),
            instruments=["ETH-USDT"],
            event_type="hack_exploit",
            importance=4,
        ),
        prepared("Tesla third-quarter deliveries rise 9%, beating forecasts", T0 + timedelta(hours=1)),
    ]
    with clean.begin() as conn:
        cluster_items(conn, items, FakeEmbedder())
    late_but_earliest = prepared(
        "Ethereum-Arbitrum bridge drained of $48 million in exploit",
        T0,
        instruments=["ETH-USDT"],
        event_type="hack_exploit",
        importance=2,
    )
    with clean.begin() as conn:
        cluster_items(conn, [late_but_earliest], FakeEmbedder())

    with clean.connect() as conn:
        clusters = conn.execute(select(NewsCluster).order_by(NewsCluster.item_count.desc())).all()
        assert [c.item_count for c in clusters] == [3, 1]
        story, tesla = clusters
        assert story.first_seen == T0
        assert story.last_seen == T0 + timedelta(hours=4)
        assert story.max_importance == 4
        assert story.instruments == ["ARB-USDT", "ETH-USDT"]
        assert tesla.headline.startswith("Tesla")

        per_cluster = dict(
            conn.execute(select(NewsItem.cluster_id, func.count()).group_by(NewsItem.cluster_id)).all()
        )
        assert per_cluster == {story.id: 3, tesla.id: 1}
        stored = conn.execute(select(NewsItem.embedding, NewsItem.novelty).limit(1)).one()
        assert len(stored.embedding) == 384
        assert stored.novelty is not None


def test_novelty_is_one_for_a_new_story_and_low_for_a_near_duplicate(clean: Engine) -> None:
    emb = FakeEmbedder()
    title = "US spot bitcoin ETFs pull in $1.2 billion in a single day, most since July"
    first = prepared(title, T0)
    dup = prepared(title.replace("pull in", "take in"), T0 + timedelta(minutes=40))
    with clean.begin() as conn:
        a1, a2 = cluster_items(conn, [first, dup], emb)
    assert a1.novelty == 1.0 and a1.created
    assert a2.novelty < 0.2 and not a2.created
    assert first.novelty == Decimal("1.0000")
    assert dup.novelty is not None and dup.novelty < Decimal("0.2")
    assert dup.cluster_id == first.cluster_id


def test_items_13_hours_apart_get_separate_clusters(clean: Engine) -> None:
    emb = FakeEmbedder()
    title = "OKX to delist several low-liquidity spot trading pairs on October 12"
    with clean.begin() as conn:
        first, later = cluster_items(
            conn, [prepared(title, T0), prepared(title, T0 + timedelta(hours=13))], emb
        )
    assert first.cluster_id != later.cluster_id
    assert later.created
    # Still within 72 h, so the repeat is not novel even in its own cluster.
    assert later.novelty < 0.01


def test_assign_alone_sets_columns_without_inserting(clean: Engine) -> None:
    it = prepared("Gold hits record above $4,050 an ounce", T0)
    (vec,) = FakeEmbedder().embed([it.title])
    with clean.begin() as conn:
        result = assign(conn, it, vec)
        assert conn.execute(select(func.count()).select_from(NewsItem)).scalar() == 0
        assert conn.execute(select(func.count()).select_from(NewsCluster)).scalar() == 1
    assert str(it.cluster_id) == result.cluster_id
    assert it.embedding == vec
    assert it.novelty == Decimal("1.0000")
