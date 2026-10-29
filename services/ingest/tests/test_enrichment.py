"""ATL-65: every ingested item is enriched, and high-importance news raises one news.alert per story.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. Feeds are served by respx: the recorded CoinDesk feed
(tests/fixtures/rss/coindesk_rss.xml, ATL-59) and two documented-shape CNBC/MarketWatch feeds
telling one Tesla earnings story (written for this task, see their header comments).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_ingest.collectors import discover
from atlas_ingest.embedding import FakeEmbedder
from atlas_ingest.enrich import backfill, main, parse_since
from atlas_ingest.enrichment import (
    ALERT_NAMESPACE,
    ALERT_STREAM,
    AlertRules,
    EnrichContext,
    Enricher,
    alert_envelope,
    alert_payload,
)
from atlas_ingest.extract import Extractor
from atlas_ingest.items import PreparedItem
from atlas_ingest.linking import AliasRow, Linker
from atlas_ingest.pipeline import Pipeline
from atlas_ingest.runner import IngestRunner, RunnerConfig
from atlas_shared import testing
from atlas_shared.db import NewsCluster, NewsItem, NewsSource, PositionSnapshot, SyncStatus, make_engine
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import OperationalError

logger = logging.getLogger("atlas_ingest.tests.latency")

FIXTURES = Path(__file__).parent / "fixtures"
CNBC_URL = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
MW_URL = "https://feeds.content.dowjones.io/public/rss/mw_topstories"
COINDESK_URL = "https://www.coindesk.com/arc/outboundfeeds/rss"
ENV = "demo"
APP_KEY = "base64:" + "a" * 44
CLOCK_START = datetime(2026, 10, 29, 12, 5, tzinfo=UTC)
ENVELOPE_FIXTURE = FIXTURES / "alerts" / "news_alert_envelope.json"

TEST_DB = testing.db_settings()

LINKER = Linker(
    [
        AliasRow("Tesla", "instrument_family", "TSLA-USDT", 10),
        AliasRow("TSLA", "instrument_family", "TSLA-USDT", 0),
        AliasRow("bitcoin", "instrument_family", "BTC-USDT", 10),
        AliasRow("BTC", "instrument_family", "BTC-USDT", 0),
    ],
    {"TSLA-USDT": "stock", "BTC-USDT": "crypto"},
)


class Recorder:
    """A publisher that keeps what it was given, with the monotonic time of each publish."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, EventEnvelope, float]] = []
        self.fail = fail

    def __call__(self, stream: str, envelope: EventEnvelope) -> str:
        if self.fail:
            raise ConnectionError("redis down")
        self.sent.append((stream, envelope, time.monotonic()))
        return "1-0"

    @property
    def envelopes(self) -> list[EventEnvelope]:
        return [e for _, e, _ in self.sent]


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    discover()
    return eng


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(NewsItem))
            conn.execute(delete(NewsCluster))
            conn.execute(delete(NewsSource))
            conn.execute(delete(PositionSnapshot).where(PositionSnapshot.env == ENV))
            conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == "account"))

    wipe()
    yield engine
    wipe()


def add_source(engine: Engine, key: str, url: str, name: str, category: str = "markets_news") -> None:
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            NewsSource.__table__.insert().values(
                id=uuid.uuid4(),
                key=key,
                kind="rss",
                category=category,
                name=name,
                url=url,
                enabled=True,
                poll_seconds=60,
                requires_key=False,
                consecutive_failures=0,
                created_at=now,
                updated_at=now,
            )
        )


def hold(engine: Engine, inst_id: str, taken_at: datetime, pos: str = "3") -> None:
    with engine.begin() as conn:
        conn.execute(
            PositionSnapshot.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                pos_side="net",
                pos=Decimal(pos),
                taken_at=taken_at,
                created_at=taken_at,
                updated_at=taken_at,
            )
        )


def rss(name: str) -> httpx.Response:
    return httpx.Response(
        200, content=(FIXTURES / "rss" / name).read_bytes(), headers={"content-type": "application/rss+xml"}
    )


def make_enricher(publish: Recorder, **kw: Any) -> Enricher:
    return Enricher(env=ENV, embedder=FakeEmbedder(), linker=LINKER, publish=publish, **kw)


