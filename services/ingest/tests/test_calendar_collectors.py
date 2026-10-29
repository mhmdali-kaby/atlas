"""ATL-61: ForexFactory / Finnhub economic calendar, Finnhub earnings and Fear & Greed collectors.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. Provider responses (tests/fixtures/calendar) are written from the
providers' documented shapes (no network at build time) and served by respx.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_ingest import calendar as cal
from atlas_ingest.collectors import discover, factory_for
from atlas_ingest.collectors.forexfactory import parse_week
from atlas_ingest.extract import Extractor
from atlas_ingest.pipeline import Pipeline
from atlas_ingest.runner import IngestRunner, RunnerConfig
from atlas_shared import testing
from atlas_shared.db import EarningsEvent, EconomicEvent, Instrument, MarketSentiment, NewsSource, make_engine
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.exc import OperationalError

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FINNHUB_CAL_URL = "https://finnhub.io/api/v1/calendar/economic"
FINNHUB_EARN_URL = "https://finnhub.io/api/v1/calendar/earnings"
FNG_URL = "https://api.alternative.me/fng/"
APP_KEY = "base64:" + base64.b64encode(b"k" * 32).decode()
FINNHUB_KEY = "fh-test-key-1234"
ENV = "demo"
STOCKS = {"AAPL-USDT-SWAP": "stock", "TSLA-USDT-SWAP": "stock", "QQQ-USDT-SWAP": "etf"}
NOT_STOCKS = {"BTC-USDT-SWAP": "crypto"}
TEST_DB = testing.db_settings()


def laravel_encrypt(plaintext: str, app_key: str = APP_KEY) -> str:
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

    def set(self, when: datetime) -> None:
        self.now = when


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
    ids = [*STOCKS, *NOT_STOCKS, "MSFT-USDT-SWAP"]

    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(EconomicEvent))
            conn.execute(delete(EarningsEvent))
            conn.execute(delete(MarketSentiment))
            conn.execute(delete(NewsSource))
            conn.execute(delete(Instrument).where(Instrument.inst_id.in_(ids)))

    wipe()
    yield engine
    wipe()


def add_source(engine: Engine, key: str, kind: str, url: str, **values: Any) -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(),
        "key": key,
        "provider": key.split(".")[0],
        "kind": kind,
        "category": {"calendar": "calendar", "earnings": "earnings", "sentiment": "sentiment"}[kind],
        "name": key,
        "url": url,
        "enabled": True,
        "poll_seconds": {"calendar": 900, "earnings": 86400, "sentiment": 3600}[kind],
        "requires_key": key.startswith("finnhub."),
        "consecutive_failures": 0,
        "created_at": now,
        "updated_at": now,
        **values,
    }
    with engine.begin() as conn:
        conn.execute(NewsSource.__table__.insert().values(**row))


def add_instruments(engine: Engine, classes: dict[str, str]) -> None:
    with engine.begin() as conn:
        for inst_id, asset_class in classes.items():
            conn.execute(
                Instrument.__table__.insert().values(
                    id=uuid.uuid4(),
                    env=ENV,
                    inst_id=inst_id,
                    inst_type="SWAP",
                    lot_sz=Decimal("1"),
                    min_sz=Decimal("1"),
                    tick_sz=Decimal("0.01"),
                    state="live",
                    asset_class=asset_class,
                )
            )


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


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def json_response(body: Any) -> httpx.Response:
    return httpx.Response(
        200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


def rows(engine: Engine, model: Any) -> list[Any]:
    with engine.connect() as conn:
        return list(conn.execute(select(model.__table__)).all())


def count(engine: Engine, model: Any) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(model)).scalar_one())


def source_row(engine: Engine, key: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(NewsSource.__table__).where(NewsSource.key == key)).one()


# ---- pure functions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3.2%", Decimal("3.2")),
        ("245K", Decimal("245000")),
        ("-1.2M", Decimal("-1200000")),
        ("1,234.5", Decimal("1234.5")),
        ("<0.1%", Decimal("0.1")),
        ("", None),
        (None, None),
        ("n/a", None),
        ("1.2|1.3", None),
    ],
)
def test_parse_number(text: str | None, expected: Decimal | None) -> None:
    assert cal.parse_number(text) == expected


def test_surprise_needs_both_numbers() -> None:
    assert cal.surprise("245K", "231K") == Decimal("14000")
    assert cal.surprise("0.3%", "0.2%") == Decimal("0.1")
    assert cal.surprise(None, "0.2%") is None
    assert cal.surprise("0.3%", "") is None


def test_collectors_are_registered_by_key() -> None:
    discover()
    for key, kind in [
        ("forexfactory.calendar", "calendar"),
        ("finnhub.calendar", "calendar"),
        ("finnhub.earnings", "earnings"),
        ("alternative_me.fng", "sentiment"),
    ]:
        assert factory_for(key, kind) is not None, key


def test_forexfactory_week_parses_to_utc_and_maps_impact() -> None:
    parsed = {r.name: r for r in parse_week(fixture("ff_calendar_thisweek.json"))}
    fed = parsed["Federal Funds Rate"]
    assert fed.scheduled_at == datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
    assert fed.importance == 5
    assert parsed["Crude Oil Inventories"].importance == 3
    assert parsed["Chicago PMI"].importance == 1
    assert parsed["Bank Holiday"].importance == 0  # not stored
    assert fed.forecast == "3.75%" and fed.previous == "4.00%" and fed.actual is None


# ---- ForexFactory ------------------------------------------------------------------------------


async def test_forexfactory_inserts_then_updates_actual_in_place(clean: Engine) -> None:
    add_source(clean, "forexfactory.calendar", "calendar", FF_URL)
    clock = FakeClock(datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
    week = fixture("ff_calendar_thisweek.json")
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock(assert_all_called=True) as router:
            route = router.get(FF_URL).mock(return_value=json_response(week))
            await runner.tick()
            assert route.call_count == 1

        stored = {r.name: r for r in rows(clean, EconomicEvent)}
        assert len(stored) == 9  # 10 entries, the Holiday is not stored
        assert "Bank Holiday" not in stored
        claims = stored["Unemployment Claims"]
        assert claims.scheduled_at == datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
        assert claims.importance == 5 and claims.source == "forexfactory.calendar"
        assert claims.actual is None and claims.surprise is None
        assert stored["German ifo Business Climate"].importance == 3
        assert stored["Chicago PMI"].importance == 1
        assert source_row(clean, "forexfactory.calendar").last_item_at == clock.now

        # The release comes out: the feed now carries an actual for the same event.
        updated = [dict(e) for e in week]
        for e in updated:
            if e["title"] == "Unemployment Claims":
                e["actual"] = "245K"
        clock.advance(900)
        with respx.mock(assert_all_called=True) as router:
            router.get(FF_URL).mock(return_value=json_response(updated))
            await runner.tick()

    after = {r.name: r for r in rows(clean, EconomicEvent)}
    assert len(after) == 9
    assert after["Unemployment Claims"].id == claims.id
    assert after["Unemployment Claims"].actual == "245K"
    assert after["Unemployment Claims"].surprise == Decimal("14000")
    assert after["Unemployment Claims"].forecast == "231K"


async def test_forexfactory_batch_with_duplicate_event_does_not_error(clean: Engine) -> None:
    week = fixture("ff_calendar_thisweek.json")
    dup = dict(week[2], forecast="94.0")  # same title/country/date as CB Consumer Confidence
    written = cal.upsert_economic(
        clean, "forexfactory.calendar", parse_week([*week, dup]), datetime(2026, 10, 27, tzinfo=UTC)
    )
    assert written == 9
    stored = {r.name: r for r in rows(clean, EconomicEvent)}
    assert stored["CB Consumer Confidence"].forecast == "94.0"  # last occurrence wins


async def test_unchanged_repoll_writes_nothing(clean: Engine) -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    parsed = parse_week(fixture("ff_calendar_thisweek.json"))
    assert cal.upsert_economic(clean, "forexfactory.calendar", parsed, now) == 9
    assert cal.upsert_economic(clean, "forexfactory.calendar", parsed, now + timedelta(minutes=15)) == 0


# ---- Finnhub calendar replaces ForexFactory ----------------------------------------------------


async def test_keyed_finnhub_calendar_means_no_forexfactory_request(clean: Engine) -> None:
    add_source(clean, "forexfactory.calendar", "calendar", FF_URL)
    add_source(clean, "finnhub.calendar", "calendar", FINNHUB_CAL_URL, api_key=laravel_encrypt(FINNHUB_KEY))
    clock = FakeClock(datetime(2026, 10, 29, 6, 0, tzinfo=UTC))
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock(assert_all_called=False) as router:
            ff = router.get(FF_URL).mock(return_value=json_response(fixture("ff_calendar_thisweek.json")))
            fh = router.get(FINNHUB_CAL_URL).mock(
                return_value=json_response(fixture("finnhub_economic.json"))
            )
            ran = await runner.tick()
            assert sorted(ran) == ["finnhub.calendar", "forexfactory.calendar"]
            assert ff.call_count == 0
            assert fh.call_count == 1
            request = fh.calls.last.request
            assert request.headers["X-Finnhub-Token"] == FINNHUB_KEY
            assert FINNHUB_KEY not in str(request.url)
            assert request.url.params["from"] == "2026-10-28" and request.url.params["to"] == "2026-11-05"

    stored = {r.name: r for r in rows(clean, EconomicEvent)}
    assert {r.source for r in stored.values()} == {"finnhub.calendar"}
    cpi = stored["CPI MoM"]
    assert cpi.importance == 5 and cpi.forecast == "0.3%" and cpi.previous == "0.4%" and cpi.actual is None
    assert cpi.scheduled_at == datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
    claims = stored["Initial Jobless Claims"]
    assert claims.importance == 3 and claims.actual == "231K" and claims.surprise == Decimal("1")
    assert stored["ECB Speech"].importance == 1
    # ForexFactory's poll was a success that wrote nothing.
    ff_row = source_row(clean, "forexfactory.calendar")
    assert ff_row.consecutive_failures == 0 and ff_row.last_item_at is None


async def test_forexfactory_runs_when_finnhub_has_no_key_or_is_degraded(clean: Engine) -> None:
    add_source(clean, "forexfactory.calendar", "calendar", FF_URL)
    add_source(clean, "finnhub.calendar", "calendar", FINNHUB_CAL_URL)  # no key: needs_key
    now = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)
    assert cal.finnhub_calendar_active(clean, now) is False
    with clean.begin() as conn:
        conn.execute(
            NewsSource.__table__.update()
            .where(NewsSource.key == "finnhub.calendar")
            .values(api_key=laravel_encrypt(FINNHUB_KEY), disabled_until=now + timedelta(minutes=5))
        )
    assert cal.finnhub_calendar_active(clean, now) is False  # degraded: ForexFactory covers
    assert cal.finnhub_calendar_active(clean, now + timedelta(minutes=5)) is True


# ---- poll cadence ------------------------------------------------------------------------------


async def test_calendar_polls_every_minute_near_events_and_15_min_otherwise(clean: Engine) -> None:
    add_source(clean, "forexfactory.calendar", "calendar", FF_URL)
    week = fixture("ff_calendar_thisweek.json")
    # Federal Funds Rate is at 18:00 UTC on 28 Oct; the event before it is at 14:30 UTC.
    clock = FakeClock(datetime(2026, 10, 28, 15, 0, tzinfo=UTC))
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock(assert_all_called=True) as router:
            route = router.get(FF_URL).mock(return_value=json_response(week))

            async def polls_at(seconds_later: float) -> int:
                before = route.call_count
                clock.advance(seconds_later)
                await runner.tick()
                return route.call_count - before

            assert await polls_at(0) == 1  # 15:00: nothing within ±10 min → next in 900 s
            assert await polls_at(60) == 0
            assert await polls_at(839) == 0  # 15:14:59
            assert await polls_at(1) == 1  # 15:15

            clock.set(datetime(2026, 10, 28, 17, 30, tzinfo=UTC))
            assert await polls_at(0) == 1  # 17:30: the 18:00 window opens at 17:50 → next 17:45
            assert await polls_at(899) == 0
            assert await polls_at(1) == 1  # 17:45 → window opens in 300 s
            assert await polls_at(299) == 0
            assert await polls_at(1) == 1  # 17:50: inside the window → every 60 s
            for _ in range(20):  # 17:51 … 18:10
                assert await polls_at(59) == 0
                assert await polls_at(1) == 1
            # 18:10 is still inside the window, so 18:11 is polled; then the next event is
            # 30 Oct 03:00 UTC → back to 900 s.
            assert await polls_at(60) == 1
            assert await polls_at(60) == 0
            assert await polls_at(839) == 0
            assert await polls_at(1) == 1


def test_calendar_poll_seconds_boundaries(clean: Engine) -> None:
    event = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
    cal.upsert_economic(
        clean,
        "forexfactory.calendar",
        [cal.EconomicRow("USD", "FOMC", 5, event, None, None, None, None)],
        event - timedelta(days=1),
    )
    assert cal.calendar_poll_seconds(clean, event - timedelta(hours=2), 900) == 900
    assert cal.calendar_poll_seconds(clean, event - timedelta(minutes=12), 900) == 120
    assert cal.calendar_poll_seconds(clean, event - timedelta(minutes=10, seconds=30), 900) == 60  # floor
    assert cal.calendar_poll_seconds(clean, event - timedelta(minutes=10), 900) == 60
    assert cal.calendar_poll_seconds(clean, event + timedelta(minutes=10), 900) == 60
    assert cal.calendar_poll_seconds(clean, event + timedelta(minutes=10, seconds=1), 900) == 900


# ---- Finnhub earnings --------------------------------------------------------------------------


async def test_finnhub_earnings_only_for_stock_and_etf_perps_and_dedupes(clean: Engine) -> None:
    add_instruments(clean, {**STOCKS, **NOT_STOCKS})
    add_source(clean, "finnhub.earnings", "earnings", FINNHUB_EARN_URL, api_key=laravel_encrypt(FINNHUB_KEY))
    clock = FakeClock(datetime(2026, 10, 29, 6, 0, tzinfo=UTC))
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock(assert_all_called=True) as router:
            route = router.get(FINNHUB_EARN_URL).mock(
                return_value=json_response(fixture("finnhub_earnings.json"))
            )
            await runner.tick()
            request = route.calls.last.request
            assert request.headers["X-Finnhub-Token"] == FINNHUB_KEY
            assert request.url.params["from"] == "2026-10-26" and request.url.params["to"] == "2026-11-12"

    row = source_row(clean, "finnhub.earnings")
    assert row.consecutive_failures == 0 and row.last_error is None
    stored = {(r.symbol, r.date): r for r in rows(clean, EarningsEvent)}
    # MSFT and ACME are not perps here; AAPL appears twice for one date and is collapsed.
    assert set(stored) == {("AAPL", date(2026, 10, 30)), ("TSLA", date(2026, 10, 22))}
    aapl = stored[("AAPL", date(2026, 10, 30))]
    assert aapl.time == "amc" and aapl.eps_est == Decimal("1.77") and aapl.rev_est == Decimal("101900000000")
    tsla = stored[("TSLA", date(2026, 10, 22))]
    assert tsla.eps_act == Decimal("0.5") and tsla.rev_act == Decimal("28100000000")


def test_earnings_upsert_with_duplicate_keys_in_one_batch(clean: Engine) -> None:
    now = datetime(2026, 10, 29, tzinfo=UTC)
    batch = [
        cal.EarningsRow("AAPL", date(2026, 10, 30), "amc", Decimal("1.7"), None, None, None),
        cal.EarningsRow("AAPL", date(2026, 10, 30), "amc", Decimal("1.8"), None, None, None),
    ]
    assert cal.upsert_earnings(clean, batch, now) == 1
    assert cal.upsert_earnings(clean, batch, now) == 0
    assert count(clean, EarningsEvent) == 1


# ---- Fear & Greed ------------------------------------------------------------------------------


async def test_fear_and_greed_one_row_per_value_and_no_duplicates_on_repoll(clean: Engine) -> None:
    add_source(clean, "alternative_me.fng", "sentiment", FNG_URL)
    clock = FakeClock(datetime(2026, 10, 29, 6, 0, tzinfo=UTC))
    body = fixture("alternative_me_fng.json")
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock(assert_all_called=True) as router:
            route = router.get(FNG_URL).mock(return_value=json_response(body))
            await runner.tick()
            assert route.calls.last.request.url.params["limit"] == "10"
            assert count(clean, MarketSentiment) == 3
            clock.advance(3600)
            await runner.tick()
            assert route.call_count == 2

    stored = {r.ts: r for r in rows(clean, MarketSentiment)}
    assert len(stored) == 3
    latest = stored[datetime(2025, 10, 29, 0, 0, tzinfo=UTC)]
    assert (
        latest.index_key == "crypto_fng" and latest.value == Decimal("34") and latest.classification == "Fear"
    )
    # The second poll changed nothing, so last_item_at stays at the first poll.
    assert source_row(clean, "alternative_me.fng").last_item_at == clock.now - timedelta(seconds=3600)


async def test_fear_and_greed_provider_error_is_a_failure(clean: Engine) -> None:
    add_source(clean, "alternative_me.fng", "sentiment", FNG_URL)
    clock = FakeClock(datetime(2026, 10, 29, 6, 0, tzinfo=UTC))
    async with httpx.AsyncClient() as http:
        runner = make_runner(clean, http, clock)
        with respx.mock() as router:
            router.get(FNG_URL).mock(return_value=json_response({"data": [], "metadata": {"error": "bad"}}))
            await runner.tick()
    row = source_row(clean, "alternative_me.fng")
    assert row.consecutive_failures == 1 and row.last_error == "provider error"
    assert count(clean, MarketSentiment) == 0
