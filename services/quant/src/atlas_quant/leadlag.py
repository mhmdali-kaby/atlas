"""Cross-asset lead/lag (ATL-81, brief §7.3 ``cross_asset_lead_lag``).

Definitions (pinned by ``quant/tests/test_leadlag.py``):

- ``a`` and ``b`` are price series (or frames with a ``c`` column) on the same bar grid. They are
  inner-joined on timestamp, then turned into log returns ``ra``, ``rb``.
- ``corr(lag) = pearson(ra_t, rb_{t+lag})`` for lag = -max_lag .. +max_lag, over the pairs both
  present. A positive lag means ``a`` moves first and ``b`` follows ``lag`` bars later; a negative
  lag means ``b`` leads. ``None`` when fewer than 3 pairs or either side is constant.
- ``best_lag`` maximises ``|corr|``; ties go to the smaller ``|lag|``, then the negative lag.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict


class LagCorrelation(BaseModel):
    model_config = ConfigDict(frozen=True)

    lag: int
    n: int
    corr: float | None


class LeadLagResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_lag: int
    n: int  # aligned returns
    lags: list[LagCorrelation]
    best_lag: int | None
    best_corr: float | None
    corr_lag0: float | None
    leader: Literal["a", "b", "none"] | None  # None when no lag had a correlation


def _prices(x: pd.Series[float] | pd.DataFrame, name: str) -> pd.Series[float]:
    s = x["c"] if isinstance(x, pd.DataFrame) else x
    index = pd.DatetimeIndex(s.index)
    if index.tz is None:
        raise ValueError(f"{name} index must be timezone-aware (UTC)")
    return pd.Series(s.to_numpy(dtype="float64"), index=index.tz_convert("UTC")).sort_index()


def _pearson(
    x: np.ndarray[tuple[int], np.dtype[np.float64]], y: np.ndarray[tuple[int], np.dtype[np.float64]]
) -> tuple[int, float | None]:
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    n = int(x.size)
    if n < 3:
        return n, None
    dx, dy = x - x.mean(), y - y.mean()
    den = float(np.sqrt((dx * dx).sum() * (dy * dy).sum()))
    if den == 0.0:
        return n, None
    return n, float((dx * dy).sum() / den)


def cross_asset_lead_lag(
    a: pd.Series[float] | pd.DataFrame, b: pd.Series[float] | pd.DataFrame, max_lag: int
) -> LeadLagResult:
    if max_lag < 0:
        raise ValueError("max_lag must be >= 0")
    joined = pd.concat([_prices(a, "a"), _prices(b, "b")], axis=1, join="inner").dropna()
    pa = joined.iloc[:, 0].to_numpy(dtype="float64")
    pb = joined.iloc[:, 1].to_numpy(dtype="float64")
    ra = np.log(pa[1:] / pa[:-1])
    rb = np.log(pb[1:] / pb[:-1])
    size = int(ra.size)

    lags: list[LagCorrelation] = []
    for lag in range(-max_lag, max_lag + 1):
        if abs(lag) >= size:
            lags.append(LagCorrelation(lag=lag, n=0, corr=None))
            continue
        x, y = (ra[: size - lag], rb[lag:]) if lag >= 0 else (ra[-lag:], rb[: size + lag])
        n, corr = _pearson(x, y)
        lags.append(LagCorrelation(lag=lag, n=n, corr=corr))

    scored = [lc for lc in lags if lc.corr is not None]
    best = min(scored, key=lambda lc: (-abs(lc.corr or 0.0), abs(lc.lag), lc.lag)) if scored else None
    zero = next(lc for lc in lags if lc.lag == 0)
    leader: Literal["a", "b", "none"] | None = None
    if best is not None:
        leader = "a" if best.lag > 0 else "b" if best.lag < 0 else "none"
    return LeadLagResult(
        max_lag=max_lag,
        n=size,
        lags=lags,
        best_lag=best.lag if best else None,
        best_corr=best.corr if best else None,
        corr_lag0=zero.corr,
        leader=leader,
    )


__all__ = ["LagCorrelation", "LeadLagResult", "cross_asset_lead_lag"]
