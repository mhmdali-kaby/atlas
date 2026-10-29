"""ATL-59: the atlas_ingest poll loop, RSS collector, dedupe and failure backoff.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. Feeds are recorded responses (tests/fixtures/rss, recorded
2026-10-29 from the live CoinDesk and Yahoo Finance TSLA feeds, trimmed) served by respx.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_ingest.canonical import canonicalize_url
from atlas_ingest.collectors import discover, factory_for
from atlas_ingest.collectors.rss import parse_feed
from atlas_ingest.extract import Extractor
from atlas_ingest.logs import configure_logging
from atlas_ingest.pipeline import Pipeline
from atlas_ingest.runner import IngestRunner, RunnerConfig, short_error
from atlas_shared import testing
from atlas_shared.db import Instrument, NewsItem, NewsSource, make_engine
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent / "fixtures" / "rss"
COINDESK_URL = "https://www.coindesk.com/arc/outboundfeeds/rss"
YAHOO_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
APP_KEY = "base64:" + base64.b64encode(b"k" * 32).decode()
TEST_INST = "TSLA-USDT-SWAP"
TEST_ENV = "demo"
ARTICLE_HTML = (
    "<html><head><title>t</title></head><body><article><h1>Headline</h1>"
    + "".join(f"<p>Tesla shipped more cars to Europe this quarter, paragraph {i}.</p>" for i in range(12))
    + "</article></body></html>"
)

TEST_DB = testing.db_settings()


def laravel_encrypt(plaintext: str, app_key: str = APP_KEY) -> str:
    """Crypt::encryptString's payload: base64(json{iv, value, mac, tag}), AES-256-CBC."""
    key = parse_app_key(app_key)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    value = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    iv_b64 = base64.b64encode(iv).decode()
    mac = hmac.new(key, (iv_b64 + value).encode(), hashlib.sha256).hexdigest()
    payload = {"iv": iv_b64, "value": value, "mac": mac, "tag": ""}
    return base64.b64encode(json.dumps(payload).encode()).decode()


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(
            f"{TEST_DB['DB_DATABASE']} is not reachable on {TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']}: {exc}"
        )
    discover()
    return eng


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(NewsItem))
            conn.execute(delete(NewsSource))
            conn.execute(
                delete(Instrument).where(Instrument.env == TEST_ENV, Instrument.inst_id == TEST_INST)
            )

    wipe()
    yield engine
    wipe()


def add_source(engine: Engine, key: str, url: str, **values: Any) -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(),
        "key": key,
        "kind": "rss",
        "category": "crypto_news",
        "name": key,
        "url": url,
        "enabled": True,
        "poll_seconds": 180,
        "requires_key": False,
        "consecutive_failures": 0,
        "created_at": now,
        "updated_at": now,
        **values,
    }
    with engine.begin() as conn:
        conn.execute(NewsSource.__table__.insert().values(**row))


def add_stock(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=TEST_ENV,
                inst_id=TEST_INST,
                inst_type="SWAP",
                lot_sz=Decimal("1"),
                min_sz=Decimal("1"),
                tick_sz=Decimal("0.01"),
                state="live",
                asset_class="stock",
            )
        )


def source(engine: Engine, key: str) -> NewsSource:
    with Session(engine) as session:
        return session.scalars(select(NewsSource).where(NewsSource.key == key)).one()


def source_row(engine: Engine, key: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(NewsSource.__table__).where(NewsSource.key == key)).one()


def item_count(engine: Engine, src: str | None = None) -> int:
    stmt = select(func.count()).select_from(NewsItem)
    if src:
        stmt = stmt.where(NewsItem.source == src)
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar_one())


def make_runner(engine: Engine, http: httpx.AsyncClient, clock: FakeClock) -> IngestRunner:
    pipeline = Pipeline(
        engine=engine,
        extractor=Extractor(http=http, timeout_seconds=2, max_bytes=2_000_000, user_agent="Browser/1.0"),
        max_extract_per_poll=25,
    )
    return IngestRunner(
        engine=engine,
        http=http,
        pipeline=pipeline,
        config=RunnerConfig(reload_seconds=60, failure_threshold=3, disable_minutes=30),
        app_key=APP_KEY,
        clock=clock,
    )


def feed(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def rss_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "application/rss+xml"})


# ---- pure functions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://WWW.CoinDesk.com/markets/a/?utm_source=x#frag", "https://www.coindesk.com/markets/a"),
        ("https://x.test/a?id=7&utm_medium=rss&fbclid=1&gclid=2", "https://x.test/a?id=7"),
        ("HTTPS://x.test:443/a/", "https://x.test/a"),
        ("http://x.test:8080/a", "http://x.test:8080/a"),
        ("https://www.fool.com/x/?.tsrc=rss", "https://www.fool.com/x"),
        ("https://x.test/", "https://x.test"),
    ],
)
def test_canonicalize_url(url: str, expected: str) -> None:
    assert canonicalize_url(url) == expected


