"""Nearest-neighbour analogs of the current market situation (ATL-81, brief §7.3 ``find_analogs``).

Definitions (pinned by ``quant/tests/test_analogs.py``):

- Only bars that had **closed** by ``as_of`` are used (``ts + bar <= as_of``, the same rule as
  :func:`atlas_quant.data.load_candles`), and only funding rows with ``ts <= as_of``. Everything
  else in the inputs is dropped before any computation, so nothing after ``as_of`` can reach an
  output. The no-look-ahead property test changes those bars and asserts the result is identical.
- The frame is laid on the bar grid from its first to its last closed bar; missing slots are NaN
  and any window or forward horizon touching one is skipped.
- A window of ``window`` bars ending at bar ``e`` is described by
  ``returns_path`` (the ``window`` log returns ``ln(c_t / c_{t-1})`` for t = e-window+1..e),
  ``realised_vol`` (their std, ddof 1), ``volume_z`` (``(vol_e - mean) / std`` of the window's
  volumes, 0 when the std is 0) and, when ``funding`` is given, the last funding rate at or before
  the window's close time ``ts_e + bar``.
- The query is the window ending at the last closed bar. A window is **eligible** when it is
  complete, every horizon's forward close exists (so the horizon ends at or before ``as_of``),
  and it shares no bar with the query window (``e <= e_query - window``).
- Each feature column is z-normalised with the mean and std (ddof 0; a std of 0 counts as 1) of
  the eligible windows. Distance = sqrt(sum over groups of weight * mean squared difference of the
  group's z columns); weights are ``quant.analogs.weights``.
- Neighbours are taken nearest first (ties: the earlier window), skipping any window that overlaps
  one already chosen, until ``k`` are chosen. Neighbours are therefore independent cases.
- Forward return over horizon H = ``c(ts_e + H) / c(ts_e) - 1`` (simple, a fraction). Per horizon
  the neighbours' forward returns are summarised (n, median, p10, p90 with numpy's linear
  quantiles, pct_positive in percent), and ``base_rate`` is the same summary over every eligible
  window.

Inputs are float64; this is analysis, not money.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from atlas_shared.defaults import DEFAULTS
from numpy.lib.stride_tricks import sliding_window_view
from pydantic import BaseModel, ConfigDict

from atlas_quant.grid import bar_delta

FloatArray = npt.NDArray[np.float64]


class Distribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    horizon: str
    n: int
    median: float | None
    p10: float | None
    p90: float | None
    pct_positive: float | None  # percent of forward returns > 0


class Analog(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int  # 1 = nearest
    start_ts: datetime  # open time of the window's first bar
    end_ts: datetime  # open time of the window's last bar; forward returns start at its close
    distance: float
    forward_returns: dict[str, float]  # horizon label -> simple return (fraction)


class AnalogsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    bar: str
    window: int
    k: int
    as_of: datetime
    horizons: list[str]
    features: list[str]
    query_start: datetime | None
    query_end: datetime | None
    n_eligible: int
    neighbours: list[Analog]
    distribution: list[Distribution]
    base_rate: list[Distribution]
    note: str | None  # why there are no neighbours, when there are none


def _cfg() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["quant"]["analogs"]
    return cfg


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return ts.astimezone(UTC)


def _horizon_bars(label: str, step: timedelta) -> int:
    delta = pd.Timedelta(label).to_pytimedelta()
    bars = delta / step
    if delta <= timedelta(0) or bars != int(bars):
        raise ValueError(f"horizon {label!r} is not a positive whole number of {step} bars")
    return int(bars)


def _distribution(label: str, values: FloatArray) -> Distribution:
    if values.size == 0:
        return Distribution(horizon=label, n=0, median=None, p10=None, p90=None, pct_positive=None)
    return Distribution(
        horizon=label,
        n=int(values.size),
        median=float(np.median(values)),
        p10=float(np.quantile(values, 0.1)),
        p90=float(np.quantile(values, 0.9)),
        pct_positive=float(np.mean(values > 0.0) * 100.0),
    )


def _closed_frame(df: pd.DataFrame, step: timedelta, as_of: datetime) -> pd.DataFrame:
    for col in ("c", "vol"):
        if col not in df.columns:
            raise ValueError(f"frame lacks column {col!r}")
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        raise ValueError("frame index must be timezone-aware (UTC)")
    frame = df[["c", "vol"]].astype("float64")
    frame.index = index.tz_convert("UTC")
    frame = frame.sort_index()
    closed = pd.DatetimeIndex(frame.index) + step <= pd.Timestamp(as_of)
    return frame[np.asarray(closed)]


def _funding_feature(funding: pd.Series[float], close_times: pd.DatetimeIndex, as_of: datetime) -> FloatArray:
    idx = pd.DatetimeIndex(funding.index)
    if idx.tz is None:
        raise ValueError("funding index must be timezone-aware (UTC)")
    fr = pd.Series(funding.to_numpy(dtype="float64"), index=idx.tz_convert("UTC")).sort_index()
    fr = fr[np.asarray(pd.DatetimeIndex(fr.index) <= pd.Timestamp(as_of))]
    out = np.full(len(close_times), np.nan)
    if fr.empty:
        return out
    fr_ns = pd.DatetimeIndex(fr.index).tz_convert(None).as_unit("ns").to_numpy().astype("int64")
    close_ns = close_times.tz_convert(None).as_unit("ns").to_numpy().astype("int64")
    pos = np.searchsorted(fr_ns, close_ns, side="right") - 1
    values = fr.to_numpy(dtype="float64")
    ok = pos >= 0
    out[ok] = values[pos[ok]]
    return out


def find_analogs(
    df: pd.DataFrame,
    bar: str,
    window: int,
    *,
    as_of: datetime,
    k: int | None = None,
    horizons: Sequence[str] | None = None,
    funding: pd.Series[float] | None = None,
) -> AnalogsResult:
    """The ``k`` past windows most like the one ending at the last bar closed by ``as_of``.

    ``df`` is shaped like ``load_candles``'s (UTC ``ts`` index on the ``bar`` grid, float ``c`` and
    ``vol``). ``funding`` is an optional funding-rate series indexed by settlement time.
    """
    if window < 2:
        raise ValueError("window must be at least 2 bars")
    cfg = _cfg()
    k_ = int(cfg["k"]) if k is None else k
    if k_ < 1:
        raise ValueError("k must be at least 1")
    labels = [str(h) for h in (cfg["horizons"] if horizons is None else horizons)]
    if not labels:
        raise ValueError("at least one horizon is required")
    weights: Mapping[str, Any] = cfg["weights"]
    step = bar_delta(bar)
    cutoff = _utc(as_of)
    hbars = [_horizon_bars(h, step) for h in labels]
    features = ["returns_path", "realised_vol", "volume_z"] + (["funding"] if funding is not None else [])

    def empty(note: str, q_start: datetime | None = None, q_end: datetime | None = None) -> AnalogsResult:
        none = [_distribution(h, np.empty(0)) for h in labels]
        return AnalogsResult(
            bar=bar, window=window, k=k_, as_of=cutoff, horizons=labels, features=features,
            query_start=q_start, query_end=q_end, n_eligible=0, neighbours=[],
            distribution=none, base_rate=none, note=note,
        )  # fmt: skip

    frame = _closed_frame(df, step, cutoff)
    if frame.empty:
        return empty("no bar had closed by as_of")
    grid = pd.date_range(frame.index[0], frame.index[-1], freq=step)
    if not pd.DatetimeIndex(frame.index).isin(grid).all():
        raise ValueError(f"frame index is not on a regular {bar} grid")
    frame = frame.reindex(grid)
    n_bars = len(grid)
    if n_bars < window + 1:
        return empty(f"fewer than window + 1 = {window + 1} closed bars")

    c = frame["c"].to_numpy(dtype="float64")
    vol = frame["vol"].to_numpy(dtype="float64")
    log_r = np.full(n_bars, np.nan)
    log_r[1:] = np.log(c[1:] / c[:-1])

    # Row j describes the window ending at bar e = j + window - 1; the first usable end is `window`
    # (it needs the close before its first return), so drop row 0.
    ends = np.arange(window, n_bars)
    path = sliding_window_view(log_r, window)[1:]
    vols = sliding_window_view(vol, window)[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        rv = path.std(axis=1, ddof=1)
        v_sd = vols.std(axis=1, ddof=1)
        vol_z = np.where(v_sd > 0, (vols[:, -1] - vols.mean(axis=1)) / np.where(v_sd > 0, v_sd, 1.0), 0.0)
    vol_z = np.where(np.isnan(vols).any(axis=1), np.nan, vol_z)
    groups: dict[str, FloatArray] = {
        "returns_path": path,
        "realised_vol": rv[:, None],
        "volume_z": vol_z[:, None],
    }
    if funding is not None:
        close_times = pd.DatetimeIndex(grid[ends]) + step
        groups["funding"] = _funding_feature(funding, close_times, cutoff)[:, None]

    complete = np.ones(len(ends), dtype=bool)
    for g in groups.values():
        complete &= ~np.isnan(g).any(axis=1)

    q = len(ends) - 1  # the query window ends at the last closed bar
    q_end = grid[ends[q]].to_pydatetime()
    q_start = grid[ends[q] - window + 1].to_pydatetime()
    if not complete[q]:
        return empty("the query window has a missing bar or feature", q_start, q_end)

    fwd = np.full((len(ends), len(hbars)), np.nan)
    for col, hb in enumerate(hbars):
        target = ends + hb
        ok = target < n_bars
        fwd[ok, col] = c[target[ok]] / c[ends[ok]] - 1.0
    eligible = complete & ~np.isnan(fwd).any(axis=1) & (ends <= ends[q] - window)
    rows = np.flatnonzero(eligible)
    if rows.size == 0:
        return empty("no eligible past window", q_start, q_end)

    dist2 = np.zeros(rows.size)
    for name, g in groups.items():
        cand = g[rows]
        mu = cand.mean(axis=0)
        sd = cand.std(axis=0, ddof=0)
        sd = np.where(sd > 0, sd, 1.0)
        zc = (cand - mu) / sd
        zq = (g[q] - mu) / sd
        dist2 += float(weights[name]) * ((zc - zq) ** 2).mean(axis=1)
    dist = np.sqrt(dist2)

    chosen: list[int] = []
    for i in np.lexsort((ends[rows], dist)):
        e = int(ends[rows[i]])
        if all(abs(e - int(ends[rows[j]])) >= window for j in chosen):
            chosen.append(int(i))
            if len(chosen) == k_:
                break

    neighbours = [
        Analog(
            rank=rank,
            start_ts=grid[int(ends[rows[i]]) - window + 1].to_pydatetime(),
            end_ts=grid[int(ends[rows[i]])].to_pydatetime(),
            distance=float(dist[i]),
            forward_returns={h: float(fwd[rows[i], col]) for col, h in enumerate(labels)},
        )
        for rank, i in enumerate(chosen, start=1)
    ]
    picked = rows[np.asarray(chosen, dtype=np.int64)]
    return AnalogsResult(
        bar=bar,
        window=window,
        k=k_,
        as_of=cutoff,
        horizons=labels,
        features=features,
        query_start=q_start,
        query_end=q_end,
        n_eligible=int(rows.size),
        neighbours=neighbours,
        distribution=[_distribution(h, fwd[picked, col]) for col, h in enumerate(labels)],
        base_rate=[_distribution(h, fwd[rows, col]) for col, h in enumerate(labels)],
        note=None,
    )


__all__ = ["Analog", "AnalogsResult", "Distribution", "find_analogs"]
