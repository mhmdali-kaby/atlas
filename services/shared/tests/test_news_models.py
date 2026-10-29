"""News, calendar and source-registry tables (ATL-57) through the SQLAlchemy models.

Needs the checkout's test DB migrated: run `cd backend && php artisan test` first. The
api_key test also runs `php artisan tinker` so the ciphertext is written by Laravel itself.
"""

import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from atlas_shared import testing
from atlas_shared.db import (
    EarningsEvent,
    EconomicEvent,
    InstrumentAlias,
    MarketSentiment,
    NewsCluster,
    NewsItem,
    NewsSource,
    make_engine,
)
from atlas_shared.laravel_crypt import decrypt_string
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TS = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
RUN = uuid.uuid4().hex[:8]
BACKEND = testing.repo_root() / "backend"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()
        s.execute(delete(NewsItem).where(NewsItem.source == f"test.{RUN}"))
        s.execute(delete(NewsCluster).where(NewsCluster.headline.like(f"%{RUN}%")))
        s.execute(delete(NewsSource).where(NewsSource.key.like(f"test.{RUN}%")))
        s.execute(delete(EconomicEvent).where(EconomicEvent.source == f"test.{RUN}"))
        s.execute(delete(EarningsEvent).where(EarningsEvent.symbol == f"T{RUN}"))
        s.execute(delete(MarketSentiment).where(MarketSentiment.index_key == f"test.{RUN}"))
        s.execute(delete(InstrumentAlias).where(InstrumentAlias.alias_normalized == f"alias {RUN}"))
        s.commit()


def _news_item(cluster_id: uuid.UUID | None = None, **values: Any) -> NewsItem:
    fields: dict[str, Any] = {
        "source": f"test.{RUN}",
        "url": f"https://x.test/{RUN}?utm=1",
        "canonical_url": f"https://x.test/{RUN}",
        "title": "Bitcoin climbs",
        "published_at": TS,
        "fetched_at": TS,
        "importance": 4,
        "cluster_id": cluster_id,
        **values,
    }
    return NewsItem(**fields)


def test_news_item_round_trips_embedding_and_text_arrays(session: Session) -> None:
    cluster = NewsCluster(
        headline=f"BTC rally {RUN}",
        first_seen=TS,
        last_seen=TS,
        item_count=1,
        max_importance=4,
        instruments=["BTC-USDT-SWAP"],
    )
    session.add(cluster)
    session.flush()
    cluster_id = cluster.id
    embedding = [i / 1000 for i in range(384)]
    session.add(
        _news_item(
            cluster_id,
            instruments=["BTC-USDT-SWAP", "BTC-USDT"],
            asset_classes=["crypto"],
            entities=[{"text": "Bitcoin", "target": "BTC-USDT"}],
            event_type="ETF_flow",
            sentiment=Decimal("0.4500"),
            novelty=Decimal("0.9100"),
            embedding=embedding,
            raw={"guid": RUN},
        )
    )
    session.commit()
    session.expunge_all()

    item = session.scalars(select(NewsItem).where(NewsItem.source == f"test.{RUN}")).one()
    assert item.instruments == ["BTC-USDT-SWAP", "BTC-USDT"]
    assert item.asset_classes == ["crypto"]
    assert item.embedding is not None and len(item.embedding) == 384
    assert item.embedding == pytest.approx(embedding, abs=1e-6)
    assert item.sentiment == Decimal("0.4500")
    assert item.event_type == "ETF_flow"
    assert item.cluster_id == cluster_id
    assert item.canonical_url_hash is not None and len(item.canonical_url_hash) == 32

    nearest = session.execute(
        text("SELECT embedding <=> CAST(:q AS vector) FROM news_items WHERE id = :id"),
        {"q": "[" + ",".join(str(v) for v in embedding) + "]", "id": item.id},
    ).scalar_one()
    assert nearest == pytest.approx(0.0, abs=1e-6)

    stored = session.scalars(select(NewsCluster).where(NewsCluster.id == cluster_id)).one()
    assert stored.instruments == ["BTC-USDT-SWAP"]


