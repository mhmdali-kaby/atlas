"""ATL-134: rubik long/short ratio and taker volume polled into their hypertables.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with respx using the recorded rubik fixtures.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import Instrument, LongShortRatio, TakerVolume, make_engine
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import OperationalError
from trading_core.app import default_scheduler
from trading_core.config import Settings
from trading_core.marketdata.derivatives import JOB, DerivativesPoller, base_ccy
from trading_core.okx import OkxClient

BASE = "https://www.okx.com"
ENV = "demo"
FIXTURES = Path(__file__).parent / "fixtures" / "okx"
LS_PATH = "/api/v5/rubik/stat/contracts/long-short-account-ratio"
TAKER_PATH = "/api/v5/rubik/stat/taker-volume"
CCYS = ("BTC", "ETH", "SOL", "XAU")


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(testing.db_settings())
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"test DB is not reachable: {exc}")
    return eng


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(LongShortRatio).where(LongShortRatio.env == ENV, LongShortRatio.ccy.in_(CCYS)))
        conn.execute(delete(TakerVolume).where(TakerVolume.env == ENV, TakerVolume.ccy.in_(CCYS)))
        conn.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == "XAU-USDT-SWAP"))


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    yield engine
    _wipe(engine)


def recorded(name: str) -> Callable[[httpx.Request], httpx.Response]:
    """A fresh Response per call: a shared one is consumed by its first reader."""
    return lambda _request: httpx.Response(200, json=fixture(name))


def factory() -> Callable[[str], OkxClient]:
    async def no_sleep(_: float) -> None:
        return None

    def make(env: str) -> OkxClient:
        return OkxClient(env, http=httpx.AsyncClient(base_url=BASE), sleep=no_sleep)

    return make


def rows(engine: Engine, model: type[Any]) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(model).where(model.env == ENV, model.ccy.in_(CCYS)).order_by(model.ccy, model.ts)
            ).all()
        )


def add_instrument(engine: Engine, inst_id: str, asset_class: str) -> None:
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            Instrument.__table__.insert().values(
                id=uuid.uuid4(),
                env=ENV,
                inst_id=inst_id,
                inst_type="SWAP",
                asset_class=asset_class,
                state="live",
                lot_sz=Decimal("1"),
                min_sz=Decimal("1"),
                tick_sz=Decimal("0.01"),
                created_at=now,
                updated_at=now,
            )
        )


def test_base_ccy() -> None:
    assert base_ccy("BTC-USDT-SWAP") == "BTC"
    assert base_ccy("eth-usdt") == "ETH"


@respx.mock(base_url=BASE, assert_all_called=True)
async def test_poll_upserts_and_a_second_poll_adds_no_rows(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    ls_route = respx_mock.get(LS_PATH).mock(side_effect=recorded("rubik_stat_contracts_long_short_account_ratio"))
    taker_route = respx_mock.get(TAKER_PATH).mock(side_effect=recorded("rubik_stat_taker_volume"))
    poller = DerivativesPoller(
        ENV, clean, client_factory=factory(), instruments=lambda: {"BTC-USDT-SWAP", "SOL-USDT-SWAP"}
    )

    first = await poller.tick()
    assert first["ccys"] == ["BTC", "ETH", "SOL"]
    assert first["failed"] == 0
    assert first["long_short_rows"] == 6
    assert first["taker_rows"] == 6

    ls = rows(clean, LongShortRatio)
    taker = rows(clean, TakerVolume)
    assert len(ls) == 6
    assert len(taker) == 6
    btc_ls = [r for r in ls if r.ccy == "BTC"]
    assert [r.ratio for r in btc_ls] == [Decimal("1.27"), Decimal("1.32")]
    assert btc_ls[1].ts == datetime.fromtimestamp(1793145600, UTC)
    assert {r.period for r in ls} == {"5m"}
    btc_taker = [r for r in taker if r.ccy == "BTC"][1]
    # rubik taker-volume rows are [ts, sellVol, buyVol].
    assert btc_taker.sell_vol == Decimal("7012345678.9")
    assert btc_taker.buy_vol == Decimal("7234567890.1")

    params = [dict(c.request.url.params) for c in taker_route.calls]
    assert all(p["instType"] == "CONTRACTS" and p["period"] == "5m" for p in params)
    assert sorted(dict(c.request.url.params)["ccy"] for c in ls_route.calls) == ["BTC", "ETH", "SOL"]

    second = await poller.tick()
    assert second["failed"] == 0
    assert len(rows(clean, LongShortRatio)) == 6
    assert len(rows(clean, TakerVolume)) == 6


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_non_crypto_instruments_are_skipped_and_one_failure_does_not_stop_others(
    respx_mock: respx.MockRouter, clean: Engine
) -> None:
    add_instrument(clean, "XAU-USDT-SWAP", "commodity")

    def ls(request: httpx.Request) -> httpx.Response:
        if request.url.params["ccy"] == "ETH":
            return httpx.Response(200, json={"code": "50011", "msg": "Too Many Requests", "data": []})
        return httpx.Response(200, json=fixture("rubik_stat_contracts_long_short_account_ratio"))

    respx_mock.get(LS_PATH).mock(side_effect=ls)
    respx_mock.get(TAKER_PATH).mock(side_effect=recorded("rubik_stat_taker_volume"))
    poller = DerivativesPoller(
        ENV, clean, client_factory=factory(), instruments=lambda: {"XAU-USDT-SWAP", "BTC-USDT-SWAP"}
    )

    result = await poller.tick()
    assert result["ccys"] == ["BTC", "ETH"]
    assert result["failed"] == 1
    assert {r.ccy for r in rows(clean, LongShortRatio)} == {"BTC"}


def test_job_is_registered_every_five_minutes(engine: Engine) -> None:
    settings = Settings.from_env()
    scheduler = default_scheduler(settings, engine)
    job = scheduler._jobs[JOB]
    assert job.interval_seconds == 300