def make_runner(
    engine: Engine, http: httpx.AsyncClient, clock: FakeClock, enricher: Enricher
) -> IngestRunner:
    pipeline = Pipeline(
        engine=engine,
        extractor=Extractor(http=http, timeout_seconds=2, max_bytes=2_000_000, user_agent="Browser/1.0"),
        max_extract_per_poll=25,
        enrich=enricher,
    )
    return IngestRunner(
        engine=engine,
        http=http,
        pipeline=pipeline,
        config=RunnerConfig(reload_seconds=60, failure_threshold=3, disable_minutes=30),
        app_key=APP_KEY,
        clock=clock,
        env=ENV,
    )


def items(engine: Engine, source: str | None = None) -> list[Any]:
    stmt = select(NewsItem.__table__).order_by(NewsItem.published_at)
    if source:
        stmt = stmt.where(NewsItem.source == source)
    with engine.connect() as conn:
        return list(conn.execute(stmt).all())


def cluster(engine: Engine, cluster_id: uuid.UUID) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(NewsCluster.__table__).where(NewsCluster.id == cluster_id)).one()


ENVELOPE_KEYS = ["id", "type", "ts", "env", "source", "correlation_id", "payload"]
PAYLOAD_KEYS = {
    "news_item_id",
    "cluster_id",
    "headline",
    "source",
    "source_name",
    "category",
    "url",
    "published_at",
    "importance",
    "event_type",
    "inst_ids",
    "asset_classes",
    "sentiment",
    "novelty",
    "reason",
    "held_inst_ids",
}


def assert_envelope_shape(env: EventEnvelope) -> dict[str, Any]:
    """The §3.2 envelope, as it goes on the wire."""
    wire = json.loads(env.model_dump_json())
    assert list(wire) == ENVELOPE_KEYS
    uuid.UUID(wire["id"])
    assert wire["type"] == "news.alert"
    assert wire["env"] == ENV
    assert wire["source"] == "system:news"
    assert wire["ts"].endswith("Z")
    assert set(wire["payload"]) == PAYLOAD_KEYS
    payload: dict[str, Any] = wire["payload"]
    assert wire["correlation_id"] == f"news:{payload['cluster_id']}"
    assert wire["id"] == str(uuid.uuid5(ALERT_NAMESPACE, payload["cluster_id"]))
    return payload


# ---- AC1: held instrument, importance 4, one alert per story ----------------------------------


@respx.mock
async def test_held_importance_4_alerts_once_and_second_source_does_not(clean: Engine) -> None:
    add_source(clean, "rss.cnbc", CNBC_URL, "CNBC Top News")
    hold(clean, "TSLA-USDT-SWAP", CLOCK_START - timedelta(minutes=1))
    respx.get(CNBC_URL).mock(return_value=rss("cnbc_tsla_earnings_rss.xml"))
    respx.get(MW_URL).mock(return_value=rss("marketwatch_tsla_earnings_rss.xml"))
    respx.route().mock(return_value=httpx.Response(404))
    pub = Recorder()
    clock = FakeClock(CLOCK_START)
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock, make_enricher(pub))
        await runner.tick()
        assert len(pub.sent) == 1
        stream, envelope, _ = pub.sent[0]
        assert stream == ALERT_STREAM == "atlas.warroom"
        payload = assert_envelope_shape(envelope)

        earnings = next(r for r in items(clean, "rss.cnbc") if "earnings" in r.title)
        assert earnings.importance == 4
        assert payload == {
            "news_item_id": str(earnings.id),
            "cluster_id": str(earnings.cluster_id),
            "headline": "Tesla reports third-quarter earnings",
            "source": "rss.cnbc",
            "source_name": "CNBC Top News",
            "category": "markets_news",
            "url": "https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html",
            "published_at": "2026-10-29T12:00:00Z",
            "importance": 4,
            "event_type": "earnings",
            "inst_ids": ["TSLA-USDT"],
            "asset_classes": ["stock"],
            "sentiment": float(earnings.sentiment),
            "novelty": float(earnings.novelty),
            "reason": "held_instrument",
            "held_inst_ids": ["TSLA-USDT-SWAP"],
        }
        assert cluster(clean, earnings.cluster_id).alerted_at is not None

        # The second source tells the same story a minute later: same cluster, no alert.
        add_source(clean, "rss.marketwatch", MW_URL, "MarketWatch Top Stories")
        clock.now += timedelta(seconds=61)
        await runner.tick()
    mw = items(clean, "rss.marketwatch")
    assert len(mw) == 1
    assert mw[0].cluster_id == earnings.cluster_id
    assert len(pub.sent) == 1
    story = cluster(clean, earnings.cluster_id)
    assert story.item_count == 2
    assert story.max_importance == 4


