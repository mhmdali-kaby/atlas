"""ATL-45: public OKX market-data methods parse into Decimal/UTC models, page, rate-limit and back off."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_shared.defaults import DEFAULTS
from trading_core.okx import OkxClient, OkxError, RateLimit, RateLimiter
from trading_core.okx.models import Candle

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"

MARKET_ENDPOINTS = [
    "market/candles",
    "market/history-candles",
    "market/tickers",
    "public/funding-rate",
    "public/funding-rate-history",
    "public/open-interest",
    "public/mark-price",
    "rubik/stat/contracts/open-interest-volume",
    "rubik/stat/contracts/long-short-account-ratio",
    "rubik/stat/taker-volume",
]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def ok(data: list[Any]) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "msg": "", "data": data})


class FakeClock:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def public_client(
    env: str = "demo", clock: FakeClock | None = None, **kw: Any
) -> tuple[OkxClient, FakeClock]:
    clock = clock or FakeClock()
    client = OkxClient(
        env,
        None,
        http=httpx.AsyncClient(base_url=BASE),
        sleep=clock.sleep,
        monotonic=clock,
        rand=lambda: 1.0,
        **kw,
    )
    return client, clock


def utc_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


# ---- parsing (criterion 1) ---------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=True)
async def test_every_market_method_parses_its_fixture(respx_mock: respx.MockRouter) -> None:
    for path in MARKET_ENDPOINTS:
        name = path.replace("/", "_").replace("-", "_")
        if path == "market/tickers":
            name = "market_tickers_swap"
        respx_mock.get(f"/api/v5/{path}").mock(return_value=httpx.Response(200, json=fixture(name)))

    client, _ = public_client()
    async with client:
        candles = await client.get_candles("BTC-USDT-SWAP", bar="1H", limit=4)
        assert len(candles) == 4
        forming, closed = candles[0], candles[1]
        assert forming.confirm is False and closed.confirm is True
        assert closed.ts == datetime(2026, 10, 28, 16, 0, tzinfo=UTC)
        assert closed.o == Decimal("114020.0") and closed.h == Decimal("114260.9")
        assert closed.l == Decimal("113950.1") and closed.c == Decimal("114210.5")
        assert closed.vol == Decimal("1873.12") and closed.vol_ccy_quote == Decimal("2137404.1131")

        hist = await client.get_history_candles("BTC-USDT-SWAP", bar="1H")
        assert hist and all(c.confirm and c.ts.tzinfo is UTC for c in hist)

        tickers = await client.get_tickers("SWAP")
        assert tickers and all(isinstance(t.last, Decimal) for t in tickers)

        fr = await client.get_funding_rate("BTC-USDT-SWAP")
        assert fr.funding_rate == Decimal("0.0000792") and fr.next_funding_rate is None
        assert fr.next_funding_time == datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
        assert fr.sett_funding_rate == Decimal("0.0000658")

        frh = await client.get_funding_rate_history("BTC-USDT-SWAP", limit=2)
        assert frh[0].realized_rate == Decimal("0.0000658")
        assert frh[0].funding_time == datetime(2026, 10, 28, 16, 0, tzinfo=UTC)

        oi = await client.get_open_interest("SWAP", inst_id="BTC-USDT-SWAP")
        assert oi[0].oi_usd == Decimal("3289012345.67") and oi[0].ts.tzinfo is UTC

        mark = await client.get_mark_price("SWAP", inst_id="BTC-USDT-SWAP")
        assert mark[0].mark_px == Decimal("114348.7")

        oiv = await client.get_open_interest_volume("BTC", period="1D")
        assert oiv[0].ts == datetime(2026, 10, 28, tzinfo=UTC)
        assert oiv[0].oi == Decimal("3301234567.8") and oiv[0].vol == Decimal("41234567890.12")

        ls = await client.get_long_short_ratio("BTC", period="1D")
        assert ls[0].ratio == Decimal("1.32") and ls[1].ts == datetime(2026, 10, 27, tzinfo=UTC)

        tv = await client.get_taker_volume("BTC", period="1D")
        assert tv[0].sell_vol == Decimal("7012345678.9") and tv[0].buy_vol == Decimal("7234567890.1")

    # public endpoints are never signed, and the query carries the documented parameter names
    for call in respx_mock.calls:
        assert "OK-ACCESS-SIGN" not in call.request.headers
    taker = next(c.request for c in respx_mock.calls if c.request.url.path.endswith("taker-volume"))
    assert dict(taker.url.params) == {"ccy": "BTC", "instType": "CONTRACTS", "period": "1D"}


def test_array_row_with_missing_columns_is_refused() -> None:
    with pytest.raises(ValueError, match="columns"):
        Candle.model_validate(["1793206800000", "1", "2"])


def test_candle_ignores_extra_trailing_columns() -> None:
    row = ["1793206800000", "1", "2", "0.5", "1.5", "10", "0.1", "15", "1", "future-column"]
    assert Candle.model_validate(row).c == Decimal("1.5")


def test_float_in_array_row_is_refused() -> None:
    with pytest.raises(ValueError):
        Candle.model_validate(["1793206800000", 1.5, "2", "0.5", "1.5", "10", "0.1", "15", "1"])


# ---- paging with the after cursor (criterion 1) -----------------------------------------------


def _page(start_ms: int, n: int) -> list[list[str]]:
    hour = 3_600_000
    return [[str(start_ms - i * hour), "1", "2", "0.5", "1.5", "10", "0.1", "15", "1"] for i in range(n)]


@pytest.mark.parametrize(("history", "path"), [(False, "market/candles"), (True, "market/history-candles")])
async def test_iter_candles_walks_back_with_the_after_cursor(history: bool, path: str) -> None:
    hour = 3_600_000
    newest = 1_793_206_800_000
    pages = {
        None: _page(newest, 3),
        str(newest - 2 * hour): _page(newest - 3 * hour, 3),
        str(newest - 5 * hour): _page(newest - 6 * hour, 2),
        str(newest - 7 * hour): [],
    }
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        seen.append(after)
        assert request.url.params["bar"] == "1H" and request.url.params["limit"] == "3"
        return ok(pages[after])

    with respx.mock(base_url=BASE) as router:
        router.get(f"/api/v5/{path}").mock(side_effect=handler)
        client, _ = public_client()
        async with client:
            got = [
                page
                async for page in client.iter_candles("BTC-USDT-SWAP", bar="1H", limit=3, history=history)
            ]
    assert seen == [None, str(newest - 2 * hour), str(newest - 5 * hour), str(newest - 7 * hour)]
    flat = [c.ts for page in got for c in page]
    assert len(flat) == 8 and flat == sorted(flat, reverse=True) and len(set(flat)) == 8


async def test_iter_candles_stops_at_since_and_drops_older_bars() -> None:
    hour = 3_600_000
    newest = 1_793_206_800_000
    since = utc_ms(newest - 4 * hour)
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        calls.append(after)
        start = newest if after is None else int(after) - hour
        return ok(_page(start, 3))

    with respx.mock(base_url=BASE) as router:
        router.get("/api/v5/market/candles").mock(side_effect=handler)
        client, _ = public_client()
        async with client:
            got = [
                c
                async for page in client.iter_candles(
                    "BTC-USDT-SWAP", bar="1H", after=newest + hour, since=since, limit=3
                )
                for c in page
            ]
    assert [c.ts for c in got] == [utc_ms(newest - i * hour) for i in range(5)]
    assert calls == [str(newest + hour), str(newest - 2 * hour)]


async def test_cursor_accepts_datetime_and_refuses_naive() -> None:
    with respx.mock(base_url=BASE) as router:
        route = router.get("/api/v5/market/candles").mock(return_value=ok([]))
        client, _ = public_client()
        async with client:
            await client.get_candles("BTC-USDT-SWAP", after=utc_ms(1_793_206_800_000))
            with pytest.raises(ValueError, match="timezone-aware"):
                await client.get_candles("BTC-USDT-SWAP", after=datetime(2026, 10, 28))  # noqa: DTZ001
    assert route.calls.last.request.url.params["after"] == "1793206800000"


@pytest.mark.parametrize(
    ("method", "cap"),
    [("get_candles", 300), ("get_history_candles", 100), ("get_funding_rate_history", 100)],
)
async def test_limit_above_the_documented_page_size_is_refused(method: str, cap: int) -> None:
    client, _ = public_client()
    async with client:
        with pytest.raises(ValueError, match=f"1..{cap}"):
            await getattr(client, method)("BTC-USDT-SWAP", limit=cap + 1)


# ---- rate limiting and backoff (criterion 2) -------------------------------------------------


def test_every_market_endpoint_has_a_limit_from_defaults() -> None:
    limiter = RateLimiter()
    configured = DEFAULTS["okx_rest"]["rate_limits"]
    for path in MARKET_ENDPOINTS:
        lim = limiter.bucket(path).limit
        assert lim == RateLimit(int(configured[path]["requests"]), float(configured[path]["per_seconds"]))
    assert limiter.bucket("market/candles").limit == RateLimit(40, 2)
    assert limiter.bucket("rubik/stat/taker-volume").limit == RateLimit(5, 2)


@pytest.mark.parametrize("path", ["market/candles", "public/funding-rate", "rubik/stat/taker-volume"])
async def test_three_times_the_limit_is_spaced_to_the_configured_rate(path: str) -> None:
    configured = DEFAULTS["okx_rest"]["rate_limits"][path]
    requests, per = int(configured["requests"]), float(configured["per_seconds"])
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, sleep=clock.sleep)
    sent_at: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_at.append(clock.now)
        if path == "public/funding-rate":
            return httpx.Response(200, json=fixture("public_funding_rate"))
        return ok([])

    with respx.mock(base_url=BASE) as router:
        router.get(f"/api/v5/{path}").mock(side_effect=handler)
        client, _ = public_client(clock=clock, rate_limiter=limiter)
        async with client:
            for _ in range(3 * requests):
                if path == "market/candles":
                    await client.get_candles("BTC-USDT-SWAP")
                elif path == "public/funding-rate":
                    await client.get_funding_rate("BTC-USDT-SWAP")
                else:
                    await client.get_taker_volume("BTC")

    assert len(sent_at) == 3 * requests
    # the first burst uses the full bucket, then one request per (per / requests) seconds
    assert sent_at[requests - 1] == 0.0
    interval = per / requests
    for i in range(requests, 3 * requests):
        assert sent_at[i] == pytest.approx((i - requests + 1) * interval)
    assert sent_at[-1] == pytest.approx(2 * per)
    # after the burst, any `requests` consecutive calls span a full `per` window
    for i in range(2 * requests, len(sent_at)):
        assert sent_at[i] - sent_at[i - requests] == pytest.approx(per)


@pytest.mark.parametrize(
    "first",
    [
        httpx.Response(429, json={"code": "50011", "msg": "Too Many Requests", "data": []}),
        httpx.Response(429, text="Too Many Requests"),
        httpx.Response(200, json={"code": "50011", "msg": "Too Many Requests", "data": []}),
    ],
)
async def test_rate_limit_responses_are_retried_after_a_full_window(first: httpx.Response) -> None:
    with respx.mock(base_url=BASE) as router:
        route = router.get("/api/v5/rubik/stat/taker-volume").mock(
            side_effect=[first, httpx.Response(200, json=fixture("rubik_stat_taker_volume"))]
        )
        client, clock = public_client()
        async with client:
            rows = await client.get_taker_volume("BTC")
    assert route.call_count == 2 and rows
    # jitter alone would be 0.5 s; a rate-limit refusal waits at least the endpoint's 2 s window
    assert clock.sleeps == [2.0]


async def test_rate_limit_backoff_grows_and_gives_up_after_max_attempts() -> None:
    body = {"code": "50011", "msg": "Too Many Requests", "data": []}
    with respx.mock(base_url=BASE) as router:
        route = router.get("/api/v5/market/history-candles").mock(return_value=httpx.Response(429, json=body))
        client, clock = public_client()
        async with client:
            with pytest.raises(OkxError) as err:
                await client.get_history_candles("BTC-USDT-SWAP")
    assert err.value.code == "50011" and err.value.http_status == 429
    assert route.call_count == int(DEFAULTS["okx_rest"]["max_attempts"])
    assert clock.sleeps == [2.0, 2.0]


async def test_non_rate_limit_retry_keeps_plain_jitter() -> None:
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v5/public/mark-price").mock(
            side_effect=[
                httpx.Response(503, text="busy"),
                httpx.Response(200, json=fixture("public_mark_price")),
            ]
        )
        client, clock = public_client()
        async with client:
            await client.get_mark_price("SWAP")
    assert clock.sleeps == [0.5]


# ---- environment header (criterion 3) ---------------------------------------------------------


@pytest.mark.parametrize(("env", "expected"), [("demo", "1"), ("live", None)])
async def test_simulated_trading_header_only_in_demo(env: str, expected: str | None) -> None:
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v5/market/candles").mock(return_value=ok([]))
        router.get("/api/v5/public/open-interest").mock(return_value=ok([]))
        router.get("/api/v5/rubik/stat/contracts/long-short-account-ratio").mock(return_value=ok([]))
        client, _ = public_client(env)
        async with client:
            await client.get_candles("BTC-USDT-SWAP")
            await client.get_open_interest("SWAP")
            await client.get_long_short_ratio("BTC")
        assert len(router.calls) == 3
        for call in router.calls:
            assert call.request.headers.get("x-simulated-trading") == expected
