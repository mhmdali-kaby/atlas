"""ATL-7: the read-only OKX REST client signs, retries, rate-limits and parses into Decimal models."""

from __future__ import annotations

import base64
import hmac
import json
import types
import typing
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from trading_core.okx import (
    OkxClient,
    OkxCredentials,
    OkxError,
    OkxHttpError,
    OkxNetworkError,
    RateLimit,
    RateLimiter,
    RetryPolicy,
    TokenBucket,
    client_for_connection,
)
from trading_core.okx.models import ALL_MODELS
from trading_core.okx.signing import iso_timestamp, sign

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"
CREDS = OkxCredentials(api_key="test-key-0000", secret="atlas-test-only-okx-secret", passphrase="test-pass")  # noqa: S106
NOW_MS = 1_793_207_736_680  # fixed wall clock for deterministic signatures


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(
    env: str = "demo", creds: OkxCredentials | None = CREDS, **kw: Any
) -> tuple[OkxClient, Sleeps]:
    sleeps = Sleeps()
    client = OkxClient(
        env,
        creds,
        http=httpx.AsyncClient(base_url=BASE),
        sleep=sleeps,
        wall_clock_ms=lambda: NOW_MS,
        rand=lambda: 1.0,
        **kw,
    )
    return client, sleeps


def mock_time(router: respx.MockRouter) -> respx.Route:
    return router.get("/api/v5/public/time").mock(
        return_value=httpx.Response(200, json=fixture("public_time"))
    )


# ---- signing ---------------------------------------------------------------------------------


def test_signature_matches_okx_docs_worked_example() -> None:
    # Inputs from the OKX v5 docs "Signature" section (timestamp 2020-12-08T09:08:57.715Z,
    # GET /api/v5/account/balance?ccy=BTC, and the POST /api/v5/trade/order example body). The docs do
    # not publish a secret, so the expected Base64 is pinned for a test-only secret and cross-checked
    # against the official python-okx SDK's okx.utils.sign.
    ts = "2020-12-08T09:08:57.715Z"
    secret = "atlas-test-only-okx-secret"  # noqa: S105
    assert (
        sign(secret, ts, "GET", "/api/v5/account/balance?ccy=BTC")
        == "MgJCWvXFkwbomqFIGgrrTerZBYSP2ydnCdc1R5DUto4="
    )
    body = '{"instId":"BTC-USDT","lever":"5","mgnMode":"isolated"}'
    assert (
        sign(secret, ts, "post", "/api/v5/trade/order", body)
        == "YuaTV3PWpoyYl06jE4O+AWbM1iPV4YzeVGcSE7bLuXE="
    )


def test_signature_agrees_with_official_sdk() -> None:
    okx_utils = pytest.importorskip("okx.utils")
    ts = "2020-12-08T09:08:57.715Z"
    message = ts + "GET" + "/api/v5/account/bills?limit=5"
    expected = okx_utils.sign(message, CREDS.secret).decode()
    assert sign(CREDS.secret, ts, "GET", "/api/v5/account/bills?limit=5") == expected


