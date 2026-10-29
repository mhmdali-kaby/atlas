"""Volatility forecast for a horizon (ATL-80, brief §7.3 ``volatility_forecast``).

Returns are close-to-close log returns in percent, r_t = 100 ln(c_t / c_{t-1}). The horizon in bars
is h = ceil(horizon_hours * 3600 / bar_seconds), the bar being the median spacing of the index.

- ``garch``: GARCH(1,1) with normal errors fitted by ``arch`` (mean ``quant.volatility.garch_mean``,
  constant or zero; no rescaling). The forecast sums the model's 1..h step conditional variances:
  sigma_h = sqrt(sum_k sigma²_{T+k}).
- ``ewma`` (fallback, with ``fallback_reason``): fewer than ``garch_min_bars`` returns, the optimiser
  does not converge, or the fitted alpha + beta ≥ 1. RiskMetrics recursion seeded with the first
  squared return: v_1 = r_1², v_t = λ v_{t-1} + (1 - λ) r_{t-1}²; the next bar's variance is
  v_{T+1} = λ v_T + (1 - λ) r_T² and the horizon variance is h v_{T+1} (EWMA has no mean reversion).

``sigma_atr`` expresses the horizon sigma in price as a multiple of the series' Wilder ATR
(``quant.atr_window``): sigma_pct / 100 * last close / ATR. None when the frame is shorter than the
ATR window.
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
from arch import arch_model
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from atlas_quant._frame import bar_spacing, prepare, quant_cfg
from atlas_quant.stats import _quant, _wilder_atr

_REQUIRED = ("h", "l", "c")


class VolParams(BaseModel):
    """Tuning for :func:`volatility_forecast`; :meth:`from_defaults` reads ``quant.volatility``."""

    model_config = ConfigDict(frozen=True)

    garch_min_bars: int
    garch_mean: Literal["constant", "zero"]
    ewma_lambda: float
    max_iter: int | None = None  # optimiser iterations for arch; None = arch's default

    @classmethod
    def from_defaults(cls) -> VolParams:
        cfg = quant_cfg("volatility")
        return cls(
            garch_min_bars=int(cfg["garch_min_bars"]),
            garch_mean=cfg["garch_mean"],
            ewma_lambda=float(cfg["ewma_lambda"]),
        )


class VolForecast(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["garch", "ewma"]
    fallback_reason: str | None
    horizon_hours: float
    horizon_bars: int
    bar_hours: float
    n_returns: int
    sigma_pct: float  # horizon sigma of the log return, in %
    sigma_next_bar_pct: float  # one-bar-ahead sigma, in %
    sigma_atr: float | None  # horizon sigma in price / ATR
    atr: float | None
    last_close: float
    as_of: datetime  # open time of the last bar
    # garch: omega, alpha, beta, mu (mu 0 for the zero mean); ewma: lambda.
    params: dict[str, float]


def _ewma(r: NDArray[np.float64], lam: float, h: int) -> tuple[float, float]:
    v = float(r[0] ** 2)
    for x in r[:-1]:
        v = lam * v + (1.0 - lam) * float(x) ** 2
    nxt = lam * v + (1.0 - lam) * float(r[-1]) ** 2
    return nxt, nxt * h


def _garch(r: NDArray[np.float64], p: VolParams, h: int) -> tuple[float, float, dict[str, float]] | str:
    """(next-bar variance, horizon variance, params), or the reason it cannot be used."""
    model = arch_model(
        r,
        mean="Constant" if p.garch_mean == "constant" else "Zero",
        vol="GARCH",
        p=1,
        q=1,
        dist="normal",
        rescale=False,
    )
    options = {"maxiter": p.max_iter} if p.max_iter is not None else None
    try:
        # show_warning=False: arch otherwise installs a global "always" filter for its warning.
        res = model.fit(disp="off", options=options, show_warning=False)
    except (ValueError, np.linalg.LinAlgError) as exc:
        return f"GARCH fit failed: {exc}"
    if int(res.convergence_flag) != 0:
        return f"GARCH optimiser did not converge (flag {int(res.convergence_flag)})"
    params = {str(k): float(v) for k, v in res.params.items()}
    omega, alpha, beta = params["omega"], params["alpha[1]"], params["beta[1]"]
    if not all(math.isfinite(x) for x in (omega, alpha, beta)) or alpha + beta >= 1.0:
        return f"GARCH fit is not stationary (alpha + beta = {alpha + beta:.4f})"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        variance = np.asarray(res.forecast(horizon=h, reindex=False).variance, dtype="float64")[-1]
    if not np.all(np.isfinite(variance)):
        return "GARCH forecast is not finite"
    out = {"omega": omega, "alpha": alpha, "beta": beta, "mu": params.get("mu", 0.0)}
    return float(variance[0]), float(variance.sum()), out


def volatility_forecast(
    df: pd.DataFrame, horizon_hours: float, params: VolParams | None = None
) -> VolForecast:
    """Forecast sigma over ``horizon_hours`` for a frame shaped like ``load_candles``'s."""
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    p = params or VolParams.from_defaults()
    frame = prepare(df, _REQUIRED)
    if len(frame) < 3:
        raise ValueError("need at least 3 bars (2 returns) for a volatility forecast")
    spacing = bar_spacing(pd.DatetimeIndex(frame.index))
    bar_hours = spacing / timedelta(hours=1)
    h = max(1, math.ceil(horizon_hours / bar_hours - 1e-9))
    c = frame["c"].to_numpy(dtype="float64")
    r = np.log(c[1:] / c[:-1]) * 100.0
    if not np.all(np.isfinite(r)):
        raise ValueError("closes must be positive and finite")

    reason: str | None
    if len(r) < p.garch_min_bars:
        reason = f"{len(r)} returns < garch_min_bars {p.garch_min_bars}"
    else:
        fitted = _garch(r, p, h)
        if isinstance(fitted, str):
            reason = fitted
        else:
            next_var, horizon_var, used = fitted
            return _result("garch", None, next_var, horizon_var, used, h, bar_hours, horizon_hours, r, frame)
    next_var, horizon_var = _ewma(r, p.ewma_lambda, h)
    return _result(
        "ewma",
        reason,
        next_var,
        horizon_var,
        {"lambda": p.ewma_lambda},
        h,
        bar_hours,
        horizon_hours,
        r,
        frame,
    )


def _result(
    method: Literal["garch", "ewma"],
    reason: str | None,
    next_var: float,
    horizon_var: float,
    used: dict[str, float],
    h: int,
    bar_hours: float,
    horizon_hours: float,
    r: NDArray[np.float64],
    frame: pd.DataFrame,
) -> VolForecast:
    sigma_pct = math.sqrt(horizon_var)
    last_close = float(frame["c"].iloc[-1])
    atr = _wilder_atr(frame, int(_quant()["atr_window"]))
    sigma_atr = sigma_pct / 100.0 * last_close / atr if atr else None
    return VolForecast(
        method=method,
        fallback_reason=reason,
        horizon_hours=float(horizon_hours),
        horizon_bars=h,
        bar_hours=bar_hours,
        n_returns=len(r),
        sigma_pct=sigma_pct,
        sigma_next_bar_pct=math.sqrt(next_var),
        sigma_atr=sigma_atr,
        atr=atr,
        last_close=last_close,
        as_of=pd.DatetimeIndex(frame.index)[-1].to_pydatetime(),
        params=used,
    )
