"""move_since_atr (ATL-106): hand-calculated golden values and the no-look-ahead rule."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from atlas_quant import move_since_atr

H = [11.0, 12.0, 13.0, 12.0, 14.0, 99.0]
L = [9.0, 10.0, 10.0, 11.0, 11.0, 1.0]
C = [10.0, 11.0, 12.0, 11.5, 13.0, 50.0]


def _bars() -> pd.DataFrame:
    idx = pd.DatetimeIndex([datetime(2026, 10, 20, h, tzinfo=UTC) for h in range(len(C))], name="ts")
    return pd.DataFrame({"o": C, "h": H, "l": L, "c": C}, index=idx)


def test_matches_a_hand_calculation() -> None:
    # first_seen 04:30: reference bar 03:00 (closes 04:00), close 11.5. Bars 00:00..03:00:
    # TR = [2 (h-l), 2, 3, 1]; Wilder ATR(3): seed mean(2, 2, 3) = 7/3, then (7/3 * 2 + 1) / 3 = 17/9.
    # (13.4 - 11.5) / (17/9) = 1.9 * 9 / 17.
    out = move_since_atr(_bars(), datetime(2026, 10, 20, 4, 30, tzinfo=UTC), 13.4, bar="1H", window=3)
    assert out.reason is None
    assert out.ref_ts == datetime(2026, 10, 20, 3, tzinfo=UTC)
    assert out.ref_close == 11.5
    assert out.atr == pytest.approx(17 / 9)
    assert out.value == pytest.approx(1.9 * 9 / 17)


def test_bars_after_first_seen_never_enter() -> None:
    at = datetime(2026, 10, 20, 4, 30, tzinfo=UTC)
    full = move_since_atr(_bars(), at, 13.4, bar="1H", window=3)
    cut = move_since_atr(_bars().iloc[:4], at, 13.4, bar="1H", window=3)
    assert full == cut


def test_negative_move_is_signed() -> None:
    out = move_since_atr(_bars(), datetime(2026, 10, 20, 4, tzinfo=UTC), 9.6, bar="1H", window=3)
    assert out.value == pytest.approx((9.6 - 11.5) / (17 / 9))


def test_null_with_reason_when_candles_are_missing() -> None:
    at = datetime(2026, 10, 20, 4, 30, tzinfo=UTC)
    empty = move_since_atr(_bars().iloc[0:0], at, 13.4, bar="1H", window=3)
    assert empty.value is None and empty.reason and "missing" in empty.reason
    gap = move_since_atr(_bars().drop(_bars().index[3]), at, 13.4, bar="1H", window=3)
    assert gap.value is None and gap.reason and "missing" in gap.reason
    short = move_since_atr(_bars(), datetime(2026, 10, 20, 2, 30, tzinfo=UTC), 13.4, bar="1H", window=3)
    assert short.value is None and short.reason and "fewer than 3" in short.reason
    assert short.ref_close == 11.0
    no_price = move_since_atr(_bars(), at, None, bar="1H", window=3)
    assert no_price.value is None and no_price.reason == "no current price"