def test_iso_timestamp_has_milliseconds_and_z() -> None:
    assert iso_timestamp(1_607_418_537_715) == "2020-12-08T09:08:57.715Z"
    assert iso_timestamp(1_607_418_537_005) == "2020-12-08T09:08:57.005Z"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_signed_request_carries_valid_okx_headers(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    route = respx_mock.get("/api/v5/account/balance").mock(
        return_value=httpx.Response(200, json=fixture("account_balance"))
    )
    client, _ = make_client()
    async with client:
        await client.get_balance(["BTC", "USDT"])
    req = route.calls.last.request
    ts = req.headers["OK-ACCESS-TIMESTAMP"]
    assert req.headers["OK-ACCESS-KEY"] == CREDS.api_key
    assert req.headers["OK-ACCESS-PASSPHRASE"] == CREDS.passphrase
    path = req.url.raw_path.decode()
    assert path == "/api/v5/account/balance?ccy=BTC%2CUSDT"
    expected = base64.b64encode(
        hmac.new(CREDS.secret.encode(), (ts + "GET" + path).encode(), sha256).digest()
    )
    assert req.headers["OK-ACCESS-SIGN"] == expected.decode()


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_clock_offset_from_public_time_is_applied_and_refreshed(respx_mock: respx.MockRouter) -> None:
    server_ms = int(fixture("public_time")["data"][0]["ts"])
    time_route = mock_time(respx_mock)
    respx_mock.get("/api/v5/account/config").mock(
        return_value=httpx.Response(200, json=fixture("account_config"))
    )
    mono = [0.0]
    local_ms = server_ms - 2_500  # local clock 2.5 s behind OKX
    client = OkxClient(
        "demo",
        CREDS,
        http=httpx.AsyncClient(base_url=BASE),
        sleep=Sleeps(),
        wall_clock_ms=lambda: local_ms,
        monotonic=lambda: mono[0],
    )
    async with client:
        await client.get_account_config()
        assert client.clock_offset_ms == 2_500
        sent = respx_mock.calls.last.request.headers["OK-ACCESS-TIMESTAMP"]
        assert sent == iso_timestamp(server_ms)
        await client.get_account_config()
        assert time_route.call_count == 1  # cached inside the 10 min window
        mono[0] = 600.0
        await client.get_account_config()
        assert time_route.call_count == 2  # refreshed after 10 min


# ---- environment header -------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_demo_sends_simulated_trading_header(respx_mock: respx.MockRouter) -> None:
    route = mock_time(respx_mock)
    client, _ = make_client("demo")
    async with client:
        await client.get_server_time()
    assert route.calls.last.request.headers["x-simulated-trading"] == "1"


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_live_does_not_send_simulated_trading_header(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    bal = respx_mock.get("/api/v5/account/balance").mock(
        return_value=httpx.Response(200, json=fixture("account_balance"))
    )
    client, _ = make_client("live")
    async with client:
        await client.get_balance()
    for call in respx_mock.calls:
        assert "x-simulated-trading" not in call.request.headers
    assert bal.called


def test_unknown_env_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown OKX environment"):
        OkxClient("paper")


async def test_private_call_without_credentials_is_refused() -> None:
    client, _ = make_client(creds=None)
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_time(router)
        async with client:
            with pytest.raises(ValueError, match="needs credentials"):
                await client.get_positions()


# ---- retries and errors ----------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_429_then_200_succeeds_after_one_retry(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/api/v5/public/time").mock(
        side_effect=[
            httpx.Response(429, json={"code": "50011", "msg": "Too Many Requests", "data": []}),
            httpx.Response(200, json=fixture("public_time")),
        ]
    )
    client, sleeps = make_client()
    async with client:
        result = await client.get_server_time()
    assert route.call_count == 2
    assert len(sleeps.calls) == 1
    assert result.epoch_ms == int(fixture("public_time")["data"][0]["ts"])


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_three_5xx_raise_after_exactly_three_attempts(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/api/v5/market/tickers").mock(return_value=httpx.Response(503, text="busy"))
    client, sleeps = make_client()
    async with client:
        with pytest.raises(OkxHttpError) as err:
            await client.get_tickers("SWAP")
    assert err.value.status == 503
    assert route.call_count == 3
    # full-jitter backoff with rand()=1.0: base 0.5 s, doubling, two sleeps between three attempts
    assert sleeps.calls == [0.5, 1.0]


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_network_errors_retry_then_raise(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/api/v5/public/time").mock(side_effect=httpx.ConnectError("refused"))
    client, _ = make_client()
    async with client:
        with pytest.raises(OkxNetworkError):
            await client.get_server_time()
    assert route.call_count == 3


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_network_error_then_success(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("/api/v5/public/time").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=fixture("public_time"))]
    )
    client, _ = make_client()
    async with client:
        assert (await client.get_server_time()).ts.tzinfo is UTC


@pytest.mark.parametrize("status", [200, 401])
async def test_okx_code_50113_raises_okx_error_without_retry(status: int) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        mock_time(router)
        route = router.get("/api/v5/account/config").mock(
            return_value=httpx.Response(status, json=fixture("error_invalid_sign"))
        )
        client, _ = make_client()
        async with client:
            with pytest.raises(OkxError) as err:
                await client.get_account_config()
    assert err.value.code == "50113"
    assert err.value.msg == "Invalid Sign"
    assert route.call_count == 1
    assert CREDS.secret not in str(err.value) and CREDS.api_key not in str(err.value)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_retryable_okx_code_in_200_body_is_retried(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("/api/v5/public/instruments").mock(
        side_effect=[
            httpx.Response(200, json={"code": "50011", "msg": "Too Many Requests", "data": []}),
            httpx.Response(200, json=fixture("public_instruments_swap")),
        ]
    )
    client, _ = make_client()
    async with client:
        assert len(await client.get_instruments("SWAP")) == 6
    assert route.call_count == 2


def test_retry_policy_comes_from_defaults() -> None:
    policy = RetryPolicy.from_defaults()
    assert policy.max_attempts == 3
    assert "50011" in policy.retryable_codes
    assert policy.delay(10, lambda: 1.0) == policy.backoff_max_seconds


# ---- rate limiting --------------------------------------------------------------------------


async def test_token_bucket_waits_when_empty() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    bucket = TokenBucket(RateLimit(requests=2, per_seconds=2), clock=lambda: now[0], sleep=fake_sleep)
    await bucket.acquire()
    await bucket.acquire()
    assert sleeps == []
    await bucket.acquire()  # third request inside 2 s must wait 1 s for a token
    assert sleeps == [pytest.approx(1.0)]


def test_every_client_endpoint_has_a_documented_rate_limit() -> None:
    limiter = RateLimiter()
    for path in [
        "public/time",
        "public/instruments",
        "market/tickers",
        "account/config",
        "account/balance",
        "account/positions",
        "account/positions-history",
        "account/bills",
        "account/bills-archive",
        "account/trade-fee",
        "asset/balances",
        "asset/deposit-history",
        "asset/withdrawal-history",
    ]:
        assert limiter.bucket(path).limit.requests > 0
    assert limiter.bucket("account/balance").limit == RateLimit(10, 2)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_client_requests_are_throttled_per_endpoint(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    respx_mock.get("/api/v5/account/config").mock(
        return_value=httpx.Response(200, json=fixture("account_config"))
    )
    now = [0.0]
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)
        now[0] += s

    limiter = RateLimiter(
        {"public/time": RateLimit(10, 2), "account/config": RateLimit(1, 2)},
        clock=lambda: now[0],
        sleep=fake_sleep,
    )
    client = OkxClient(
        "demo",
        CREDS,
        http=httpx.AsyncClient(base_url=BASE),
        rate_limiter=limiter,
        sleep=fake_sleep,
        wall_clock_ms=lambda: NOW_MS,
        monotonic=lambda: now[0],
    )
    async with client:
        await client.get_account_config()
        await client.get_account_config()
    assert waits == [pytest.approx(2.0)]


# ---- parsing ---------------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_balance_fixture_parses_to_decimals(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    respx_mock.get("/api/v5/account/balance").mock(
        return_value=httpx.Response(200, json=fixture("account_balance"))
    )
    client, _ = make_client()
    async with client:
        bal = await client.get_balance()
    assert isinstance(bal.total_eq, Decimal)
    assert bal.total_eq == Decimal(fixture("account_balance")["data"][0]["totalEq"])
    assert bal.adj_eq is None  # OKX "" -> None
    assert bal.u_time is not None and bal.u_time.tzinfo is UTC
    btc = next(d for d in bal.details if d.ccy == "BTC")
    assert isinstance(btc.eq_usd, Decimal) and isinstance(btc.avail_bal, Decimal)
    for d in bal.details:
        for name in ("eq", "cash_bal", "avail_bal", "eq_usd"):
            value = getattr(d, name)
            assert value is None or isinstance(value, Decimal)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_positions_fixture_parses_to_decimals(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    respx_mock.get("/api/v5/account/positions").mock(
        return_value=httpx.Response(200, json=fixture("account_positions"))
    )
    client, _ = make_client()
    async with client:
        positions = await client.get_positions(inst_type="SWAP")
    btc, xau = positions
    assert btc.inst_id == "BTC-USDT-SWAP"
    assert btc.pos == Decimal("1") and btc.avg_px == Decimal("80250.5") and btc.upl == Decimal("1.482")
    assert btc.liq_px == Decimal("64512.3") and btc.lever == Decimal("5")
    assert xau.pos == Decimal("-0.1") and xau.liq_px is None
    assert btc.c_time == datetime(2026, 10, 28, 7, 40, tzinfo=UTC)
    for p in positions:
        for name in ("pos", "avg_px", "mark_px", "upl", "margin", "notional_usd", "fee", "funding_fee"):
            assert isinstance(getattr(p, name), Decimal)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_every_read_method_parses_its_fixture(respx_mock: respx.MockRouter) -> None:
    mock_time(respx_mock)
    for path, name in [
        ("public/instruments", "public_instruments_swap"),
        ("market/tickers", "market_tickers_swap"),
        ("account/config", "account_config"),
        ("account/positions-history", "account_positions_history"),
        ("account/bills", "account_bills"),
        ("account/bills-archive", "account_bills_archive"),
        ("account/trade-fee", "account_trade_fee_swap"),
        ("asset/balances", "asset_balances"),
        ("asset/deposit-history", "asset_deposit_history"),
        ("asset/withdrawal-history", "asset_withdrawal_history"),
    ]:
        respx_mock.get(f"/api/v5/{path}").mock(return_value=httpx.Response(200, json=fixture(name)))
    client, _ = make_client()
    async with client:
        instruments = await client.get_instruments("SWAP")
        btc = next(i for i in instruments if i.inst_id == "BTC-USDT-SWAP")
        assert (
            btc.ct_val == Decimal("0.01")
            and btc.lot_sz == Decimal("0.01")
            and btc.max_lever == Decimal("100")
        )
        assert btc.inst_category == "1" and btc.exp_time is None
        assert {i.settle_ccy for i in instruments} >= {"USDT", "USDC"}
        tickers = await client.get_tickers("SWAP")
        assert all(isinstance(t.last, Decimal) for t in tickers)
        config = await client.get_account_config()
        assert config.acct_lv == "2" and config.pos_mode == "net_mode" and "trade" in config.permissions
        assert (await client.get_positions_history())[0].pnl == Decimal("0.97240")
        bills = await client.get_bills(limit=5)
        assert bills and all(isinstance(b.bal_chg, Decimal) for b in bills)
        assert await client.get_bills_archive()
        fee = await client.get_trade_fee("SWAP")
        assert fee.taker == Decimal("-0.0005") and fee.maker_usdc == Decimal("-0.0002")
        funding = await client.get_asset_balances()
        assert funding[1].frozen_bal == Decimal("0.0003")
        deposit = (await client.get_deposit_history())[0]
        assert deposit.amt == Decimal("1000") and deposit.from_ is None
        wd = (await client.get_withdrawal_history())[0]
        assert wd.amt == Decimal("250") and wd.fee == Decimal("1.5")


def _contains_float(tp: Any) -> bool:
    if tp is float:
        return True
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _contains_float(typing.get_args(tp)[0])
    if origin in (typing.Union, types.UnionType) or origin is not None:
        return any(_contains_float(a) for a in typing.get_args(tp))
    return False


def test_no_model_field_is_a_float() -> None:
    for model in ALL_MODELS:
        for name, field in model.model_fields.items():
            assert not _contains_float(field.annotation), f"{model.__name__}.{name} is a float"


def test_float_input_is_refused_for_decimal_fields() -> None:
    from trading_core.okx.models import AssetBalance

    with pytest.raises(ValueError, match="float"):
        AssetBalance.model_validate({"ccy": "USDT", "bal": 1.1})


# ---- connection row --------------------------------------------------------------------------


def test_client_for_connection_decrypts_laravel_ciphertexts() -> None:
    crypt = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "shared"
            / "tests"
            / "fixtures"
            / "laravel_crypt_fixture.json"
        ).read_text(encoding="utf-8")
    )
    row = types.SimpleNamespace(
        env="demo", api_key=crypt["payload"], api_secret=crypt["payload"], passphrase=crypt["payload"]
    )
    client = client_for_connection(row, crypt["app_key"])
    assert client.env == "demo"
    assert client._creds is not None and client._creds.secret == crypt["plaintext"]
    assert crypt["plaintext"] not in repr(client._creds)


# ---- no write endpoints ----------------------------------------------------------------------


def test_client_has_no_write_methods() -> None:
    names = {n for n in dir(OkxClient) if not n.startswith("_")}
    for forbidden in ("order", "cancel", "amend", "leverage", "withdraw", "transfer", "close_position"):
        assert not any(forbidden in n and not n.endswith("_history") for n in names), forbidden
