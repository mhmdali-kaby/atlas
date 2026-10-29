"""Market regime of one candle series (ATL-80, brief §7.3 ``detect_regime``).

Primary method: a Gaussian HMM (hmmlearn, ``quant.regime.n_states`` states, seeded with
``random_state`` so the same frame always gives the same answer) fitted on two features per bar:
the log return in % and ln of the rolling standard deviation of log returns in % over ``vol_window``
bars. Each state is labelled from the fitted model's parameters:

- ``high_vol``: the state's typical rolling vol (exp of its mean ln vol) is at least
  ``high_vol_ratio`` times the calmest state's;
- otherwise ``trend_up`` / ``trend_down`` when the t-statistic of the state's mean return,
  mean / std * sqrt(bars Viterbi assigns to the state), is at least ``trend_t`` in size;
- otherwise ``low_vol`` for the calmest state when some state is ``high_vol``, else ``range``.

The current label is the Viterbi state of the last bar; ``probability`` is the posterior probability
of the last bar being in any state with that label; ``days_in_regime`` runs from the first bar of the
final run of that label to the end of the last bar.

Fallback (``method="fallback"``, with ``fallback_reason``): fewer than ``min_bars`` feature rows, the
fit raises, or EM stops at ``hmm_n_iter`` without the log-likelihood gain dropping under ``hmm_tol``.
Each bar is then labelled by rule: ``high_vol`` when the rolling vol's percentile within the last
``vol_percentile_lookback`` bars is ≥ ``high_vol_percentile``; else ``trend_up`` / ``trend_down``
(by +DI vs -DI) when Wilder ADX(``adx_window``) ≥ ``adx_trend_threshold``; else ``low_vol`` when the
percentile is ≤ ``low_vol_percentile``; else ``range``. A rule label has no probability (None).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM  # type: ignore[import-untyped]
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from atlas_quant._frame import bar_spacing, prepare, quant_cfg

RegimeLabel = Literal["trend_up", "trend_down", "range", "high_vol", "low_vol"]
_REQUIRED = ("h", "l", "c")


class RegimeParams(BaseModel):
    """Tuning for :func:`detect_regime`; :meth:`from_defaults` reads ``quant.regime``."""

    model_config = ConfigDict(frozen=True)

    n_states: int
    min_bars: int
    vol_window: int
    hmm_n_iter: int
    hmm_tol: float
    hmm_covariance_type: Literal["full", "diag", "spherical", "tied"]
    random_state: int
    high_vol_ratio: float
    trend_t: float
    adx_window: int
    adx_trend_threshold: float
    vol_percentile_lookback: int
    high_vol_percentile: float
    low_vol_percentile: float

    @classmethod
    def from_defaults(cls) -> RegimeParams:
        cfg = quant_cfg("regime")
        return cls(
            n_states=int(cfg["n_states"]),
            min_bars=int(cfg["min_bars"]),
            vol_window=int(cfg["vol_window"]),
            hmm_n_iter=int(cfg["hmm_n_iter"]),
            hmm_tol=float(cfg["hmm_tol"]),
            hmm_covariance_type=cfg["hmm_covariance_type"],
            random_state=int(cfg["random_state"]),
            high_vol_ratio=float(cfg["high_vol_ratio"]),
            trend_t=float(cfg["trend_t"]),
            adx_window=int(cfg["adx_window"]),
            adx_trend_threshold=float(cfg["adx_trend_threshold"]),
            vol_percentile_lookback=int(cfg["vol_percentile_lookback"]),
            high_vol_percentile=float(cfg["high_vol_percentile"]),
            low_vol_percentile=float(cfg["low_vol_percentile"]),
        )


class RegimeState(BaseModel):
    """One fitted HMM state, as labelled."""

    model_config = ConfigDict(frozen=True)

    state: int
    label: RegimeLabel
    mean_return_pct: float  # per bar
    return_std_pct: float  # per bar
    typical_vol_pct: float  # exp(mean ln rolling vol), per bar
    occupancy: float  # share of feature rows Viterbi assigns to this state


class RegimeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    method: Literal["hmm", "fallback"]
    fallback_reason: str | None
    label: RegimeLabel
    probability: float | None  # posterior of the current label (hmm only)
    days_in_regime: float
    bars_in_regime: int
    regime_start: datetime  # open time of the first bar of the current run
    as_of: datetime  # open time of the last bar
    n: int  # bars in the frame
    n_states: int | None
    states: list[RegimeState]
    adx: float | None  # current Wilder ADX, None when too short
    vol_percentile: float | None  # current rolling-vol percentile (0-100) in the lookback


def _log_returns_pct(c: pd.Series[float]) -> pd.Series[float]:
    values = np.log((c / c.shift(1)).to_numpy(dtype="float64")) * 100.0
    return pd.Series(values, index=c.index, dtype="float64")


def _wilder_smooth(x: NDArray[np.float64], window: int) -> NDArray[np.float64]:
    """Wilder's running mean of x[1:] (x[0] is undefined): seed = mean of the first ``window``."""
    out = np.full(len(x), np.nan)
    if len(x) < window + 1:
        return out
    value = float(np.mean(x[1 : window + 1]))
    out[window] = value
    for i in range(window + 1, len(x)):
        value = (value * (window - 1) + float(x[i])) / window
        out[i] = value
    return out


