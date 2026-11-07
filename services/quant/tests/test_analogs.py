"""ATL-81: find_analogs — no look-ahead (hypothesis), planted-motif recovery, eligibility rules."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from atlas_quant import find_analogs
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

START = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _frame(returns: np.ndarray, vol: np.ndarray) -> pd.DataFrame:
    c = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))
    idx = pd.DatetimeIndex([START + i * HOUR for i in range(len(c))], name="ts")
    return pd.DataFrame({"c": c, "vol": vol[: len(c)]}, index=idx)


def _random_frame(seed: int, n: int) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    df = _frame(rng.normal(0, 0.01, n - 1), rng.uniform(50, 150, n))
    funding_idx = pd.DatetimeIndex([START + i * 8 * HOUR for i in range(n // 8 + 1)])
    funding = pd.Series(rng.normal(0.0001, 0.0002, len(funding_idx)), index=funding_idx)
    return df, funding


# --- the property: bars after as_of cannot change any output ------------------------------------


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 10_000),
    cut=st.integers(60, 230),
    minute=st.integers(0, 59),
    window=st.integers(2, 8),
    k=st.integers(1, 8),
    mutate_seed=st.integers(0, 10_000),
    drop_future=st.booleans(),
    extra=st.integers(0, 40),
)
def test_changing_bars_after_as_of_changes_nothing(
    seed: int,
    cut: int,
    minute: int,
    window: int,
    k: int,
    mutate_seed: int,
    drop_future: bool,
    extra: int,
) -> None:
    df, funding = _random_frame(seed, 240)
    # as_of falls inside bar `cut`: that bar is still forming, so it and everything after is future.
    as_of = START + cut * HOUR + timedelta(minutes=minute)
    horizons = ("1h", "4h", "24h")

    base = find_analogs(df, "1H", window, as_of=as_of, k=k, horizons=horizons, funding=funding)

    rng = np.random.default_rng(mutate_seed)
    future = (df.index + HOUR) > as_of
    changed = df.copy()
    changed.loc[future, "c"] = rng.uniform(1.0, 1000.0, int(future.sum()))
    changed.loc[future, "vol"] = rng.uniform(0.0, 1e6, int(future.sum()))
    if drop_future:
        keep = ~future | (rng.uniform(size=len(df)) < 0.5)
        changed = changed[keep]
    if extra:
        last = changed.index[-1]
        more = pd.DataFrame(
            {"c": rng.uniform(1, 1000, extra), "vol": rng.uniform(0, 1e6, extra)},
            index=pd.DatetimeIndex([last + (i + 1) * HOUR for i in range(extra)], name="ts"),
        )
        changed = pd.concat([changed, more])
    f_future = funding.index > as_of
    changed_funding = funding.copy()
    changed_funding[f_future] = rng.normal(0, 1, int(f_future.sum()))

    after = find_analogs(changed, "1H", window, as_of=as_of, k=k, horizons=horizons, funding=changed_funding)
    assert after == base  # neighbours, distances and every distribution, bit for bit

    # And what was used really is in the past: every neighbour's longest horizon ended by as_of,
    # and no neighbour shares a bar with the query window.
    assert base.query_end is not None
    for nb in base.neighbours:
        assert nb.end_ts + HOUR + timedelta(hours=24) <= as_of
        assert nb.end_ts < base.query_end - (window - 1) * HOUR
        assert nb.end_ts <= base.query_end - window * HOUR


def test_query_is_the_last_closed_bar() -> None:
    df, _ = _random_frame(1, 120)
    as_of = START + 100 * HOUR + timedelta(minutes=30)
    res = find_analogs(df, "1H", 5, as_of=as_of, k=3, horizons=("1h",))
    assert res.query_end == START + 99 * HOUR
    assert res.query_start == START + 95 * HOUR


# --- planted motif ------------------------------------------------------------------------------

MOTIF = np.array([0.03, -0.02, 0.04, -0.015, 0.035, -0.025, 0.02, 0.01])
FOLLOW = 0.005  # log return per bar for the 4 bars after each planted motif


def _planted() -> tuple[pd.DataFrame, list[datetime], datetime]:
    rng = np.random.default_rng(7)
    n = 4000
    returns = rng.normal(0, 0.001, n)
    w = len(MOTIF)
    ends: list[int] = []
    pos = 50
    while len(ends) < 25:
        returns[pos : pos + w] = MOTIF
        returns[pos + w : pos + w + 4] = FOLLOW
        ends.append(pos + w)  # bar index of the motif's last bar (returns[i] ends bar i + 1)
        pos += 140
    returns[n - w : n] = MOTIF  # the query: the motif has just printed, nothing after it yet
    df = _frame(returns, rng.uniform(80, 120, n + 1))
    as_of = START + (n + 1) * HOUR
    return df, [START + e * HOUR for e in ends], as_of


def test_planted_motif_occurrences_are_the_nearest_neighbours() -> None:
    df, planted, as_of = _planted()
    res = find_analogs(df, "1H", len(MOTIF), as_of=as_of, k=20, horizons=("1h", "4h", "24h"))
    assert res.note is None
    assert res.query_end == START + 4000 * HOUR
    assert len(res.neighbours) == 20
    assert {nb.end_ts for nb in res.neighbours} <= set(planted)
    by_h = {d.horizon: d for d in res.distribution}
    assert by_h["1h"].n == 20
    assert by_h["1h"].median == pytest.approx(math.exp(FOLLOW) - 1, rel=1e-12)
    assert by_h["4h"].median == pytest.approx(math.exp(4 * FOLLOW) - 1, rel=1e-12)
    assert by_h["4h"].pct_positive == 100.0
    base = {d.horizon: d for d in res.base_rate}
    assert base["4h"].n == res.n_eligible
    assert base["4h"].median is not None and abs(base["4h"].median) < 0.002  # noise drifts ~0


def test_planted_motif_is_found_with_the_default_k_and_horizons() -> None:
    df, planted, as_of = _planted()
    res = find_analogs(df, "1H", len(MOTIF), as_of=as_of)
    assert res.k == 20 and res.horizons == ["1h", "4h", "24h", "72h"]
    assert {nb.end_ts for nb in res.neighbours} <= set(planted)
    assert [nb.rank for nb in res.neighbours] == list(range(1, 21))
    distances = [nb.distance for nb in res.neighbours]
    assert distances == sorted(distances)


def test_neighbours_never_overlap_each_other() -> None:
    df, _ = _random_frame(3, 600)
    res = find_analogs(df, "1H", 6, as_of=START + 600 * HOUR, k=20, horizons=("4h",))
    ends = sorted(nb.end_ts for nb in res.neighbours)
    assert len(ends) == 20
    assert all(b - a >= 6 * HOUR for a, b in itertools.pairwise(ends))


def test_funding_feature_is_listed_and_changes_distances() -> None:
    df, funding = _random_frame(4, 400)
    as_of = START + 400 * HOUR
    without = find_analogs(df, "1H", 6, as_of=as_of, k=5, horizons=("4h",))
    with_f = find_analogs(df, "1H", 6, as_of=as_of, k=5, horizons=("4h",), funding=funding)
    assert without.features == ["returns_path", "realised_vol", "volume_z"]
    assert with_f.features[-1] == "funding"
    assert [n.distance for n in without.neighbours] != [n.distance for n in with_f.neighbours]


def test_gap_in_query_window_gives_no_neighbours() -> None:
    df, _ = _random_frame(5, 200)
    holed = df.drop(df.index[197])
    res = find_analogs(holed, "1H", 5, as_of=START + 200 * HOUR, k=5, horizons=("1h",))
    assert res.neighbours == [] and res.note is not None
    assert res.distribution[0].n == 0


def test_windows_touching_a_gap_are_not_eligible() -> None:
    df, _ = _random_frame(6, 200)
    full = find_analogs(df, "1H", 5, as_of=START + 200 * HOUR, k=5, horizons=("1h",))
    holed = find_analogs(df.drop(df.index[50]), "1H", 5, as_of=START + 200 * HOUR, k=5, horizons=("1h",))
    # bar 50 missing removes the windows containing return 50 or 51 (5 + 1 ends) and the forward
    # return of the window ending at 49.
    assert holed.n_eligible == full.n_eligible - 7


@pytest.mark.parametrize("horizon", ["90min", "0h", "-1h"])
def test_horizon_must_be_whole_bars(horizon: str) -> None:
    df, _ = _random_frame(1, 50)
    with pytest.raises(ValueError):
        find_analogs(df, "1H", 3, as_of=START + 50 * HOUR, horizons=(horizon,))


def test_rejects_naive_inputs() -> None:
    df, _ = _random_frame(1, 50)
    with pytest.raises(ValueError):
        find_analogs(df, "1H", 3, as_of=datetime(2026, 1, 2))  # noqa: DTZ001
    naive = df.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError):
        find_analogs(naive, "1H", 3, as_of=START + 50 * HOUR)