def test_news_item_dedupes_on_canonical_url(session: Session) -> None:
    session.add(_news_item())
    session.commit()
    session.add(_news_item(url="https://other.test/a"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    stmt = (
        insert(NewsItem)
        .values(
            source=f"test.{RUN}",
            url="https://other.test/b",
            canonical_url=f"https://x.test/{RUN}",
            title="dup",
            published_at=TS,
            fetched_at=TS,
            importance=2,
            id=uuid.uuid4(),
        )
        .on_conflict_do_nothing(index_elements=["canonical_url_hash"])
        .returning(NewsItem.id)
    )
    assert session.execute(stmt).all() == []
    session.commit()


def test_news_item_rejects_importance_6(session: Session) -> None:
    session.add(_news_item(importance=6))
    with pytest.raises(IntegrityError, match="news_items_importance_check"):
        session.commit()


def test_calendar_sentiment_alias_and_source_models_round_trip(session: Session) -> None:
    session.add_all(
        [
            EconomicEvent(
                source=f"test.{RUN}",
                external_id="cpi",
                country="USD",
                name="CPI m/m",
                importance=5,
                scheduled_at=TS,
                forecast="0.3%",
                previous="0.2%",
                surprise=Decimal("0.1"),
            ),
            EarningsEvent(
                symbol=f"T{RUN}",
                date=date(2026, 10, 29),
                time="amc",
                eps_est=Decimal("0.52"),
                rev_est=Decimal("25000000000"),
            ),
            MarketSentiment(index_key=f"test.{RUN}", value=Decimal("42"), classification="Fear", ts=TS),
            InstrumentAlias(
                alias=f"Alias {RUN}",
                alias_normalized=f"alias {RUN}",
                target_kind="instrument_family",
                target="TSLA-USDT",
                priority=10,
            ),
            NewsSource(
                key=f"test.{RUN}", kind="rss", name="Test feed", url="https://x.test/rss", poll_seconds=180
            ),
        ]
    )
    session.commit()
    session.expunge_all()

    event = session.scalars(select(EconomicEvent).where(EconomicEvent.source == f"test.{RUN}")).one()
    assert (event.importance, event.forecast, event.surprise) == (5, "0.3%", Decimal("0.1"))
    earnings = session.scalars(select(EarningsEvent).where(EarningsEvent.symbol == f"T{RUN}")).one()
    assert (earnings.date, earnings.rev_est) == (date(2026, 10, 29), Decimal("25000000000"))
    reading = session.scalars(select(MarketSentiment).where(MarketSentiment.index_key == f"test.{RUN}")).one()
    assert (reading.value, reading.ts) == (Decimal("42"), TS)
    alias = session.scalars(
        select(InstrumentAlias).where(InstrumentAlias.alias_normalized == f"alias {RUN}")
    ).one()
    assert (alias.target_kind, alias.target, alias.priority) == ("instrument_family", "TSLA-USDT", 10)
    source = session.scalars(select(NewsSource).where(NewsSource.key == f"test.{RUN}")).one()
    assert (source.enabled, source.requires_key, source.consecutive_failures, source.api_key) == (
        True,
        False,
        0,
        None,
    )


def _phpunit_app_key() -> str:
    xml = (BACKEND / "phpunit.xml").read_text(encoding="utf-8")
    match = re.search(r'name="APP_KEY"\s+value="([^"]+)"', xml)
    assert match, "APP_KEY missing from backend/phpunit.xml"
    return match.group(1)


@pytest.mark.skipif(shutil.which("php") is None, reason="php not installed")
def test_python_decrypts_an_api_key_written_by_laravel(session: Session) -> None:
    app_key = _phpunit_app_key()
    key = f"test.{RUN}.keyed"
    plaintext = f"cp-secret-{RUN}-9876"
    settings = testing.db_settings()
    env = {
        **os.environ,
        "APP_ENV": "testing",
        "APP_KEY": app_key,
        "DB_CONNECTION": "pgsql",
        **settings,
    }
    php = (
        "App\\Models\\NewsSource::query()->create(["
        f"'key' => '{key}', 'kind' => 'api', 'name' => 'Keyed', 'poll_seconds' => 180,"
        f"'requires_key' => true, 'api_key' => '{plaintext}']);"
    )
    php_bin = shutil.which("php")
    assert php_bin is not None
    # Fixed argv built from constants in this test; no shell.
    subprocess.run(  # noqa: S603
        [php_bin, "artisan", "tinker", f"--execute={php}"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )

    raw = session.execute(text("SELECT api_key FROM news_sources WHERE key = :k"), {"k": key}).scalar_one()
    assert isinstance(raw, str)
    assert plaintext not in raw
    assert decrypt_string(raw, app_key) == plaintext