def _adx(frame: pd.DataFrame, window: int) -> tuple[pd.Series[float], pd.Series[float]]:
    """Wilder ADX and (+DI - -DI) per bar; NaN until defined (2 x window bars)."""
    h = frame["h"].to_numpy(dtype="float64")
    lo = frame["l"].to_numpy(dtype="float64")
    c = frame["c"].to_numpy(dtype="float64")
    n = len(frame)
    up = np.zeros(n)
    down = np.zeros(n)
    tr = np.zeros(n)
    up[1:] = h[1:] - h[:-1]
    down[1:] = lo[:-1] - lo[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr[1:] = np.fmax(h[1:] - lo[1:], np.fmax(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    s_tr = _wilder_smooth(tr, window)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * _wilder_smooth(plus_dm, window) / s_tr
        minus_di = 100.0 * _wilder_smooth(minus_dm, window) / s_tr
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = np.full(n, np.nan)
    first = window  # first defined dx
    if n >= first + window:
        value = float(np.nanmean(dx[first : first + window]))
        adx[first + window - 1] = value
        for i in range(first + window, n):
            d = float(dx[i])
            if not math.isnan(d):
                value = (value * (window - 1) + d) / window
            adx[i] = value
    index = frame.index
    return (
        pd.Series(adx, index=index, dtype="float64"),
        pd.Series(plus_di - minus_di, index=index, dtype="float64"),
    )


def _vol_percentile(vol: pd.Series[float], lookback: int, min_periods: int) -> pd.Series[float]:
    def rank(window: NDArray[np.float64]) -> float:
        return float(np.mean(window <= window[-1]) * 100.0)

    return vol.rolling(lookback, min_periods=min_periods).apply(rank, raw=True)


def _opt(value: float) -> float | None:
    return None if math.isnan(value) or math.isinf(value) else value


def _final_run(labels: Sequence[str]) -> int:
    """Index where the final run of identical labels starts."""
    start = len(labels) - 1
    while start > 0 and labels[start - 1] == labels[-1]:
        start -= 1
    return start


def _label_states(model: GaussianHMM, states: NDArray[np.int64], params: RegimeParams) -> list[RegimeLabel]:
    means = np.asarray(model.means_, dtype="float64")
    covars = np.asarray(model.covars_, dtype="float64")  # always (k, f, f) in hmmlearn
    typical_vol = np.exp(means[:, 1])
    calmest = int(np.argmin(typical_vol))
    high = typical_vol >= params.high_vol_ratio * typical_vol[calmest]
    labels: list[RegimeLabel] = []
    for k in range(len(means)):
        std = math.sqrt(max(float(covars[k, 0, 0]), 0.0))
        count = int(np.sum(states == k))
        t = float(means[k, 0]) / std * math.sqrt(count) if std > 0 else 0.0
        if high[k]:
            labels.append("high_vol")
        elif abs(t) >= params.trend_t:
            labels.append("trend_up" if t > 0 else "trend_down")
        elif k == calmest and bool(high.any()):
            labels.append("low_vol")
        else:
            labels.append("range")
    return labels


class _HmmFailed(Exception):
    pass


def _fit_hmm(features: NDArray[np.float64], params: RegimeParams) -> GaussianHMM:
    model = GaussianHMM(
        n_components=params.n_states,
        covariance_type=params.hmm_covariance_type,
        n_iter=params.hmm_n_iter,
        tol=params.hmm_tol,
        random_state=params.random_state,
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit(features)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        raise _HmmFailed(f"HMM fit failed: {exc}") from exc
    monitor = model.monitor_
    history = [float(v) for v in monitor.history]
    if not history or not all(math.isfinite(v) for v in history):
        raise _HmmFailed("HMM log-likelihood is not finite")
    stopped_by_tol = len(history) >= 2 and history[-1] - history[-2] < params.hmm_tol
    if monitor.iter >= params.hmm_n_iter and not stopped_by_tol:
        raise _HmmFailed(f"HMM did not converge in {params.hmm_n_iter} iterations")
    return model


def detect_regime(df: pd.DataFrame, params: RegimeParams | None = None) -> RegimeResult:
    """Current regime of a frame shaped like ``load_candles``'s (UTC index, float h/l/c columns)."""
    p = params or RegimeParams.from_defaults()
    frame = prepare(df, _REQUIRED)
    spacing = bar_spacing(pd.DatetimeIndex(frame.index))
    c = frame["c"].astype("float64")
    log_r = _log_returns_pct(c)
    vol = log_r.rolling(p.vol_window, min_periods=p.vol_window).std(ddof=1)
    pct = _vol_percentile(vol, p.vol_percentile_lookback, p.vol_window)
    adx, di_diff = _adx(frame, p.adx_window)
    current_adx = _opt(float(adx.iloc[-1]))
    current_pct = _opt(float(pct.iloc[-1]))

    feats = pd.DataFrame({"r": log_r, "lv": np.log(vol)}).replace([np.inf, -np.inf], np.nan).dropna()
    reason: str | None = None
    if len(feats) < p.min_bars:
        reason = f"{len(feats)} feature rows < min_bars {p.min_bars}"
    else:
        x = feats.to_numpy(dtype="float64")
        try:
            model = _fit_hmm(x, p)
            states = np.asarray(model.predict(x), dtype="int64")
            posterior = np.asarray(model.predict_proba(x), dtype="float64")
        except _HmmFailed as exc:
            reason = str(exc)
        except (ValueError, np.linalg.LinAlgError) as exc:
            reason = f"HMM decode failed: {exc}"
        else:
            state_labels = _label_states(model, states, p)
            labels = [state_labels[int(s)] for s in states]
            start = _final_run(labels)
            current = state_labels[int(states[-1])]
            prob = float(sum(posterior[-1, k] for k in range(p.n_states) if state_labels[k] == current))
            means = np.asarray(model.means_, dtype="float64")
            covars = np.asarray(model.covars_, dtype="float64")
            described = [
                RegimeState(
                    state=k,
                    label=state_labels[k],
                    mean_return_pct=float(means[k, 0]),
                    return_std_pct=math.sqrt(max(float(covars[k, 0, 0]), 0.0)),
                    typical_vol_pct=float(math.exp(means[k, 1])),
                    occupancy=float(np.mean(states == k)),
                )
                for k in range(p.n_states)
            ]
            return _result(
                "hmm",
                None,
                labels,
                start,
                pd.DatetimeIndex(feats.index),
                frame,
                spacing,
                prob,
                p.n_states,
                described,
                current_adx,
                current_pct,
            )

    rule_labels = _rule_labels(adx, di_diff, pct, p)
    defined = rule_labels.dropna()
    if defined.empty:
        raise ValueError(f"too few bars for a regime: need more than {p.vol_window} bars")
    fallback_labels = [str(v) for v in defined.tolist()]
    return _result(
        "fallback",
        reason,
        fallback_labels,
        _final_run(fallback_labels),
        pd.DatetimeIndex(defined.index),
        frame,
        spacing,
        None,
        None,
        [],
        current_adx,
        current_pct,
    )


def _rule_labels(
    adx: pd.Series[float], di_diff: pd.Series[float], pct: pd.Series[float], p: RegimeParams
) -> pd.Series[str]:
    out: list[str | None] = []
    for a, d, q in zip(adx.tolist(), di_diff.tolist(), pct.tolist(), strict=True):
        if math.isnan(q):
            out.append(None)
        elif q >= p.high_vol_percentile:
            out.append("high_vol")
        elif not math.isnan(a) and a >= p.adx_trend_threshold:
            out.append("trend_up" if d > 0 else "trend_down")
        elif q <= p.low_vol_percentile:
            out.append("low_vol")
        else:
            out.append("range")
    return pd.Series(out, index=adx.index, dtype="object")


def _result(
    method: Literal["hmm", "fallback"],
    reason: str | None,
    labels: Sequence[str],
    start: int,
    label_index: pd.DatetimeIndex,
    frame: pd.DataFrame,
    spacing: timedelta,
    probability: float | None,
    n_states: int | None,
    states: list[RegimeState],
    adx: float | None,
    vol_percentile: float | None,
) -> RegimeResult:
    start_ts = label_index[start].to_pydatetime()
    last_ts = label_index[-1].to_pydatetime()
    days = (last_ts + spacing - start_ts) / timedelta(days=1)
    label: RegimeLabel = labels[-1]  # type: ignore[assignment]
    return RegimeResult(
        method=method,
        fallback_reason=reason,
        label=label,
        probability=probability,
        days_in_regime=days,
        bars_in_regime=len(labels) - start,
        regime_start=start_ts,
        as_of=pd.DatetimeIndex(frame.index)[-1].to_pydatetime(),
        n=len(frame),
        n_states=n_states,
        states=states,
        adx=adx,
        vol_percentile=vol_percentile,
    )