async def test_one_alert_per_cluster_even_when_every_item_qualifies(clean: Engine) -> None:
    """Rule thresholds at 1 so both items of one story qualify: only the first alerts, in one poll."""
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    pub = Recorder()
    enricher = make_enricher(pub, rules=AlertRules(importance=1, held_importance=1, alias_resync_seconds=60))
    batch = [
        prepared("rss.a", "https://a.test/1", "Bitcoin exchange hacked for $40 million", CLOCK_START),
        prepared(
            "rss.a",
            "https://a.test/2",
            "Bitcoin exchange hacked, $40 million stolen",
            CLOCK_START + timedelta(minutes=1),
        ),
    ]
    inserted = await enricher(batch, EnrichContext(engine=clean, now=CLOCK_START))
    assert inserted == 2
    rows = items(clean)
    assert rows[0].cluster_id == rows[1].cluster_id
    assert len(pub.sent) == 1
    assert pub.envelopes[0].payload["news_item_id"] == str(rows[0].id)


def prepared(
    source: str, url: str, title: str, published: datetime, summary: str | None = None
) -> PreparedItem:
    return PreparedItem(
        source=source,
        url=url,
        canonical_url=url,
        title=title,
        summary=summary,
        published_at=published,
        fetched_at=CLOCK_START,
        language="en",
    )


# ---- AC2: importance 3 → nothing; importance 5 on an unheld instrument → one alert -------------


@respx.mock
async def test_importance_3_publishes_nothing_and_importance_5_unheld_alerts(clean: Engine) -> None:
    add_source(clean, "rss.cnbc", CNBC_URL, "CNBC Top News")
    add_source(clean, "rss.coindesk", COINDESK_URL, "CoinDesk", category="crypto_news")
    # Held: TSLA, so the Model Y item (importance 3) is on a held instrument. BTC is not held.
    hold(clean, "TSLA-USDT-SWAP", CLOCK_START - timedelta(minutes=1))
    respx.get(CNBC_URL).mock(return_value=rss("cnbc_tsla_earnings_rss.xml"))
    respx.get(COINDESK_URL).mock(return_value=rss("coindesk_rss.xml"))
    respx.route().mock(return_value=httpx.Response(404))
    pub = Recorder()
    # Earnings (4, held) is not what this test is about: raise held_importance past it.
    rules = AlertRules(importance=5, held_importance=5, alias_resync_seconds=60)
    async with httpx.AsyncClient() as http:
        await make_runner(clean, http, FakeClock(CLOCK_START), make_enricher(pub, rules=rules)).tick()

    model_y = next(r for r in items(clean, "rss.cnbc") if "Model Y" in r.title)
    assert model_y.importance == 3
    assert model_y.instruments == ["TSLA-USDT"]

    assert len(pub.sent) == 1
    payload = assert_envelope_shape(pub.envelopes[0])
    fed = next(r for r in items(clean, "rss.coindesk") if r.title.startswith("Bitcoin dips below"))
    assert fed.importance == 5
    assert payload["news_item_id"] == str(fed.id)
    assert payload["importance"] == 5
    assert payload["inst_ids"] == ["BTC-USDT"]
    assert payload["held_inst_ids"] == []
    assert payload["reason"] == "importance"
    assert payload["event_type"] == "central_bank"
    assert all(r.importance < 5 for r in items(clean) if r.id != fed.id)


async def test_alert_carries_sentiment_alongside_the_card_fields(clean: Engine) -> None:
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    pub = Recorder()
    item = prepared("rss.a", "https://a.test/h", "Bitcoin exchange hacked for $40 million", CLOCK_START)
    await make_enricher(pub)([item], EnrichContext(engine=clean, now=CLOCK_START))
    payload = pub.envelopes[0].payload
    stored = items(clean)[0]
    assert isinstance(payload["sentiment"], float)
    assert -1.0 <= payload["sentiment"] <= 1.0
    assert payload["sentiment"] == float(stored.sentiment) < 0
    for key in ("headline", "importance", "event_type", "inst_ids", "source", "url", "published_at"):
        assert payload[key] not in (None, "")