def test_parse_recorded_feeds() -> None:
    coindesk = parse_feed(feed("coindesk_rss.xml"))
    assert len(coindesk) == 5
    assert all(i.title and i.url.startswith("https://www.coindesk.com/") for i in coindesk)
    assert all(i.published_at is not None and i.published_at.tzinfo is not None for i in coindesk)
    assert all(i.body is None for i in coindesk)  # CoinDesk's content:encoded is empty: extract
    yahoo = parse_feed(feed("yahoo_tsla_rss.xml"), symbol="TSLA")
    assert len(yahoo) == 6
    assert all(i.body is None and i.summary for i in yahoo)
    assert all(i.raw["symbol"] == "TSLA" for i in yahoo)


def test_short_error_never_carries_the_url_or_key() -> None:
    request = httpx.Request("GET", "https://x.test/feed?token=sekret-123")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError(
        "Server error for url https://x.test/feed?token=sekret-123", request=request, response=response
    )
    assert short_error(exc, "sekret-123") == "HTTP 500"
    assert short_error(httpx.ConnectTimeout("https://x.test/?token=sekret-123")) == "timeout"
    assert "sekret" not in short_error(httpx.ConnectError("https://x.test/?token=sekret-123"))


def test_rss_is_registered_by_kind() -> None:
    discover()
    assert factory_for("rss.anything", "rss") is not None
    assert factory_for("nothing", "carrier-pigeon") is None


# ---- AC1: recorded CoinDesk + Yahoo insert once; re-poll and tracking-param variants are seen ----


async def test_recorded_feeds_insert_once_and_dedupe(clean: Engine) -> None:
    add_source(clean, "rss.coindesk", COINDESK_URL, poll_seconds=180)
    add_source(clean, "rss.yahoo", YAHOO_TEMPLATE, category="markets_news", poll_seconds=300)
    add_stock(clean)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))

    coindesk = feed("coindesk_rss.xml")
    with respx.mock(assert_all_called=False) as router:
        cd_route = router.get(COINDESK_URL).mock(side_effect=lambda _r: rss_response(coindesk))
        yahoo_route = router.get(url__startswith="https://feeds.finance.yahoo.com/").mock(
            side_effect=lambda _r: rss_response(feed("yahoo_tsla_rss.xml"))
        )
        article_route = router.get(url__regex=r"^https?://").mock(  # every article page (added last)
            side_effect=lambda _r: httpx.Response(
                200, text=ARTICLE_HTML, headers={"content-type": "text/html"}
            )
        )
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            assert sorted(await runner.tick()) == ["rss.coindesk", "rss.yahoo"]
            assert item_count(clean, "rss.coindesk") == 5
            assert item_count(clean, "rss.yahoo") == 6
            assert yahoo_route.calls[0].request.url.params["s"] == "TSLA"
            extracted = article_route.call_count
            assert extracted == 11  # neither feed carries the article text
            assert article_route.calls[0].request.headers["user-agent"] == "Browser/1.0"
            assert cd_route.calls[0].request.headers["user-agent"] != "Browser/1.0"

            # Second poll: same feeds, plus a CoinDesk link with ?utm_source=x#frag
            first_link = parse_feed(coindesk)[0].url
            coindesk = coindesk.replace(
                f"<link>{first_link}</link>".encode(), f"<link>{first_link}?utm_source=x#frag</link>".encode()
            )
            clock.advance(301)
            assert sorted(await runner.tick()) == ["rss.coindesk", "rss.yahoo"]
            assert cd_route.call_count == 2
            assert item_count(clean) == 11
            assert article_route.call_count == extracted  # seen items are not re-fetched

    with Session(clean) as session:
        rows = session.scalars(select(NewsItem).where(NewsItem.source == "rss.yahoo")).all()
        cd = session.scalars(select(NewsItem).where(NewsItem.source == "rss.coindesk")).all()
    assert all(r.body and "Tesla shipped" in r.body for r in [*rows, *cd])
    assert all(r.canonical_url == canonicalize_url(r.url) and ".tsrc" not in r.canonical_url for r in rows)
    assert all(r.language == "en" for r in [*rows, *cd])
    assert all(r.raw and r.raw["symbol"] == "TSLA" for r in rows)
    assert all(r.published_at.year == 2026 for r in cd)
    status = source(clean, "rss.coindesk")
    assert status.consecutive_failures == 0 and status.last_success_at == clock.now
    assert status.last_item_at is not None and status.last_error is None


# ---- AC2: three 500s disable the source for 30 minutes; a success resets it -------------------


