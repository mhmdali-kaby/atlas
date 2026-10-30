"""Derivatives & Sentiment features (brief §7.5 inputs, ATL-134).

Pure functions over frames read from Postgres (``load_frames``), plus the basis from ATL-125's Redis
market snapshot. The analyst model reads these numbers; it never computes them. Settings live in
``agents.derivatives`` (config/defaults.yaml).

Definitions:

* **Funding** (``funding_rates``, settled rates): ``current`` = newest rate at or before ``as_of``;
  ``annualised`` = current x funding periods per year, the period being the median gap between the
  stored rates in the longest z window (``default_interval_hours`` with fewer than two rates);
  ``z_<N>d`` = (current - mean) / std (ddof 1) of the rates with ``as_of - N days < ts <= as_of``,
  newest included, null below ``min_funding_obs`` rates or with zero spread; ``sign_flips_<N>d`` =
  sign changes between consecutive non-zero rates in the last N days.
* **Open interest** (``open_interest``: the instrument's own rows, else its base currency's rubik
  series): for each window of N hours, the newest OI row vs the newest row at least N hours older
  (and no more than N x ``window_stretch`` older), as %; the price change is measured between the
  same two times from closed-candle closes. **Quadrant**: price up + OI up = new_longs, price up +
  OI down = short_covering, price down + OI up = new_shorts, price down + OI down =
  long_liquidation; ``flat`` when |price %| < ``price_dead_band_pct`` or |OI %| <
  ``oi_dead_band_pct``.
* **Long/short ratio** (``long_short_ratio`` of the base currency at ``long_short_period``): newest
  ratio and its change vs the row ``long_short_change_hours`` earlier (absolute and %).
* **Taker flow** (``taker_volume`` at ``taker_period``): sum(buy) / sum(sell) of the rows with
  ``as_of - N hours < ts <= as_of``.
* **Basis**: (mark - index) / index x 10 000 bps, mark from the ``mark-price`` channel of the
  ``atlas:md`` snapshot only while that channel is fresh (``market_ws.market_data_max_age_seconds``),
  else null with reason ``stale``. The index is the snapshot's ``index`` field (``market/index-tickers``,
  stored every 5 min by trading_core's derivatives poller) unless the caller passes ``index_px``. No
  stored index gives reason ``index_unavailable``; an index older than
  ``market_data_max_age_seconds`` x ``index_max_age_multiplier`` gives reason ``stale``.

**Unreliable readings.** Demo data can be abnormal. Zero open interest in the OI window, zero candle
volume over the longest OI window, or zero taker volume in a taker window sets ``unreliable=true``
with a reason under ``unreliable_reasons``, and the affected numbers (quadrants, OI changes, taker
ratios) are null rather than computed on it.

A value that cannot be computed is null and ``missing[<name>]`` says why; it is never 0.

Replay: ``load_frames`` reads rows with ``ts <= as_of`` and candles closed by ``as_of`` only.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import pandas as pd
from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_shared.db import FundingRate, LongShortRatio, OpenInterest, TakerVolume
from atlas_shared.defaults import DEFAULTS
from atlas_shared.market_data import INDEX as INDEX_CHANNEL
from atlas_shared.market_data import MarketSnapshot
from atlas_shared.market_data import max_age_seconds as md_max_age_seconds
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

Quadrant = Literal["new_longs", "short_covering", "new_shorts", "long_liquidation", "flat"]
BasisReason = Literal["stale", "no_mark", "index_unavailable", "bad_index"]

MARK_CHANNEL = "mark-price"
PRICE_BAR = "5m"


def derivatives_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["derivatives"]
    return cfg


# -- output models -----------------------------------------------------------------------------------


class FundingFeatures(BaseModel):
    current: float | None = None
    annualised: float | None = None
    interval_hours: float | None = None
    z: dict[str, float | None] = Field(default_factory=dict)  # "7d" / "30d"
    sign_flips: int | None = None
    sign_flip_days: int
    as_of: datetime | None = None  # ts of the newest rate used
    missing: dict[str, str] = Field(default_factory=dict)


class OiWindow(BaseModel):
    hours: int
    oi_change_pct: float | None = None
    price_change_pct: float | None = None
    quadrant: Quadrant | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    missing: str | None = None


class OpenInterestFeatures(BaseModel):
    series: str | None = None  # the open_interest inst_id read (instId or bare ccy)
    oi: float | None = None
    as_of: datetime | None = None
    windows: list[OiWindow] = Field(default_factory=list)
    unreliable: bool = False
    unreliable_reasons: dict[str, str] = Field(default_factory=dict)
    missing: dict[str, str] = Field(default_factory=dict)


class LongShortFeatures(BaseModel):
    ratio: float | None = None
    change_24h: float | None = None
    change_24h_pct: float | None = None
    as_of: datetime | None = None
    missing: dict[str, str] = Field(default_factory=dict)


class TakerWindow(BaseModel):
    hours: int
    buy_sell_ratio: float | None = None
    rows: int = 0
    missing: str | None = None


class TakerFeatures(BaseModel):
    windows: list[TakerWindow] = Field(default_factory=list)
    as_of: datetime | None = None
    unreliable: bool = False
    unreliable_reasons: dict[str, str] = Field(default_factory=dict)


class BasisFeatures(BaseModel):
    basis_bps: float | None = None
    mark_px: Decimal | None = None
    index_px: Decimal | None = None
    reason: BasisReason | None = None
    as_of: datetime | None = None  # the mark-price channel's last message
    index_as_of: datetime | None = None  # OKX ts of the index price used


class DerivativesFeatures(BaseModel):
    inst_id: str
    ccy: str
    funding: FundingFeatures
    open_interest: OpenInterestFeatures
    long_short: LongShortFeatures
    taker: TakerFeatures
    basis: BasisFeatures
    unreliable: bool
    unreliable_reasons: dict[str, str]
    data_as_of: datetime


# -- small helpers -----------------------------------------------------------------------------------


def _finite(x: float | None) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return float(x)


def _upto(series: pd.Series[float], at: datetime) -> pd.Series[float]:
    return series[series.index <= at]


def _since(series: pd.Series[float], start: datetime, at: datetime) -> pd.Series[float]:
    return series[(series.index > start) & (series.index <= at)]


def _pct(new: float, old: float) -> float | None:
    if old == 0:
        return None
    return _finite((new / old - 1.0) * 100.0)


def _value_at(series: pd.Series[float], at: datetime) -> float | None:
    upto = _upto(series, at)
    return None if upto.empty else float(upto.iloc[-1])


def _reference(
    series: pd.Series[float], newest_ts: datetime, hours: int, stretch: float
) -> tuple[datetime, float] | None:
    """Newest row at least ``hours`` before ``newest_ts`` and no more than hours x stretch before."""
    target = newest_ts - timedelta(hours=hours)
    floor = newest_ts - timedelta(hours=hours * stretch)
    older = series[(series.index <= target) & (series.index >= floor)]
    if older.empty:
        return None
    return older.index[-1].to_pydatetime(), float(older.iloc[-1])


def _sign(x: float) -> int:
    return 1 if x > 0 else -1


# -- funding -----------------------------------------------------------------------------------------


def count_sign_flips(rates: Sequence[float]) -> int:
    """Sign changes between consecutive non-zero rates (zeros carry no sign and are skipped)."""
    signs = [_sign(r) for r in rates if r != 0]
    return sum(1 for a, b in itertools.pairwise(signs) if a != b)


def funding_features(
    funding: pd.Series[float], as_of: datetime, cfg: Mapping[str, Any] | None = None
) -> FundingFeatures:
    """``funding``: settled funding rates indexed by ts (UTC), ascending."""
    c = cfg or derivatives_config()
    z_days = [int(d) for d in c["funding_z_days"]]
    flip_days = int(c["sign_flip_days"])
    min_obs = int(c["min_funding_obs"])
    out = FundingFeatures(sign_flip_days=flip_days)
    upto = _upto(funding.sort_index(), as_of)
    if upto.empty:
        reason = "no funding rates stored"
        out.missing.update(current=reason, annualised=reason, sign_flips=reason)
        out.missing.update({f"z_{d}d": reason for d in z_days})
        out.z = {f"{d}d": None for d in z_days}
        return out

    current = float(upto.iloc[-1])
    out.current = current
    out.as_of = upto.index[-1].to_pydatetime()

    longest = _since(upto, as_of - timedelta(days=max(z_days)), as_of)
    stamps = [pd.Timestamp(ts).timestamp() for ts in longest.index]
    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    if gaps:
        out.interval_hours = float(statistics.median(gaps) / 3600.0)
    else:
        out.interval_hours = float(c["default_interval_hours"])
        out.missing["interval_hours"] = (
            f"fewer than two rates in {max(z_days)}d; default {c['default_interval_hours']}h used"
        )
    if out.interval_hours > 0:
        out.annualised = _finite(current * (24.0 / out.interval_hours) * 365.0)

    for d in z_days:
        key = f"{d}d"
        window = _since(upto, as_of - timedelta(days=d), as_of)
        if len(window) < min_obs:
            out.z[key] = None
            out.missing[f"z_{key}"] = f"{len(window)} funding rates in {d}d < {min_obs}"
            continue
        std = float(window.std(ddof=1))
        if std == 0 or not math.isfinite(std):
            out.z[key] = None
            out.missing[f"z_{key}"] = f"funding rate is constant over {d}d"
            continue
        out.z[key] = _finite((current - float(window.mean())) / std)

    flips = _since(upto, as_of - timedelta(days=flip_days), as_of)
    out.sign_flips = count_sign_flips([float(x) for x in flips.to_numpy()])
    return out


# -- open interest + quadrant ------------------------------------------------------------------------


def classify_quadrant(
    price_change_pct: float,
    oi_change_pct: float,
    *,
    price_dead_band_pct: float,
    oi_dead_band_pct: float,
) -> Quadrant:
    """OI-price quadrant; ``flat`` when either move sits inside its dead-band."""
    if abs(price_change_pct) < price_dead_band_pct or abs(oi_change_pct) < oi_dead_band_pct:
        return "flat"
    if price_change_pct > 0:
        return "new_longs" if oi_change_pct > 0 else "short_covering"
    return "new_shorts" if oi_change_pct > 0 else "long_liquidation"


def oi_features(
    oi: pd.Series[float],
    price: pd.DataFrame,
    as_of: datetime,
    cfg: Mapping[str, Any] | None = None,
    *,
    series: str | None = None,
) -> OpenInterestFeatures:
    """``oi``: open interest indexed by ts. ``price``: closed candles indexed by **close** time with
    columns ``c`` (close) and ``vol``."""
    c = cfg or derivatives_config()
    hours_list = [int(h) for h in c["oi_windows_hours"]]
    stretch = float(Decimal(str(c["window_stretch"])))
    price_band = float(Decimal(str(c["price_dead_band_pct"])))
    oi_band = float(Decimal(str(c["oi_dead_band_pct"])))
    max_age = timedelta(minutes=int(c["max_age_minutes"]))
    out = OpenInterestFeatures(series=series)
    upto = _upto(oi.sort_index().dropna(), as_of)

    def all_missing(reason: str) -> OpenInterestFeatures:
        out.windows = [OiWindow(hours=h, missing=reason) for h in hours_list]
        out.missing["windows"] = reason
        return out

    if upto.empty:
        out.missing["oi"] = "no open interest stored"
        return all_missing("no open interest stored")
    newest_ts: datetime = upto.index[-1].to_pydatetime()
    out.oi = float(upto.iloc[-1])
    out.as_of = newest_ts
    if as_of - newest_ts > max_age:
        return all_missing(f"stale: newest open interest is older than {c['max_age_minutes']} min")

    span = timedelta(hours=max(hours_list) * stretch)
    in_window = upto[upto.index >= newest_ts - span]
    if (in_window == 0).any():
        out.unreliable = True
        out.unreliable_reasons["zero_open_interest"] = (
            f"open interest is 0 in the last {max(hours_list)}h (demo data); OI changes and "
            "quadrants are not computed on it"
        )
        return all_missing("unreliable: zero open interest")

    closes = price.sort_index()
    vol_window = closes[(closes.index > as_of - timedelta(hours=max(hours_list))) & (closes.index <= as_of)]
    zero_volume = not vol_window.empty and float(vol_window["vol"].sum()) == 0.0
    if zero_volume:
        out.unreliable = True
        out.unreliable_reasons["zero_volume"] = (
            f"candle volume is 0 over the last {max(hours_list)}h (demo data); quadrants are not "
            "computed on it"
        )

    close = closes["c"] if "c" in closes else pd.Series(dtype=float)
    for h in hours_list:
        w = OiWindow(hours=h, to_ts=newest_ts)
        ref = _reference(upto, newest_ts, h, stretch)
        if ref is None:
            w.missing = f"no open interest row {h}h-{h * stretch:g}h before the newest"
            out.windows.append(w)
            continue
        ref_ts, ref_oi = ref
        w.from_ts = ref_ts
        w.oi_change_pct = _pct(out.oi, ref_oi)
        p_new = _value_at(close, newest_ts)
        p_old = _value_at(close, ref_ts)
        if p_new is None or p_old is None:
            w.missing = "no closed candle at the window's start or end"
        else:
            w.price_change_pct = _pct(p_new, p_old)
        if zero_volume:
            w.missing = "unreliable: zero volume"
        elif w.oi_change_pct is not None and w.price_change_pct is not None:
            w.quadrant = classify_quadrant(
                w.price_change_pct,
                w.oi_change_pct,
                price_dead_band_pct=price_band,
                oi_dead_band_pct=oi_band,
            )
        out.windows.append(w)
    return out


# -- long/short + taker ------------------------------------------------------------------------------


def long_short_features(
    ratio: pd.Series[float], as_of: datetime, cfg: Mapping[str, Any] | None = None
) -> LongShortFeatures:
    """``ratio``: rubik long/short account ratio of the base currency indexed by ts."""
    c = cfg or derivatives_config()
    hours = int(c["long_short_change_hours"])
    stretch = float(Decimal(str(c["window_stretch"])))
    out = LongShortFeatures()
    upto = _upto(ratio.sort_index().dropna(), as_of)
    if upto.empty:
        out.missing["ratio"] = "no long/short ratio stored"
        return out
    newest_ts: datetime = upto.index[-1].to_pydatetime()
    if as_of - newest_ts > timedelta(minutes=int(c["max_age_minutes"])):
        out.missing["ratio"] = f"stale: newest ratio is older than {c['max_age_minutes']} min"
        return out
    out.ratio = float(upto.iloc[-1])
    out.as_of = newest_ts
    ref = _reference(upto, newest_ts, hours, stretch)
    if ref is None:
        out.missing["change_24h"] = f"no ratio {hours}h before the newest"
        return out
    out.change_24h = _finite(out.ratio - ref[1])
    out.change_24h_pct = _pct(out.ratio, ref[1])
    return out


def taker_features(
    taker: pd.DataFrame, as_of: datetime, cfg: Mapping[str, Any] | None = None
) -> TakerFeatures:
    """``taker``: rubik taker volume indexed by ts with columns ``buy_vol`` and ``sell_vol``."""
    c = cfg or derivatives_config()
    hours_list = [int(h) for h in c["taker_windows_hours"]]
    out = TakerFeatures()
    frame = taker.sort_index()
    upto = frame[frame.index <= as_of]
    if upto.empty:
        out.windows = [TakerWindow(hours=h, missing="no taker volume stored") for h in hours_list]
        return out
    newest_ts: datetime = upto.index[-1].to_pydatetime()
    out.as_of = newest_ts
    if as_of - newest_ts > timedelta(minutes=int(c["max_age_minutes"])):
        reason = f"stale: newest taker volume is older than {c['max_age_minutes']} min"
        out.windows = [TakerWindow(hours=h, missing=reason) for h in hours_list]
        return out
    for h in hours_list:
        rows = upto[upto.index > as_of - timedelta(hours=h)]
        w = TakerWindow(hours=h, rows=len(rows))
        buy = float(rows["buy_vol"].sum())
        sell = float(rows["sell_vol"].sum())
        if buy == 0 or sell == 0:
            out.unreliable = True
            out.unreliable_reasons["zero_taker_volume"] = (
                f"taker {'buy' if buy == 0 else 'sell'} volume is 0 over {h}h (demo data); the "
                "buy/sell ratio is not computed on it"
            )
            w.missing = "unreliable: zero taker volume"
        else:
            w.buy_sell_ratio = _finite(buy / sell)
        out.windows.append(w)
    return out


# -- basis -------------------------------------------------------------------------------------------


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def index_max_age_seconds(cfg: Mapping[str, Any] | None = None) -> float:
    """How old the stored index may be: the market-data max age x ``index_max_age_multiplier``."""
    c = cfg or derivatives_config()
    return md_max_age_seconds() * float(Decimal(str(c["index_max_age_multiplier"])))


def basis_features(
    snapshot: MarketSnapshot | None,
    index_px: Decimal | None,
    now: datetime,
    *,
    index_at: datetime | None = None,
    max_age_seconds: float | None = None,
    index_max_age: float | None = None,
) -> BasisFeatures:
    """Basis in bps from the snapshot's mark price while the mark-price channel is fresh.

    ``index_px``/``index_at`` override the snapshot's stored index (``index``/``index_ms``); with
    ``index_px`` None the stored one is used. An index older than ``index_max_age`` seconds
    (default :func:`index_max_age_seconds`) is stale.
    """
    if index_px is None and snapshot is not None and snapshot.index is not None:
        index_px = _decimal(snapshot.index.get("idxPx"))
        index_at = snapshot.channel_at.get(INDEX_CHANNEL) if index_px is not None else None
    if snapshot is None or snapshot.channel_stale(MARK_CHANNEL, now, max_age_seconds):
        at = None if snapshot is None else snapshot.channel_at.get(MARK_CHANNEL)
        return BasisFeatures(reason="stale", as_of=at, index_px=index_px, index_as_of=index_at)
    at = snapshot.channel_at.get(MARK_CHANNEL)
    mark = _decimal((snapshot.mark or {}).get("markPx"))
    if mark is None:
        return BasisFeatures(reason="no_mark", as_of=at, index_px=index_px, index_as_of=index_at)
    if index_px is None:
        return BasisFeatures(reason="index_unavailable", mark_px=mark, as_of=at)
    limit = index_max_age_seconds() if index_max_age is None else index_max_age
    if index_at is not None and (now - index_at).total_seconds() > limit:
        return BasisFeatures(reason="stale", mark_px=mark, index_px=index_px, as_of=at, index_as_of=index_at)
    if index_px <= 0:
        return BasisFeatures(
            reason="bad_index", mark_px=mark, index_px=index_px, as_of=at, index_as_of=index_at
        )
    bps = (mark - index_px) / index_px * Decimal(10000)
    return BasisFeatures(
        basis_bps=_finite(float(bps)), mark_px=mark, index_px=index_px, as_of=at, index_as_of=index_at
    )


# -- all together ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivativesFrames:
    """What ``load_frames`` reads; build one by hand in tests."""

    funding: pd.Series[float]
    oi: pd.Series[float]
    oi_series: str | None
    price: pd.DataFrame  # indexed by candle close time; columns c, vol
    long_short: pd.Series[float]
    taker: pd.DataFrame  # columns buy_vol, sell_vol


def base_ccy(inst_id: str) -> str:
    return inst_id.split("-", 1)[0].upper()


def compute_features(
    inst_id: str,
    frames: DerivativesFrames,
    as_of: datetime,
    *,
    snapshot: MarketSnapshot | None = None,
    index_px: Decimal | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> DerivativesFeatures:
    c = cfg or derivatives_config()
    oi = oi_features(frames.oi, frames.price, as_of, c, series=frames.oi_series)
    taker = taker_features(frames.taker, as_of, c)
    reasons = {**oi.unreliable_reasons, **taker.unreliable_reasons}
    return DerivativesFeatures(
        inst_id=inst_id,
        ccy=base_ccy(inst_id),
        funding=funding_features(frames.funding, as_of, c),
        open_interest=oi,
        long_short=long_short_features(frames.long_short, as_of, c),
        taker=taker,
        basis=basis_features(snapshot, index_px, as_of, index_max_age=index_max_age_seconds(c)),
        unreliable=bool(reasons),
        unreliable_reasons=reasons,
        data_as_of=as_of,
    )


# -- loading from Postgres ---------------------------------------------------------------------------


def _utc_index(stamps: Sequence[datetime]) -> pd.DatetimeIndex:
    if not stamps:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.DatetimeIndex(list(stamps)).tz_convert("UTC")


def _series(rows: Sequence[tuple[datetime, Decimal | None]]) -> pd.Series[float]:
    data = [(ts, float(v)) for ts, v in rows if v is not None]
    idx = _utc_index([ts for ts, _ in data])
    return pd.Series([v for _, v in data], index=idx, dtype=float).sort_index()


def load_frames(
    session: Session, env: str, inst_id: str, as_of: datetime, cfg: Mapping[str, Any] | None = None
) -> DerivativesFrames:
    """Every row with ``ts <= as_of`` the features need; candles closed by ``as_of`` only."""
    c = cfg or derivatives_config()
    stretch = float(Decimal(str(c["window_stretch"])))
    ccy = base_ccy(inst_id)
    funding_from = as_of - timedelta(days=max(int(d) for d in c["funding_z_days"]))
    oi_from = (
        as_of
        - timedelta(hours=max(int(h) for h in c["oi_windows_hours"]) * stretch)
        - timedelta(minutes=int(c["max_age_minutes"]))
    )
    ls_from = (
        as_of
        - timedelta(hours=int(c["long_short_change_hours"]) * stretch)
        - timedelta(minutes=int(c["max_age_minutes"]))
    )
    taker_from = as_of - timedelta(hours=max(int(h) for h in c["taker_windows_hours"]))

    funding = _series(
        session.execute(
            select(FundingRate.ts, FundingRate.funding_rate)
            .where(
                FundingRate.env == env,
                FundingRate.inst_id == inst_id,
                FundingRate.ts > funding_from,
                FundingRate.ts <= as_of,
            )
            .order_by(FundingRate.ts)
        ).all()
    )

    oi_series: str | None = None
    oi = _series([])
    for key in (inst_id, ccy):
        rows = session.execute(
            select(OpenInterest.ts, OpenInterest.oi_usd, OpenInterest.oi)
            .where(
                OpenInterest.env == env,
                OpenInterest.inst_id == key,
                OpenInterest.ts >= oi_from,
                OpenInterest.ts <= as_of,
            )
            .order_by(OpenInterest.ts)
        ).all()
        if rows:
            # One unit per series: oi_usd when the series has it, else contracts.
            use_usd = all(r[1] is not None for r in rows)
            oi = _series([(r[0], r[1] if use_usd else r[2]) for r in rows])
            oi_series = key
            break

    step = bar_delta(PRICE_BAR)
    candles = load_candles(session, inst_id, PRICE_BAR, oi_from - step, as_of, as_of=as_of, env=env).df
    if candles.empty:
        price = pd.DataFrame({"c": [], "vol": []}, index=pd.DatetimeIndex([], tz="UTC"))
    else:
        price = pd.DataFrame(
            {"c": candles["c"].to_numpy(), "vol": candles["vol"].to_numpy()},
            index=candles.index + step,  # load_candles indexes by open time; features use close time
        )

    ls_period = str(c["long_short_period"])
    long_short = _series(
        session.execute(
            select(LongShortRatio.ts, LongShortRatio.ratio)
            .where(
                LongShortRatio.env == env,
                LongShortRatio.ccy == ccy,
                LongShortRatio.period == ls_period,
                LongShortRatio.ts >= ls_from,
                LongShortRatio.ts <= as_of,
            )
            .order_by(LongShortRatio.ts)
        ).all()
    )

    taker_rows = session.execute(
        select(TakerVolume.ts, TakerVolume.buy_vol, TakerVolume.sell_vol)
        .where(
            TakerVolume.env == env,
            TakerVolume.ccy == ccy,
            TakerVolume.period == str(c["taker_period"]),
            TakerVolume.ts > taker_from - timedelta(minutes=int(c["max_age_minutes"])),
            TakerVolume.ts <= as_of,
        )
        .order_by(TakerVolume.ts)
    ).all()
    taker = pd.DataFrame(
        {"buy_vol": [float(r[1]) for r in taker_rows], "sell_vol": [float(r[2]) for r in taker_rows]},
        index=_utc_index([r[0] for r in taker_rows]),
    )
    return DerivativesFrames(
        funding=funding, oi=oi, oi_series=oi_series, price=price, long_short=long_short, taker=taker
    )


__all__ = [
    "BasisFeatures",
    "DerivativesFeatures",
    "DerivativesFrames",
    "FundingFeatures",
    "LongShortFeatures",
    "OiWindow",
    "OpenInterestFeatures",
    "Quadrant",
    "TakerFeatures",
    "TakerWindow",
    "basis_features",
    "classify_quadrant",
    "compute_features",
    "count_sign_flips",
    "derivatives_config",
    "funding_features",
    "index_max_age_seconds",
    "load_frames",
    "long_short_features",
    "oi_features",
    "taker_features",
]