async def test_closed_position_is_not_held(clean: Engine) -> None:
    """The newest account sync wrote no TSLA row (position closed): TSLA is no longer held."""
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    old = CLOCK_START - timedelta(minutes=10)
    hold(clean, "TSLA-USDT-SWAP", old)
    with clean.begin() as conn:
        conn.execute(
            SyncStatus.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                stream="account",
                last_success_at=CLOCK_START - timedelta(minutes=1),
                created_at=old,
                updated_at=old,
            )
        )
    pub = Recorder()
    item = prepared("rss.a", "https://a.test/t", "Tesla reports third-quarter earnings", CLOCK_START)
    await make_enricher(pub)([item], EnrichContext(engine=clean, now=CLOCK_START))
    assert items(clean)[0].importance == 4
    assert pub.sent == []


async def test_failed_publish_releases_the_story_for_its_next_item(clean: Engine) -> None:
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    rules = AlertRules(importance=1, held_importance=1, alias_resync_seconds=60)
    down = Recorder(fail=True)
    first = prepared("rss.a", "https://a.test/1", "Bitcoin exchange hacked for $40 million", CLOCK_START)
    assert await make_enricher(down, rules=rules)([first], EnrichContext(engine=clean, now=CLOCK_START)) == 1
    assert cluster(clean, items(clean)[0].cluster_id).alerted_at is None

    up = Recorder()
    later = CLOCK_START + timedelta(minutes=2)
    second = prepared("rss.a", "https://a.test/2", "Bitcoin exchange hacked, $40 million stolen", later)
    await make_enricher(up, rules=rules)([second], EnrichContext(engine=clean, now=later))
    assert len(up.sent) == 1
    assert up.envelopes[0].payload["cluster_id"] == str(items(clean)[0].cluster_id)


async def test_every_enrichment_column_is_written(clean: Engine) -> None:
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    item = prepared(
        "rss.a", "https://a.test/e", "Tesla reports third-quarter earnings", CLOCK_START, "Results beat."
    )
    await make_enricher(Recorder())([item], EnrichContext(engine=clean, now=CLOCK_START))
    row = items(clean)[0]
    assert row.instruments == ["TSLA-USDT"]
    assert row.asset_classes == ["stock"]
    assert row.entities and row.entities[0]["target"] == "TSLA-USDT"
    assert row.event_type == "earnings"
    assert row.sentiment is not None
    assert row.novelty == Decimal("1.0000")
    assert row.cluster_id is not None
    assert row.embedding is not None and len(row.embedding) == 384


# ---- AC3: fetch-to-publish latency ------------------------------------------------------------


def _rss_poll_seconds() -> int:
    """The slowest configured headline RSS feed (per-symbol templates excluded: they are one
    request per symbol and are not the news-alert path)."""
    sources: dict[str, dict[str, Any]] = DEFAULTS["news"]["sources"]
    return max(
        int(s["poll_seconds"])
        for s in sources.values()
        if s["kind"] == "rss" and "{symbol}" not in (s["url"] or "")
    )


@respx.mock
async def test_fetch_to_publish_latency_is_under_90s_including_one_poll_interval(
    clean: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    add_source(clean, "rss.coindesk", COINDESK_URL, "CoinDesk", category="crypto_news")
    respx.get(COINDESK_URL).mock(return_value=rss("coindesk_rss.xml"))
    respx.route().mock(return_value=httpx.Response(404))
    pub = Recorder()
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, FakeClock(CLOCK_START), make_enricher(pub))
        started = time.monotonic()
        await runner.tick()
    assert len(pub.sent) == 1
    processing = pub.sent[0][2] - started
    poll = _rss_poll_seconds()
    worst_case = processing + poll
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info(
            "news.alert fetch-to-publish %.3f s + one RSS poll interval %d s = %.3f s (target < 90 s)",
            processing,
            poll,
            worst_case,
        )
    print(f"ATL-65 latency: fetch-to-publish {processing:.3f} s + poll {poll} s = {worst_case:.3f} s")
    assert worst_case < 90


# ---- AC5: backfill ----------------------------------------------------------------------------


def test_parse_since() -> None:
    assert parse_since("72h") == timedelta(hours=72)
    assert parse_since("30m") == timedelta(minutes=30)
    assert parse_since("7d") == timedelta(days=7)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_since("soon")


def insert_bare(engine: Engine, url: str, title: str, published: datetime) -> uuid.UUID:
    item = prepared("rss.a", url, title, published)
    with engine.begin() as conn:
        conn.execute(NewsItem.__table__.insert().values(**item.row()))
    return item.id


