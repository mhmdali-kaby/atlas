"""ATL-79: load_candles reads the candles hypertable without look-ahead and reports data quality.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_quant import load_candles
from atlas_quant.grid import slot
from atlas_shared import testing
from atlas_shared.db import Candle, make_engine
from sqlalchemy import Engine, delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

ENV = "demo"
INST = "ATL79-USDT-SWAP"  # an id no other suite writes
HOUR = timedelta(hours=1)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(testing.db_settings())
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{testing.database_name()} is not reachable (run `php artisan test` first): {exc}")
    return eng


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Candle).where(Candle.inst_id == INST))


@pytest.fixture
def clean(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    yield engine
    _wipe(engine)


def _row(ts: datetime, bar: str = "1H", vol: str = "10", confirmed: bool = True) -> dict[str, Any]:
    price = Decimal(100) + Decimal(int((ts - T0) / HOUR))
    return {
        "env": ENV,
        "inst_id": INST,
        "bar": bar,
        "ts": ts,
        "o": price,
        "h": price + 2,
        "l": price - 1,
        "c": price + 1,
        "vol": Decimal(vol),
        "vol_ccy": None,
        "vol_ccy_quote": Decimal("1000.5"),
        "confirmed": confirmed,
    }


def _insert(engine: Engine, rows: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        conn.execute(Candle.__table__.insert(), rows)


def _hours(n: int, start: datetime = T0) -> list[datetime]:
    return [start + i * HOUR for i in range(n)]


def test_frame_shape_and_values(clean: Engine) -> None:
    _insert(clean, [_row(ts) for ts in _hours(5)])
    with Session(clean) as session:
        df, quality = load_candles(session, INST, "1H", T0, T0 + 5 * HOUR)
    assert list(df.columns) == ["o", "h", "l", "c", "vol", "vol_ccy", "vol_ccy_quote"]
    assert str(df.index.tz) == "UTC" and df.index.name == "ts"
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert all(str(t) == "float64" for t in df.dtypes)
    assert df.loc[T0 + 2 * HOUR, "c"] == 103.0
    assert df["vol_ccy"].isna().all() and df["vol_ccy_quote"].iloc[0] == 1000.5
    assert quality.expected_bars == 5 and quality.returned_bars == 5 and quality.missing_bars == 0
    assert quality.gaps == [] and not quality.gap_in_window and not quality.interior_gap


def test_as_of_in_the_middle_returns_no_later_bar(clean: Engine) -> None:
    _insert(clean, [_row(ts) for ts in _hours(48)])
    as_of = T0 + timedelta(hours=20, minutes=30)  # the 20:00 bar is still forming
    with clean.connect() as conn:
        df, quality = load_candles(conn, INST, "1H", T0, T0 + 48 * HOUR, as_of=as_of)
    assert len(df) == 20
    assert df.index.max() <= as_of
    assert all(ts.to_pydatetime() + HOUR <= as_of for ts in df.index)  # every bar had closed
    assert df.index.max() == T0 + 19 * HOUR
    # The window is cut at as_of, so the bars after it are not reported missing either.
    assert quality.expected_bars == 20 and quality.missing_bars == 0 and quality.as_of == as_of

    # A bar that closes exactly at as_of is known by then.
    with clean.connect() as conn:
        df2, _ = load_candles(conn, INST, "1H", T0, T0 + 48 * HOUR, as_of=T0 + 20 * HOUR)
    assert df2.index.max() == T0 + 19 * HOUR
    with clean.connect() as conn:
        df3, _ = load_candles(conn, INST, "1H", T0, T0 + 48 * HOUR, as_of=T0 + 21 * HOUR)
    assert df3.index.max() == T0 + 20 * HOUR


def test_three_deleted_bars_are_one_gap(clean: Engine) -> None:
    hours = _hours(24)
    deleted = hours[10:13]
    _insert(clean, [_row(ts) for ts in hours if ts not in deleted])
    with Session(clean) as session:
        df, quality = load_candles(session, INST, "1H", T0, T0 + 24 * HOUR)
    assert len(df) == 21
    assert quality.expected_bars == 24
    assert quality.missing_bars == 3
    assert len(quality.gaps) == 1
    gap = quality.gaps[0]
    assert (gap.from_ts, gap.to_ts, gap.missing_bars, gap.position) == (
        deleted[0],
        deleted[-1],
        3,
        "interior",
    )
    assert quality.gap_in_window and quality.interior_gap


def test_edge_gaps_duplicates_zero_volume_and_unconfirmed(clean: Engine) -> None:
    rows = [_row(ts) for ts in _hours(6, T0 + 2 * HOUR)]  # 02:00..07:00 stored
    rows[1]["vol"] = Decimal(0)
    rows[2]["vol"] = Decimal(0)
    off_grid = T0 + timedelta(hours=4, minutes=7)  # shares the 04:00 slot with the on-grid bar
    extra = _row(off_grid)
    extra["c"] = Decimal(999)
    rows.append(extra)
    rows.append(_row(T0 + 8 * HOUR, confirmed=False))  # forming bar: never read
    _insert(clean, rows)
    with Session(clean) as session:
        df, quality = load_candles(session, INST, "1H", T0, T0 + 10 * HOUR)
    assert len(df) == 6
    assert df.loc[T0 + 4 * HOUR, "c"] == 105.0  # the on-grid row won
    assert off_grid not in df.index
    assert quality.duplicate_count == 1
    assert quality.zero_volume_count == 2
    assert quality.expected_bars == 10 and quality.missing_bars == 4
    assert [(g.from_ts, g.missing_bars, g.position) for g in quality.gaps] == [
        (T0, 2, "leading"),
        (T0 + 8 * HOUR, 2, "trailing"),
    ]
    assert quality.gap_in_window and not quality.interior_gap


def test_daily_bars_follow_the_utc_plus_8_grid(clean: Engine) -> None:
    opens = [T0 + timedelta(days=d, hours=16) for d in range(5)]  # OKX 1D opens 16:00 UTC
    _insert(clean, [_row(ts, bar="1D") for ts in opens if ts != opens[2]])
    with Session(clean) as session:
        df, quality = load_candles(session, INST, "1D", T0, T0 + timedelta(days=5))
    assert len(df) == 4
    assert slot("1D", T0) == T0 - timedelta(hours=8)
    assert quality.expected_bars == 5
    assert [(g.from_ts, g.missing_bars) for g in quality.gaps] == [(opens[2], 1)]


def test_other_env_and_empty_window(clean: Engine) -> None:
    _insert(clean, [_row(ts) for ts in _hours(3)])
    with Session(clean) as session:
        df, quality = load_candles(session, INST, "1H", T0, T0 + 3 * HOUR, env="live")
    assert df.empty and quality.env == "live"
    assert quality.missing_bars == 3 and [g.position for g in quality.gaps] == ["empty"]


def test_naive_datetimes_are_refused(clean: Engine) -> None:
    with Session(clean) as session, pytest.raises(ValueError, match="timezone-aware"):
        load_candles(session, INST, "1H", T0.replace(tzinfo=None), T0 + HOUR)