async def test_three_failures_disable_for_thirty_minutes_then_success_resets(clean: Engine) -> None:
    url = "https://feeds.example.test/rss"
    add_source(clean, "rss.flaky", url, poll_seconds=60)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))

    with respx.mock as router:
        route = router.get(url).mock(return_value=httpx.Response(500))
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            for expected in (1, 2, 3):
                assert await runner.tick() == ["rss.flaky"]
                row = source_row(clean, "rss.flaky")
                assert row.consecutive_failures == expected
                assert row.last_error == "HTTP 500"
                clock.advance(61)
            assert route.call_count == 3
            disabled_at = clock.now - timedelta(seconds=61)
            row = source_row(clean, "rss.flaky")
            assert row.disabled_until == disabled_at + timedelta(minutes=30)

            # Not polled again until disabled_until (ticks every 61 s up to just before it)
            while clock.now < row.disabled_until:
                assert await runner.tick() == []
                clock.advance(61)
            assert route.call_count == 3

            route.mock(return_value=rss_response(feed("coindesk_rss.xml")))
            router.get(url__startswith="https://www.coindesk.com/").respond(404)
            assert await runner.tick() == ["rss.flaky"]
            assert route.call_count == 4

    row = source_row(clean, "rss.flaky")
    assert row.consecutive_failures == 0
    assert row.disabled_until is None and row.last_error is None
    assert row.last_success_at == clock.now
    assert item_count(clean, "rss.flaky") == 5


# ---- AC3: a keyed source without a key is never requested -------------------------------------


async def test_keyed_source_without_key_is_never_requested(clean: Engine) -> None:
    url = "https://keyed.example.test/rss"
    add_source(clean, "rss.keyed", url, requires_key=True, poll_seconds=60)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__startswith="https://keyed.example.test/").respond(200)
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            for _ in range(3):
                assert await runner.tick() == []
                clock.advance(61)
        assert route.call_count == 0
    row = source_row(clean, "rss.keyed")
    assert row.last_error is None and row.consecutive_failures == 0 and row.last_success_at is None


# ---- AC4 (log half): a keyed source's key never reaches the logs ----------------------------------


async def test_api_key_never_logged(clean: Engine) -> None:
    secret = "cp-live-9f8e7d6c5b4a3210"  # noqa: S105 - test value
    url = "https://keyed.example.test/feed?auth_token={key}"
    add_source(clean, "rss.keyed", url, requires_key=True, poll_seconds=60, api_key=laravel_encrypt(secret))

    from atlas_ingest.collectors import CollectContext, SourceConfig, _by_key
    from atlas_ingest.collectors.rss import RssCollector

    class KeyedRss(RssCollector):
        async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[Any]:
            assert source.api_key == secret
            keyed = SourceConfig(**{**source.__dict__, "url": (source.url or "").replace("{key}", secret)})
            return await super().collect(keyed, ctx)

    _by_key["rss.keyed"] = KeyedRss
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    configure_logging(logging.DEBUG, stream=stream)  # the process's real logging setup
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    try:
        with respx.mock as router:
            route = router.get(url__startswith="https://keyed.example.test/feed").mock(
                side_effect=[httpx.Response(500), httpx.Response(503), httpx.ConnectError("boom " + secret)]
            )
            async with httpx.AsyncClient() as http:
                runner = make_runner(clean, http, clock)
                for _ in range(3):
                    await runner.tick()
                    clock.advance(61)
            assert route.call_count == 3
            assert route.calls[0].request.url.params["auth_token"] == secret
    finally:
        root.handlers, level = saved
        root.setLevel(level)
        _by_key.pop("rss.keyed", None)

    logs = stream.getvalue()
    assert "poll failed" in logs
    assert secret not in logs
    row = source_row(clean, "rss.keyed")
    assert secret not in (row.last_error or "") and row.consecutive_failures == 3


# ---- AC5: disabling a source in the table stops polling within 60 s, no restart -----------------


async def test_disabling_in_table_stops_polling_without_restart(clean: Engine) -> None:
    url = "https://feeds.example.test/fast"
    add_source(clean, "rss.fast", url, poll_seconds=10)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    with respx.mock as router:
        route = router.get(url).mock(side_effect=lambda _r: rss_response(feed("coindesk_rss.xml")))
        router.get(url__startswith="https://www.coindesk.com/").respond(404)
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            assert await runner.tick() == ["rss.fast"]
            with clean.begin() as conn:
                conn.execute(update(NewsSource).where(NewsSource.key == "rss.fast").values(enabled=False))
            # Poll every second of the next 60 s: at most nothing further starts once disabled.
            for _ in range(60):
                clock.advance(1)
                await runner.tick()
            assert route.call_count == 1
            assert "rss.fast" not in runner.sources

            # Re-enabling also applies without a restart, within the reload interval.
            with clean.begin() as conn:
                conn.execute(update(NewsSource).where(NewsSource.key == "rss.fast").values(enabled=True))
            for _ in range(61):
                clock.advance(1)
                await runner.tick()
            assert route.call_count >= 2