def test_backfill_fills_missing_rows_and_is_idempotent(clean: Engine) -> None:
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    now = CLOCK_START
    a = insert_bare(
        clean, "https://a.test/1", "Bitcoin exchange hacked for $40 million", now - timedelta(hours=2)
    )
    b = insert_bare(
        clean, "https://a.test/2", "Bitcoin exchange hacked, $40 million stolen", now - timedelta(hours=1)
    )
    old = insert_bare(
        clean, "https://a.test/old", "Tesla reports third-quarter earnings", now - timedelta(hours=80)
    )

    done = backfill(
        clean, since=timedelta(hours=72), now=now, embedder=FakeEmbedder(), linker=LINKER, batch_size=1
    )
    assert done == 2
    rows = {r.id: r for r in items(clean)}
    for rid in (a, b):
        assert rows[rid].event_type == "hack_exploit"
        assert rows[rid].embedding is not None
        assert rows[rid].importance >= 4
        assert rows[rid].instruments == ["BTC-USDT"]
        assert rows[rid].cluster_id is not None
    assert rows[a].cluster_id == rows[b].cluster_id
    assert rows[old].event_type is None and rows[old].embedding is None
    story = cluster(clean, rows[a].cluster_id)
    assert story.item_count == 2

    snapshot = {r.id: (r.event_type, r.importance, r.cluster_id, r.novelty) for r in items(clean)}
    with clean.connect() as conn:
        clusters_before = conn.execute(select(func.count()).select_from(NewsCluster)).scalar_one()
    assert backfill(clean, since=timedelta(hours=72), now=now, embedder=FakeEmbedder(), linker=LINKER) == 0
    assert {r.id: (r.event_type, r.importance, r.cluster_id, r.novelty) for r in items(clean)} == snapshot
    with clean.connect() as conn:
        assert conn.execute(select(func.count()).select_from(NewsCluster)).scalar_one() == clusters_before
    assert cluster(clean, rows[a].cluster_id).item_count == 2


def test_backfill_cli(
    clean: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k, v in TEST_DB.items():
        monkeypatch.setenv(k, v)
    add_source(clean, "rss.a", "https://a.test/rss", "A")
    insert_bare(
        clean,
        "https://a.test/cli",
        "Bitcoin exchange hacked for $40 million",
        datetime.now(UTC) - timedelta(hours=1),
    )
    assert main(["--since", "72h", "--embedder", "fake"]) == 0
    assert "news_items enriched: 1" in capsys.readouterr().out
    assert main(["--since", "72h", "--embedder", "fake"]) == 0
    assert "news_items enriched: 0" in capsys.readouterr().out
    row = items(clean)[0]
    assert row.event_type == "hack_exploit" and row.embedding is not None


# ---- the wire contract the bridge consumes (backend/tests/Feature/Warroom/NewsAlertBridgeTest.php) --


def test_envelope_fixture_matches_what_ingest_publishes() -> None:
    """The JSON fixture Pest feeds to the bridge has exactly the shape alert_envelope() produces."""
    item = prepared(
        "rss.cnbc",
        "https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html",
        "Tesla reports third-quarter earnings",
        datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
    )
    item.id = uuid.UUID("3f9c2a1e-7b4d-4c8e-9a10-2b3c4d5e6f70")
    item.cluster_id = uuid.UUID("8a7b6c5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d")
    item.instruments, item.asset_classes = ["TSLA-USDT"], ["stock"]
    item.event_type, item.importance = "earnings", 4
    item.sentiment, item.novelty = Decimal("0.000"), Decimal("1.0000")
    payload = alert_payload(
        item,
        source_name="CNBC Top News",
        category="markets_news",
        held=["TSLA-USDT-SWAP"],
        reason="held_instrument",
    )
    produced = json.loads(alert_envelope(ENV, payload).model_dump_json())
    fixture = json.loads(ENVELOPE_FIXTURE.read_text())
    assert list(fixture) == list(produced)
    produced["ts"] = fixture["ts"]
    assert fixture == produced


def test_enricher_resyncs_aliases_on_its_own_engine(clean: Engine) -> None:
    """With no fixed linker the enricher loads one from the stored aliases (AliasWatcher + load_linker)."""
    enricher = Enricher(env=ENV, embedder=FakeEmbedder(), publish=Recorder())
    linker = enricher.linker(clean)
    assert enricher.linker(clean) is linker  # not re-synced inside alias_resync_seconds
