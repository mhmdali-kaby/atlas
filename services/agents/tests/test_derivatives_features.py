"""ATL-134: derivatives features (brief §7.5) — funding, OI-price quadrant, L/S, taker flow, basis.

Pure-function tests build frames by hand. The loader test needs this checkout's test DB, migrated
by `cd backend && php artisan test`.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from atlas_agents.derivatives.features import (
    DerivativesFrames,
    basis_features,
    classify_quadrant,
    compute_features,
    count_sign_flips,
    funding_features,
    index_max_age_seconds,
    load_frames,
    long_short_features,
    oi_features,
    taker_features,
)
from atlas_shared.db import Candle, FundingRate, LongShortRatio, OpenInterest, TakerVolume
from atlas_shared.defaults import DEFAULTS
from atlas_shared.market_data import MarketSnapshot
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

AS_OF = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
H = timedelta(hours=1)
BANDS = {"price_dead_band_pct": 0.25, "oi_dead_band_pct": 0.5}


def series(points: dict[datetime, float]) -> pd.Series[float]:
    return pd.Series(list(points.values()), index=pd.DatetimeIndex(list(points.keys())), dtype=float)


def price_frame(points: dict[datetime, float], vol: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {"c": list(points.values()), "vol": [vol] * len(points)}, index=pd.DatetimeIndex(list(points.keys()))
    )


def empty_series() -> pd.Series[float]:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))


def empty_taker() -> pd.DataFrame:
    return pd.DataFrame({"buy_vol": [], "sell_vol": []}, index=pd.DatetimeIndex([], tz="UTC"))


def window(feats: object, hours: int) -> object:
    return next(w for w in feats.windows if w.hours == hours)  # type: ignore[attr-defined]


# -- quadrant (AC2) ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price", "oi", "expected"),
    [
        (3.0, -6.0, "short_covering"),
        (-2.0, 5.0, "new_shorts"),
        (2.0, 4.0, "new_longs"),
        (-2.0, -4.0, "long_liquidation"),
        (0.2, 6.0, "flat"),  # price inside its dead-band
        (3.0, -0.4, "flat"),  # OI inside its dead-band
        (-0.1, 0.1, "flat"),
    ],
)
def test_classify_quadrant(price: float, oi: float, expected: str) -> None:
    assert classify_quadrant(price, oi, **BANDS) == expected


def oi_and_price(price_move: float, oi_move: float) -> tuple[pd.Series[float], pd.DataFrame]:
    """5-minute OI and closes over 25h; both move linearly by the given total % over the last 24h."""
    oi_pts: dict[datetime, float] = {}
    px_pts: dict[datetime, float] = {}
    steps = 24 * 12
    for i in range(steps + 13):
        ts = AS_OF - timedelta(minutes=5 * i)
        frac = max(0.0, (steps - i) / steps)
        oi_pts[ts] = 1_000_000.0 * (1 + oi_move / 100 * frac)
        px_pts[ts] = 100.0 * (1 + price_move / 100 * frac)
    return series(oi_pts).sort_index(), price_frame(px_pts).sort_index()


def test_oi_features_short_covering_over_24h() -> None:
    oi, price = oi_and_price(3.0, -6.0)
    feats = oi_features(oi, price, AS_OF)
    w24 = window(feats, 24)
    assert w24.price_change_pct == pytest.approx(3.0, abs=1e-9)  # type: ignore[attr-defined]
    assert w24.oi_change_pct == pytest.approx(-6.0, abs=1e-9)  # type: ignore[attr-defined]
    assert w24.quadrant == "short_covering"  # type: ignore[attr-defined]
    assert w24.from_ts == AS_OF - 24 * H  # type: ignore[attr-defined]
    # 1h: price +0.125 % sits inside the 0.25 % dead-band.
    assert window(feats, 1).quadrant == "flat"  # type: ignore[attr-defined]
    assert feats.unreliable is False


def test_oi_features_new_shorts_over_24h() -> None:
    oi, price = oi_and_price(-2.0, 5.0)
    feats = oi_features(oi, price, AS_OF)
    assert window(feats, 24).quadrant == "new_shorts"  # type: ignore[attr-defined]


def test_oi_features_moves_inside_dead_band_are_flat() -> None:
    oi, price = oi_and_price(0.1, 0.3)
    feats = oi_features(oi, price, AS_OF)
    assert {w.quadrant for w in feats.windows} == {"flat"}


def test_daily_oi_series_never_passes_for_an_hourly_change() -> None:
    newest = AS_OF - timedelta(minutes=10)
    oi = series({newest - 24 * H: 100.0, newest: 110.0})
    price = price_frame({newest - 24 * H: 100.0, newest: 103.0})
    feats = oi_features(oi, price, AS_OF)
    assert window(feats, 1).quadrant is None  # type: ignore[attr-defined]
    assert "no open interest row" in window(feats, 1).missing  # type: ignore[attr-defined]
    assert window(feats, 24).quadrant == "new_longs"  # type: ignore[attr-defined]


def test_stale_open_interest_gives_no_quadrant() -> None:
    oi, price = oi_and_price(3.0, -6.0)
    feats = oi_features(oi, price, AS_OF + 2 * H)
    assert all(w.quadrant is None for w in feats.windows)
    assert feats.missing["windows"].startswith("stale")


# -- unreliable demo readings (AC4) ------------------------------------------------------------------


def test_zero_open_interest_is_unreliable_with_no_quadrant() -> None:
    oi, price = oi_and_price(3.0, -6.0)
    oi.iloc[-1] = 0.0
    feats = oi_features(oi, price, AS_OF)
    assert feats.unreliable is True
    assert "zero_open_interest" in feats.unreliable_reasons
    assert all(w.quadrant is None and w.oi_change_pct is None for w in feats.windows)

    frames = DerivativesFrames(
        funding=empty_series(),
        oi=oi,
        oi_series="BTC-USDT-SWAP",
        price=price,
        long_short=empty_series(),
        taker=empty_taker(),
    )
    full = compute_features("BTC-USDT-SWAP", frames, AS_OF)
    assert full.unreliable is True
    assert "zero_open_interest" in full.unreliable_reasons
    assert full.unreliable_reasons["zero_open_interest"]


def test_zero_candle_volume_is_unreliable_with_no_quadrant() -> None:
    oi, price = oi_and_price(3.0, -6.0)
    price["vol"] = 0.0
    feats = oi_features(oi, price, AS_OF)
    assert feats.unreliable is True
    assert "zero_volume" in feats.unreliable_reasons
    assert all(w.quadrant is None for w in feats.windows)


def test_zero_taker_volume_is_unreliable() -> None:
    idx = pd.DatetimeIndex([AS_OF - timedelta(minutes=5 * i) for i in range(48)]).sort_values()
    taker = pd.DataFrame({"buy_vol": [10.0] * 48, "sell_vol": [0.0] * 48}, index=idx)
    feats = taker_features(taker, AS_OF)
    assert feats.unreliable is True
    assert all(w.buy_sell_ratio is None for w in feats.windows)


# -- funding (AC3) -----------------------------------------------------------------------------------


def funding_fixture() -> tuple[pd.Series[float], list[float]]:
    """91 rates every 8h ending at AS_OF; the last 9 (3 days) are a fixed sign pattern."""
    n = 91
    rates = [0.0001 * (1 + 0.5 * math.sin(i)) for i in range(n)]
    tail = [0.0003, 0.0002, -0.0001, 0.0, -0.0004, 0.0002, 0.0001, -0.0002, 0.0005]
    rates[-9:] = tail
    stamps = [AS_OF - 8 * H * (n - 1 - i) for i in range(n)]
    return series(dict(zip(stamps, rates, strict=True))), rates


def test_funding_z_scores_match_hand_computed_values() -> None:
    funding, rates = funding_fixture()
    feats = funding_features(funding, AS_OF)
    current = rates[-1]
    # 7d: ts > AS_OF - 7d -> the last 21 rates (8h apart). 30d: the last 90.
    w7, w30 = rates[-21:], rates[-90:]
    z7 = (current - statistics.fmean(w7)) / statistics.stdev(w7)
    z30 = (current - statistics.fmean(w30)) / statistics.stdev(w30)
    assert feats.current == current
    assert feats.z["7d"] == pytest.approx(z7, abs=1e-8)
    assert feats.z["30d"] == pytest.approx(z30, abs=1e-8)
    assert feats.interval_hours == 8.0
    assert feats.annualised == pytest.approx(current * 3 * 365, abs=1e-12)


def test_funding_sign_flips_in_last_three_days() -> None:
    funding, _ = funding_fixture()
    # + + - (0) - + + - +  -> signs + + - - + + - + -> 4 flips; the zero carries no sign.
    assert funding_features(funding, AS_OF).sign_flips == 4
    assert count_sign_flips([0.1, -0.1, 0.0, -0.2, 0.3]) == 2
    assert count_sign_flips([]) == 0


def test_funding_with_too_few_rates_has_no_z() -> None:
    feats = funding_features(series({AS_OF - 8 * H: 0.0001, AS_OF: 0.0002}), AS_OF)
    assert feats.z["7d"] is None
    assert "< 3" in feats.missing["z_7d"]
    empty = funding_features(empty_series(), AS_OF)
    assert empty.current is None and empty.missing["current"]


# -- long/short + taker ------------------------------------------------------------------------------


def test_long_short_ratio_and_24h_change() -> None:
    newest = AS_OF - timedelta(minutes=5)
    ls = series({newest - 24 * H: 1.25, newest - 12 * H: 1.4, newest: 1.5})
    feats = long_short_features(ls, AS_OF)
    assert feats.ratio == 1.5
    assert feats.change_24h == pytest.approx(0.25)
    assert feats.change_24h_pct == pytest.approx(20.0)
    assert long_short_features(ls, AS_OF + 2 * H).ratio is None


def test_taker_buy_sell_ratio_over_1h_and_4h() -> None:
    idx = pd.DatetimeIndex([AS_OF - timedelta(minutes=5 * i) for i in range(60)]).sort_values()
    # Last hour (12 rows): buy 30 / sell 10; the 3 hours before: buy 10 / sell 10.
    buy = [10.0] * 48 + [30.0] * 12
    taker = pd.DataFrame({"buy_vol": buy, "sell_vol": [10.0] * 60}, index=idx)
    feats = taker_features(taker, AS_OF)
    one, four = window(feats, 1), window(feats, 4)
    assert one.buy_sell_ratio == pytest.approx(3.0)  # type: ignore[attr-defined]
    assert one.rows == 12  # type: ignore[attr-defined]
    assert four.buy_sell_ratio == pytest.approx((36 * 10 + 12 * 30) / (48 * 10))  # type: ignore[attr-defined]
    assert feats.unreliable is False


# -- basis (AC5) -------------------------------------------------------------------------------------


def snapshot(
    mark_age_seconds: float, mark_px: str = "101", index_age_seconds: float | None = None
) -> MarketSnapshot:
    at = AS_OF - timedelta(seconds=mark_age_seconds)
    channel_at = {"mark-price": at, "tickers": AS_OF}
    index = None
    if index_age_seconds is not None:
        index_at = AS_OF - timedelta(seconds=index_age_seconds)
        channel_at["index"] = index_at
        index = {"idxPx": "100", "ts": str(int(index_at.timestamp() * 1000))}
    return MarketSnapshot(
        env="demo",
        inst_id="BTC-USDT-SWAP",
        last_msg_at=AS_OF,  # other channels are fresh; only the mark channel's age counts
        ticker=None,
        mark={"instId": "BTC-USDT-SWAP", "markPx": mark_px, "ts": "0"},
        books5=None,
        channel_at=channel_at,
        index=index,
    )


def test_stale_mark_price_gives_null_basis_with_reason_stale() -> None:
    feats = basis_features(snapshot(60), Decimal("100"), AS_OF)
    assert feats.basis_bps is None
    assert feats.reason == "stale"
    none = basis_features(None, Decimal("100"), AS_OF)
    assert none.basis_bps is None and none.reason == "stale"


def test_fresh_mark_price_gives_basis_in_bps() -> None:
    feats = basis_features(snapshot(5), Decimal("100"), AS_OF)
    assert feats.reason is None
    assert feats.basis_bps == pytest.approx(100.0)
    assert feats.mark_px == Decimal("101")
    missing_index = basis_features(snapshot(5), None, AS_OF)
    assert missing_index.basis_bps is None and missing_index.reason == "index_unavailable"


def test_stored_index_limit_is_market_data_max_age_times_the_multiplier() -> None:
    expected = float(DEFAULTS["market_ws"]["market_data_max_age_seconds"]) * float(
        DEFAULTS["agents"]["derivatives"]["index_max_age_multiplier"]
    )
    assert index_max_age_seconds() == expected == 300.0


def test_fresh_stored_index_gives_numeric_basis() -> None:
    feats = basis_features(snapshot(5, index_age_seconds=120), None, AS_OF)
    assert feats.reason is None
    assert feats.basis_bps == pytest.approx(100.0)
    assert feats.index_px == Decimal("100")
    assert feats.index_as_of == AS_OF - timedelta(seconds=120)


def test_stored_index_older_than_the_limit_gives_reason_stale() -> None:
    limit = index_max_age_seconds()
    assert basis_features(snapshot(5, index_age_seconds=limit), None, AS_OF).reason is None
    feats = basis_features(snapshot(5, index_age_seconds=limit + 1), None, AS_OF)
    assert feats.basis_bps is None
    assert feats.reason == "stale"
    assert feats.index_px == Decimal("100")


def test_no_stored_index_gives_reason_index_unavailable() -> None:
    feats = basis_features(snapshot(5), None, AS_OF)
    assert feats.basis_bps is None
    assert feats.reason == "index_unavailable"


def test_compute_features_reads_the_snapshot_index() -> None:
    empty = pd.Series([], index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    frames = DerivativesFrames(
        funding=empty,
        oi=empty,
        oi_series=None,
        price=pd.DataFrame({"c": [], "vol": []}, index=pd.DatetimeIndex([], tz="UTC")),
        long_short=empty,
        taker=pd.DataFrame({"buy_vol": [], "sell_vol": []}, index=pd.DatetimeIndex([], tz="UTC")),
    )
    feats = compute_features("BTC-USDT-SWAP", frames, AS_OF, snapshot=snapshot(5, index_age_seconds=60))
    assert feats.basis.basis_bps == pytest.approx(100.0)
    stale = compute_features("BTC-USDT-SWAP", frames, AS_OF, snapshot=snapshot(5, index_age_seconds=3600))
    assert stale.basis.reason == "stale"


# -- loader (DB) -------------------------------------------------------------------------------------


@pytest.fixture
def inst(engine: Engine) -> Iterator[str]:
    ccy = f"T{uuid.uuid4().hex[:6].upper()}"
    inst_id = f"{ccy}-USDT-SWAP"
    yield inst_id
    with engine.begin() as conn:
        for model, col in (
            (FundingRate, FundingRate.inst_id),
            (OpenInterest, OpenInterest.inst_id),
            (Candle, Candle.inst_id),
        ):
            conn.execute(delete(model).where(col == inst_id))
        conn.execute(delete(LongShortRatio).where(LongShortRatio.ccy == ccy))
        conn.execute(delete(TakerVolume).where(TakerVolume.ccy == ccy))


def test_load_frames_reads_rows_up_to_as_of(engine: Engine, inst: str) -> None:
    ccy = inst.split("-")[0]
    env = "demo"
    with Session(engine) as s, s.begin():
        for i in range(25 * 12 + 1):
            ts = AS_OF - timedelta(minutes=5 * i)
            frac = max(0.0, (288 - i) / 288)
            s.add(
                OpenInterest(env=env, inst_id=inst, ts=ts, oi_usd=Decimal(str(1_000_000 * (1 - 0.06 * frac))))
            )
            s.add(LongShortRatio(env=env, ccy=ccy, period="5m", ts=ts, ratio=Decimal("1.2")))
            s.add(
                TakerVolume(
                    env=env, ccy=ccy, period="5m", ts=ts, buy_vol=Decimal("20"), sell_vol=Decimal("10")
                )
            )
            # A 5m candle that closes at ts (open = ts - 5m).
            px = Decimal(str(100 * (1 + 0.03 * frac)))
            s.add(
                Candle(
                    env=env,
                    inst_id=inst,
                    bar="5m",
                    ts=ts - timedelta(minutes=5),
                    o=px,
                    h=px,
                    l=px,
                    c=px,
                    vol=Decimal("5"),
                    confirmed=True,
                )
            )
        for i in range(10):
            s.add(
                FundingRate(
                    env=env, inst_id=inst, ts=AS_OF - 8 * H * i, funding_rate=Decimal("0.0001") * (i + 1)
                )
            )
        # A row after as_of must not be read.
        s.add(OpenInterest(env=env, inst_id=inst, ts=AS_OF + H, oi_usd=Decimal("0")))

    with Session(engine) as s:
        frames = load_frames(s, env, inst, AS_OF)
    assert frames.oi_series == inst
    assert frames.oi.index.max() == pd.Timestamp(AS_OF)
    feats = compute_features(inst, frames, AS_OF)
    assert feats.unreliable is False
    assert window(feats.open_interest, 24).quadrant == "short_covering"  # type: ignore[attr-defined]
    assert feats.long_short.ratio == pytest.approx(1.2)
    assert window(feats.taker, 1).buy_sell_ratio == pytest.approx(2.0)  # type: ignore[attr-defined]
    assert feats.funding.current == pytest.approx(0.0001)
    assert feats.basis.reason == "stale"
