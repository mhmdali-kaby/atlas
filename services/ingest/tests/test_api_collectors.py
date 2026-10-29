"""ATL-60: CryptoPanic, Finnhub news and OKX announcements collectors.

Responses in tests/fixtures/api are written from each provider's documented response shape (the
build had no network access to record live ones); respx serves them. DB tests run against this
checkout's test DB (atlas_shared.testing), migrated by `cd backend && php artisan test`.
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
from atlas_ingest import sources as db
from atlas_ingest.collectors import CollectError, discover, factory_for
from atlas_ingest.collectors.cryptopanic import parse_posts
from atlas_ingest.collectors.finnhub import FinnhubNewsCollector, company_news_url, parse_news
from atlas_ingest.collectors.okx_announcements import parse_announcements
from atlas_ingest.extract import Extractor
from atlas_ingest.logs import configure_logging
from atlas_ingest.pipeline import Pipeline
from atlas_ingest.runner import IngestRunner, RunnerConfig
from atlas_shared import testing
from atlas_shared.db import Instrument, NewsItem, NewsSource, make_engine
from atlas_shared.defaults import DEFAULTS
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import OperationalError

FIXTURES = Path(__file__).parent / "fixtures" / "api"
APP_KEY = "base64:" + base64.b64encode(b"q" * 32).decode()
CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
FINNHUB_URL = "https://finnhub.io/api/v1/news"
FINNHUB_COMPANY_URL = "https://finnhub.io/api/v1/company-news"
OKX_URL = "https://www.okx.com/api/v5/support/announcements"
ARTICLE_HTML = (
    "<html><body><article><h1>Headline</h1>"
    + "".join(f"<p>Markets moved on the news today, paragraph {i}.</p>" for i in range(12))
    + "</article></body></html>"
)
# (env, inst_id, asset_class, state): only the demo, live stock/ETF rows may be requested.
TEST_INSTRUMENTS = (
    ("demo", "TSLA-USDT-SWAP", "stock", "live"),
    ("demo", "AAPL-USDC-SWAP", "stock", "live"),
    ("demo", "SPY-USDT-SWAP", "etf", "live"),
    ("demo", "QQQ-USDT-SWAP", "etf", "suspend"),
    ("demo", "XAU-USDT-SWAP", "commodity", "live"),
    ("demo", "ATLTESTCOIN-USDT-SWAP", "crypto", "live"),
    ("live", "NVDA-USDT-SWAP", "stock", "live"),
    ("live", "SPY-USDT-SWAP", "etf", "live"),
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
    return base64.b64encode(
        json.dumps({"iv": iv_b64, "value": value, "mac": mac, "tag": ""}).encode()
    ).decode()


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DEFAULTS["news"]["collectors"]["finnhub"], "request_interval_seconds", "0")
    # The test DB may hold other demo stock rows: never truncate the batch in these tests.
    monkeypatch.setitem(DEFAULTS["news"]["collectors"]["finnhub"], "max_calls_per_poll", 500)


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
            for env, inst_id, _cls, _state in TEST_INSTRUMENTS:
                conn.execute(delete(Instrument).where(Instrument.env == env, Instrument.inst_id == inst_id))

    wipe()
    yield engine
    wipe()


def add_source(engine: Engine, key: str, url: str, *, api_key: str | None = None, **values: Any) -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(),
        "key": key,
        "provider": key.split(".")[0],
        "kind": "api",
        "category": "crypto_news",
        "name": key,
        "url": url,
        "enabled": True,
        "poll_seconds": 180,
        "requires_key": api_key is not None,
        "api_key": laravel_encrypt(api_key) if api_key else None,
        "consecutive_failures": 0,
        "created_at": now,
        "updated_at": now,
        **values,
    }
    with engine.begin() as conn:
        conn.execute(NewsSource.__table__.insert().values(**row))


def add_instruments(engine: Engine) -> None:
    with engine.begin() as conn:
        for env, inst_id, asset_class, state in TEST_INSTRUMENTS:
            conn.execute(
                Instrument.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=env,
                    inst_id=inst_id,
                    inst_type="SWAP",
                    lot_sz=Decimal("1"),
                    min_sz=Decimal("1"),
                    tick_sz=Decimal("0.01"),
                    state=state,
                    asset_class=asset_class,
                )
            )


def source_row(engine: Engine, key: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(NewsSource.__table__).where(NewsSource.key == key)).one()


def items(engine: Engine, src: str) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(NewsItem.__table__).where(NewsItem.source == src)).all())


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
        env="demo",
    )


def article_route(router: respx.MockRouter) -> None:
    """Every article page the pipeline extracts (register after the API routes)."""
    router.get(url__regex=r"^https?://").mock(
        side_effect=lambda _r: httpx.Response(200, text=ARTICLE_HTML, headers={"content-type": "text/html"})
    )


def finnhub_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/company-news"):
        symbol = request.url.params["symbol"]
        path = FIXTURES / f"finnhub_company_{symbol}.json"
        return httpx.Response(200, json=json.loads(path.read_text()) if path.exists() else [])
    return httpx.Response(200, json=fixture("finnhub_general.json"))


# ---- parsing the recorded shapes ----------------------------------------------------------------


def test_collectors_are_registered_by_key() -> None:
    discover()
    for key in ("cryptopanic", "finnhub.news", "okx.announcements"):
        assert factory_for(key, "api") is not None


def test_parse_cryptopanic_posts() -> None:
    parsed = parse_posts(fixture("cryptopanic_posts.json"))
    assert parsed[0].title.startswith("Bitcoin ETFs")
    assert len(parsed) == 3
    assert all(i.published_at is not None and i.published_at.utcoffset() == timedelta(0) for i in parsed)
    assert parsed[0].published_at == datetime(2026, 10, 29, 6, 41, 12, tzinfo=UTC)
    assert parsed[0].url.startswith("https://cryptopanic.com/news/21004311/")  # no original_url on free
    assert parsed[1].url == "https://www.theblock.co/post/380001/solana-validators-fee-market-upgrade"
    assert parsed[0].raw["currencies"] == ["BTC"] and parsed[0].raw["publisher"] == "CoinDesk"
    with pytest.raises(CollectError):
        parse_posts({"status": "Incomplete", "info": "Token not found"})


def test_parse_finnhub_news() -> None:
    general = parse_news(fixture("finnhub_general.json"))
    assert len(general) == 2
    assert general[0].published_at == datetime.fromtimestamp(1793254500, UTC)
    assert general[1].summary is None  # empty summary -> the pipeline extracts the article
    company = parse_news(fixture("finnhub_company_TSLA.json"), symbol="TSLA")
    assert company[0].raw["symbol"] == "TSLA" and company[0].raw["related"] == "TSLA"
    assert company_news_url(FINNHUB_URL) == FINNHUB_COMPANY_URL
    with pytest.raises(CollectError):
        parse_news({"error": "Invalid API key"})


def test_parse_okx_announcements_and_error_yields_nothing() -> None:
    parsed = parse_announcements(fixture("okx_announcements.json"))
    assert len(parsed) == 3
    assert parsed[0].published_at == datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
    assert parsed[0].raw["ann_type"] == "announcements-new-listings"
    with pytest.raises(CollectError, match="OKX error 50026"):
        parse_announcements({"code": "50026", "msg": "System error", "data": []})


def test_finnhub_batch_rotates_through_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DEFAULTS["news"]["collectors"]["finnhub"], "max_calls_per_poll", 3)
    collector = FinnhubNewsCollector()
    symbols = ["AAPL", "MSFT", "SPY", "TSLA", "QQQ"]
    assert collector.next_batch(symbols) == ["AAPL", "MSFT"]
    collector._cursor = 2
    assert collector.next_batch(symbols) == ["SPY", "TSLA"]
    collector._cursor = 4
    assert collector.next_batch(symbols) == ["QQQ", "AAPL"]
    assert collector.next_batch([]) == []


# ---- AC1: recorded responses go through the ATL-59 pipeline into news_items ---------------------


async def test_each_collector_inserts_through_the_pipeline(clean: Engine) -> None:
    add_source(clean, "cryptopanic", CRYPTOPANIC_URL, api_key="cp-key-0001")
    add_source(clean, "finnhub.news", FINNHUB_URL, api_key="fh-key-0002", category="markets_news")
    add_source(clean, "okx.announcements", OKX_URL, category="exchange", poll_seconds=120)
    add_instruments(clean)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))

    with respx.mock(assert_all_called=False) as router:
        cp = router.get(CRYPTOPANIC_URL).mock(
            return_value=httpx.Response(200, json=fixture("cryptopanic_posts.json"))
        )
        fh = router.get(url__startswith="https://finnhub.io/api/v1/").mock(side_effect=finnhub_response)
        okx = router.get(OKX_URL).mock(
            return_value=httpx.Response(200, json=fixture("okx_announcements.json"))
        )
        article_route(router)
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            ran = await runner.tick()

    assert sorted(ran) == ["cryptopanic", "finnhub.news", "okx.announcements"]
    assert cp.call_count == 1 and okx.call_count == 1
    assert cp.calls[0].request.url.params["public"] == "true"
    assert cp.calls[0].request.url.params["auth_token"] == "cp-key-0001"  # noqa: S105 - a fake key
    # Finnhub's key travels in the header, never in a URL.
    assert all(c.request.headers["X-Finnhub-Token"] == "fh-key-0002" for c in fh.calls)
    assert all("fh-key-0002" not in str(c.request.url) for c in fh.calls)
    assert okx.calls[0].request.headers["Accept-Language"] == "en-US"

    expected = {
        "cryptopanic": 3,
        "finnhub.news": 4,
        "okx.announcements": 3,
    }  # finnhub: 2 general + TSLA + AAPL
    for key, count in expected.items():
        rows = items(clean, key)
        assert len(rows) == count, key
        for row in rows:
            assert row.title and row.url.startswith("https://")
            assert row.source == key
            assert row.published_at.utcoffset() == timedelta(0)
            assert row.published_at < clock.now
        assert source_row(clean, key).last_success_at is not None
    okx_titles = {r.title for r in items(clean, "okx.announcements")}
    assert "OKX to list perpetual futures for PLUME" in okx_titles


async def test_okx_error_records_a_failure_and_inserts_nothing(clean: Engine) -> None:
    add_source(clean, "okx.announcements", OKX_URL, category="exchange", poll_seconds=120)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    with respx.mock as router:  # any other request (a scrape) would fail the mock
        route = router.get(OKX_URL).mock(
            return_value=httpx.Response(
                200, json={"code": "50001", "msg": "Service temporarily unavailable", "data": []}
            )
        )
        async with httpx.AsyncClient() as http:
            await make_runner(clean, http, clock).tick()
    assert route.call_count == 1
    assert items(clean, "okx.announcements") == []
    row = source_row(clean, "okx.announcements")
    assert row.consecutive_failures == 1 and row.last_error == "OKX error 50001"


# ---- AC2: a Finnhub 429 is one failure and the next poll waits the provider's window -----------


@pytest.mark.parametrize(("retry_after", "window"), [(None, 60), ("120", 120)])
async def test_finnhub_429_waits_the_provider_window(
    clean: Engine, retry_after: str | None, window: int
) -> None:
    # poll_seconds shorter than Finnhub's one-minute window, so only the 429 handling can delay it.
    add_source(
        clean, "finnhub.news", FINNHUB_URL, api_key="fh-key-0003", category="markets_news", poll_seconds=10
    )
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    headers = {"Retry-After": retry_after} if retry_after else {}
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__startswith="https://finnhub.io/api/v1/").mock(
            side_effect=[httpx.Response(429, headers=headers, json={"error": "API limit reached."})]
            + [httpx.Response(200, json=[])] * 5
        )
        async with httpx.AsyncClient() as http:
            runner = make_runner(clean, http, clock)
            assert await runner.tick() == ["finnhub.news"]
            row = source_row(clean, "finnhub.news")
            assert row.consecutive_failures == 1
            assert row.last_error == "rate limited (HTTP 429)"
            assert row.disabled_until is None

            for elapsed in (10, 30, window - 1):
                clock.now = datetime(2026, 10, 29, 8, 0, tzinfo=UTC) + timedelta(seconds=elapsed)
                assert await runner.tick() == [], f"polled again after {elapsed}s"
            assert route.call_count == 1

            clock.now = datetime(2026, 10, 29, 8, 0, tzinfo=UTC) + timedelta(seconds=window)
            assert await runner.tick() == ["finnhub.news"]
    assert route.call_count == 2
    assert source_row(clean, "finnhub.news").consecutive_failures == 0


# ---- AC3: a CryptoPanic error never leaks the key into logs or last_error ------------------------


async def test_cryptopanic_error_never_leaks_the_key(clean: Engine) -> None:
    secret = "cp-SECRET-7f3a9d"  # noqa: S105 - a fake key
    add_source(clean, "cryptopanic", CRYPTOPANIC_URL, api_key=secret, poll_seconds=60)
    stream = io.StringIO()
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    configure_logging(logging.DEBUG, stream=stream)  # the process's real logging setup
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))

    def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    try:
        with respx.mock as router:
            route = router.get(url__startswith=CRYPTOPANIC_URL).mock(
                side_effect=[
                    httpx.Response(401, json={"status": "Incomplete", "info": f"Token {secret} not found"}),
                    connect_error,
                    httpx.Response(200, json={"status": "api_error", "info": f"bad token {secret}"}),
                ]
            )
            async with httpx.AsyncClient() as http:
                runner = make_runner(clean, http, clock)
                errors = []
                for _ in range(3):
                    await runner.tick()
                    errors.append(source_row(clean, "cryptopanic").last_error)
                    clock.advance(61)
        assert route.call_count == 3
        assert all(c.request.url.params["auth_token"] == secret for c in route.calls)  # it was sent
    finally:
        root.handlers = saved[0]
        root.setLevel(saved[1])

    assert errors == ["HTTP 401", "network error (ConnectError)", "unexpected response shape"]
    output = stream.getvalue()
    assert "poll failed" in output
    assert secret not in output
    assert all(secret not in (e or "") for e in errors)
    row = source_row(clean, "cryptopanic")
    assert row.consecutive_failures == 3 and row.disabled_until is not None


# ---- AC4: company news only for the active env's stock/ETF instruments ---------------------------


def test_stock_symbols_are_limited_to_the_env(clean: Engine) -> None:
    add_instruments(clean)
    demo = db.stock_symbols(clean, "demo")
    assert {"AAPL", "SPY", "TSLA"} <= set(demo)
    assert not {"QQQ", "XAU", "ATLTESTCOIN", "NVDA"} & set(demo)
    assert "NVDA" in db.stock_symbols(clean, "live")


async def test_finnhub_requests_only_active_env_stock_and_etf_symbols(clean: Engine) -> None:
    add_source(clean, "finnhub.news", FINNHUB_URL, api_key="fh-key-0004", category="markets_news")
    add_instruments(clean)
    clock = FakeClock(datetime(2026, 10, 29, 8, 0, tzinfo=UTC))
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__startswith="https://finnhub.io/api/v1/").mock(side_effect=finnhub_response)
        article_route(router)
        async with httpx.AsyncClient() as http:
            await make_runner(clean, http, clock).tick()

    company = [c.request.url for c in route.calls if c.request.url.path.endswith("/company-news")]
    requested = {u.params["symbol"] for u in company}
    expected = set(db.stock_symbols(clean, "demo"))
    assert requested == expected  # every demo live stock/ETF underlying, nothing else
    assert {"AAPL", "SPY", "TSLA"} <= requested
    assert not {"QQQ", "XAU", "ATLTESTCOIN", "NVDA"} & requested
    assert all(u.params["from"] == "2026-10-27" and u.params["to"] == "2026-10-29" for u in company)
    general = [c for c in route.calls if c.request.url.path == "/api/v1/news"]
    assert len(general) == 1 and general[0].request.url.params["category"] == "general"
