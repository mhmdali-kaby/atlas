"""Market Scout step 2: the per-instrument feature table (brief §7.1, decision #247 (2), ATL-104).

Built by code for every instrument the universe filter kept, stored as a ``research_reports`` row
(agent ``market_scout``, kind ``feature_table``, ``valid_until`` = as_of + ``valid_minutes``). The
model reads it; it never computes these numbers.

Definitions (``agents.scout.features`` in config/defaults.yaml; pinned by the golden test):

* ``returns_pct[Nd]``: close-to-close simple return over the last N closed 1D bars, in percent
  (atlas_quant ``compute_stats(...).simple_returns.total`` on the last N+1 bars).
* ``atr_pct_1h`` / ``atr_pct_1d``: Wilder ATR(14) as % of the last close (``compute_stats``
  ``atr_pct``) over the last ``atr_bars`` bars of that size.
* ``vol_ratio_24h_30d``: realised vol of the last 24 1H log returns / that of the last 720
  (``compute_stats`` ``realised_vol``; the annualisation cancels).
* ``volume_z_30d``: (latest closed day's quote volume - mean of the 30 days before it) / their std
  (ddof 1). Quote volume is OKX volCcyQuote, else volCcy x close.
* ``ma_distance_pct[Nd]``: (last close / mean of the last N closes - 1) x 100.
* ``breakout_high_20d`` / ``breakout_low_20d``: last close above the highest high / below the lowest
  low of the 20 bars before it.
* ``funding_rate``: newest funding rate; ``funding_z_7d``: (newest - mean) / std (ddof 1) of the
  rates in the last 7 days, newest included.
* ``oi_change_24h_pct``: newest OI (oi_usd, else contracts) vs the newest row at least 24h older.
  The instrument's own series is used, else the base currency's rubik series (``oi_series`` says
  which).
* ``long_short_ratio``: newest long/short account ratio of the base currency (``long_short_period``),
  null when older than ``long_short_max_age_hours``.
* ``corr_btc_30d`` / ``corr_spy_30d``: Pearson correlation of the last 30 overlapping daily log
  returns with the reference (BTC, then SPY or US100 in demo; the table names which was used).
* ``news_count_24h`` / ``news_max_importance_24h``: news items linked to the instrument published in
  the last 24h.
* ``catalysts``: economic events (shared by every instrument, at table level) and the instrument's
  own earnings in the next 48h.
* ``regime_*``: atlas_quant ``detect_regime`` on the Historian's series (agents.history.detect_regime).

A feature that cannot be computed is null and ``missing[<feature>]`` says why; it is never 0.

Replay: every read is bounded by ``ctx.now()`` (the replay cutoff): candles had closed by it
(atlas_quant ``load_candles``), funding / OI / long-short rows have ``ts <= as_of``, news has
``published_at <= as_of`` and ``fetched_at <= as_of``, calendar rows were stored by it
(``created_at <= as_of``), and instruments were listed by it. Nothing after as_of reaches a feature.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

import numpy as np
import pandas as pd
from atlas_quant import compute_stats, detect_regime, load_candles
from atlas_quant.grid import bar_delta
from atlas_shared.db import (
    EarningsEvent,
    EconomicEvent,
    FundingRate,
    Instrument,
    LongShortRatio,
    NewsItem,
    OpenInterest,
    ResearchReport,
)
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.scout.universe import Kept, Rejected, UniverseResult, scout_config
from atlas_agents.tools.account import pearson
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.market import listed

AGENT: Final = "market_scout"
KIND: Final = "feature_table"
_STOCK_CLASSES: Final = frozenset({"stock", "etf", "index"})


def features_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = scout_config(defaults)["features"]
    return config


# -- models ------------------------------------------------------------------------------------------


class Catalyst(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["economic", "earnings"]
    name: str
    at: datetime | None  # scheduled time; earnings carry only a date
    date: date
    importance: int | None  # economic events only
    country: str | None = None
    source: str


class InstrumentFeatures(BaseModel):
    inst_id: str
    asset_class: str
    market: str | None = None
    volume_24h_usd: Decimal | None = None
    returns_pct: dict[str, float | None]
    atr_pct_1h: float | None
    atr_pct_1d: float | None
    vol_ratio_24h_30d: float | None
    volume_z_30d: float | None
    ma_distance_pct: dict[str, float | None]
    breakout_high_20d: bool | None
    breakout_low_20d: bool | None
    funding_rate: float | None
    funding_z_7d: float | None
    oi_change_24h_pct: float | None
    oi_series: str | None
    long_short_ratio: float | None
    corr_btc_30d: float | None
    corr_spy_30d: float | None
    news_count_24h: int
    news_max_importance_24h: int | None
    catalysts: list[Catalyst]  # this instrument's own (earnings); shared ones are on the table
    catalyst_count_48h: int  # shared + own
    catalyst_max_importance_48h: int | None
    regime_label: str | None
    regime_probability: float | None
    regime_method: str | None
    data_as_of: datetime | None
    missing: dict[str, str]
    quality_notes: list[str]


class FeatureTable(BaseModel):
    env: str
    as_of: datetime
    valid_until: datetime
    settings_version: int | None
    btc_reference: str | None
    spy_reference: str | None
    shared_catalysts: list[Catalyst]
    rows: list[InstrumentFeatures]
    rejected: list[Rejected]
    notes: list[str]


# -- pure computation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureInputs:
    """Everything one instrument's features are computed from, already bounded by as_of."""

    inst_id: str
    asset_class: str
    daily: pd.DataFrame  # closed 1D bars, load_candles shape
    hourly: pd.DataFrame  # closed 1H bars
    regime_frame: pd.DataFrame | None = None  # None: use ``daily``
    funding: Sequence[tuple[datetime, float]] | None = None  # None: the instrument has no funding
    oi: Sequence[tuple[datetime, float]] = ()
    oi_series: str | None = None
    long_short: tuple[datetime, float] | None = None
    news: Sequence[tuple[datetime, int]] = ()  # (published_at, importance)
    catalysts: Sequence[Catalyst] = ()  # own
    shared_catalysts: Sequence[Catalyst] = ()
    btc_daily: pd.DataFrame | None = None
    spy_daily: pd.DataFrame | None = None
    quality_notes: Sequence[str] = field(default_factory=tuple)
    no_funding_reason: str | None = None
    no_oi_reason: str | None = None
    no_long_short_reason: str | None = None


def _finite(x: float | None) -> float | None:
    if x is None:
        return None
    v = float(x)
    return None if math.isnan(v) or math.isinf(v) else v


def quote_volume_series(df: pd.DataFrame) -> pd.Series[float]:
    """Per-bar quote volume: volCcyQuote, else volCcy x close (the okx_market tools' rule)."""
    if "vol_ccy_quote" not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    fallback = df["vol_ccy"] * df["c"] if "vol_ccy" in df.columns else np.nan
    return df["vol_ccy_quote"].fillna(fallback).astype("float64")


def _log_returns(df: pd.DataFrame) -> pd.Series[float]:
    c = df["c"].astype("float64")
    return pd.Series(np.log((c / c.shift(1)).to_numpy()), index=c.index, dtype="float64").iloc[1:]


def _corr(
    df: pd.DataFrame, ref: pd.DataFrame | None, days: int, min_obs: int
) -> tuple[float | None, str | None]:
    if ref is None or ref.empty:
        return None, "no reference instrument with 1D bars"
    joined = pd.concat([_log_returns(df), _log_returns(ref)], axis=1, join="inner").dropna().iloc[-days:]
    if len(joined) < min_obs:
        return None, f"{len(joined)} overlapping daily returns < {min_obs}"
    value = pearson(joined.iloc[:, 0].tolist(), joined.iloc[:, 1].tolist())
    if value is None:
        return None, "a return series is constant"
    return _finite(value), None


def compute_features(inputs: FeatureInputs, as_of: datetime) -> InstrumentFeatures:
    """The feature row of one instrument. Pure: no I/O, no clock."""
    cfg = features_config()
    missing: dict[str, str] = {}
    daily = inputs.daily.sort_index()
    hourly = inputs.hourly.sort_index()
    nd = len(daily)
    stamps: list[datetime] = []
    if nd:
        stamps.append((daily.index[-1] + bar_delta("1D")).to_pydatetime())
    if len(hourly):
        stamps.append((hourly.index[-1] + bar_delta("1H")).to_pydatetime())

    # Returns.
    returns: dict[str, float | None] = {}
    for n in cfg["return_days"]:
        key = f"{int(n)}d"
        if nd < int(n) + 1:
            returns[key] = None
            missing[f"returns_pct.{key}"] = f"{nd} closed 1D bars < {int(n) + 1}"
            continue
        total = compute_stats(daily.iloc[-(int(n) + 1) :], "1D", inputs.asset_class).simple_returns.total
        returns[key] = _finite(total * 100.0) if total is not None else None

    # ATR %.
    atr_bars = int(cfg["atr_bars"])

    def atr_pct(df: pd.DataFrame, bar: str, name: str) -> float | None:
        if df.empty:
            missing[name] = f"no closed {bar} bars"
            return None
        value = compute_stats(df.iloc[-atr_bars:], bar, inputs.asset_class).atr_pct
        if value is None:
            missing[name] = f"{min(len(df), atr_bars)} closed {bar} bars < ATR window"
        return _finite(value)

    atr_1h = atr_pct(hourly, "1H", "atr_pct_1h")
    atr_1d = atr_pct(daily, "1D", "atr_pct_1d")

    # Volatility ratio.
    short_n = int(cfg["vol_ratio"]["short_returns"])
    long_n = int(cfg["vol_ratio"]["long_returns"])
    vol_ratio: float | None = None
    if len(hourly) < long_n + 1:
        missing["vol_ratio_24h_30d"] = f"{len(hourly)} closed 1H bars < {long_n + 1}"
    else:
        short = compute_stats(hourly.iloc[-(short_n + 1) :], "1H", inputs.asset_class).realised_vol
        long = compute_stats(hourly.iloc[-(long_n + 1) :], "1H", inputs.asset_class).realised_vol
        if short is None or long is None or long == 0:
            missing["vol_ratio_24h_30d"] = "realised vol undefined (flat closes)"
        else:
            vol_ratio = _finite(short / long)

    # Volume z-score.
    z_days = int(cfg["volume_z_days"])
    volume_z: float | None = None
    qv = quote_volume_series(daily).dropna()
    if len(qv) < z_days + 1:
        missing["volume_z_30d"] = f"{len(qv)} days with quote volume < {z_days + 1}"
    else:
        prior = qv.iloc[-(z_days + 1) : -1]
        sd = float(prior.std(ddof=1))
        if sd == 0 or math.isnan(sd):
            missing["volume_z_30d"] = "volume is constant over the window"
        else:
            volume_z = _finite((float(qv.iloc[-1]) - float(prior.mean())) / sd)

    # Moving-average distances.
    ma: dict[str, float | None] = {}
    for n in cfg["ma_days"]:
        key = f"{int(n)}d"
        if nd < int(n):
            ma[key] = None
            missing[f"ma_distance_pct.{key}"] = f"{nd} closed 1D bars < {int(n)}"
            continue
        mean = float(daily["c"].iloc[-int(n) :].mean())
        ma[key] = _finite((float(daily["c"].iloc[-1]) / mean - 1.0) * 100.0)

    # Breakouts.
    b_days = int(cfg["breakout_days"])
    high_flag: bool | None = None
    low_flag: bool | None = None
    if nd < b_days + 1:
        missing["breakout_high_20d"] = missing["breakout_low_20d"] = f"{nd} closed 1D bars < {b_days + 1}"
    else:
        prior_bars = daily.iloc[-(b_days + 1) : -1]
        last_close = float(daily["c"].iloc[-1])
        high_flag = bool(last_close > float(prior_bars["h"].max()))
        low_flag = bool(last_close < float(prior_bars["l"].min()))

    # Funding.
    funding_rate: float | None = None
    funding_z: float | None = None
    if inputs.funding is None:
        reason = inputs.no_funding_reason or "instrument has no funding (not a perpetual)"
        missing["funding_rate"] = missing["funding_z_7d"] = reason
    elif not inputs.funding:
        reason = inputs.no_funding_reason or f"no funding history stored for {inputs.inst_id}"
        missing["funding_rate"] = missing["funding_z_7d"] = reason
    else:
        rows = sorted(inputs.funding)
        latest_ts, latest = rows[-1]
        funding_rate = _finite(latest)
        stamps.append(latest_ts)
        start = as_of - timedelta(days=int(cfg["funding_z_days"]))
        window = [r for t, r in rows if t > start]
        if len(window) < int(cfg["min_funding_obs"]):
            missing["funding_z_7d"] = (
                f"{len(window)} funding rates in {cfg['funding_z_days']}d < {cfg['min_funding_obs']}"
            )
        else:
            sd = float(np.std(window, ddof=1))
            if sd == 0:
                missing["funding_z_7d"] = "funding rate is constant over the window"
            else:
                funding_z = _finite((latest - float(np.mean(window))) / sd)

    # Open interest.
    oi_change: float | None = None
    if not inputs.oi:
        missing["oi_change_24h_pct"] = inputs.no_oi_reason or f"no open interest stored for {inputs.inst_id}"
    else:
        oi_rows = sorted(inputs.oi)
        latest_ts, latest_oi = oi_rows[-1]
        stamps.append(latest_ts)
        older = [v for t, v in oi_rows if t <= latest_ts - timedelta(hours=int(cfg["oi_hours"]))]
        if not older:
            missing["oi_change_24h_pct"] = f"no open interest row {cfg['oi_hours']}h before the newest"
        elif older[-1] == 0:
            missing["oi_change_24h_pct"] = "open interest 24h earlier is 0"
        else:
            oi_change = _finite((latest_oi / older[-1] - 1.0) * 100.0)

    # Long/short ratio.
    ls_ratio: float | None = None
    if inputs.long_short is None:
        missing["long_short_ratio"] = inputs.no_long_short_reason or "no long/short ratio stored"
    else:
        ls_ts, ls_value = inputs.long_short
        if ls_ts < as_of - timedelta(hours=int(cfg["long_short_max_age_hours"])):
            missing["long_short_ratio"] = (
                f"newest ratio {ls_ts.isoformat()} is older than {cfg['long_short_max_age_hours']}h"
            )
        else:
            ls_ratio = _finite(ls_value)
            stamps.append(ls_ts)

    # Correlations.
    corr_days, min_obs = int(cfg["corr_days"]), int(cfg["min_corr_obs"])
    corr_btc, why = _corr(daily, inputs.btc_daily, corr_days, min_obs)
    if why:
        missing["corr_btc_30d"] = why
    corr_spy, why = _corr(daily, inputs.spy_daily, corr_days, min_obs)
    if why:
        missing["corr_spy_30d"] = why

    # News.
    news_since = as_of - timedelta(hours=int(cfg["news_hours"]))
    news = [(t, imp) for t, imp in inputs.news if news_since < t <= as_of]
    news_max = max((imp for _t, imp in news), default=None)
    if news_max is None:
        missing["news_max_importance_24h"] = f"no news linked in the last {cfg['news_hours']}h"

    # Catalysts.
    all_catalysts = [*inputs.shared_catalysts, *inputs.catalysts]
    importances = [c.importance for c in all_catalysts if c.importance is not None]
    cat_max = max(importances, default=None)
    if cat_max is None:
        missing["catalyst_max_importance_48h"] = "no scheduled catalyst with an importance in the window"

    # Regime.
    regime_label: str | None = None
    regime_p: float | None = None
    regime_method: str | None = None
    frame = inputs.regime_frame if inputs.regime_frame is not None else daily
    if frame.empty:
        missing["regime_label"] = "no closed bars for the regime series"
    else:
        try:
            regime = detect_regime(frame)
        except (ValueError, np.linalg.LinAlgError) as exc:
            missing["regime_label"] = f"detect_regime failed: {exc}"
        else:
            regime_label = regime.label
            regime_method = regime.method
            p = _finite(regime.probability)
            regime_p = None if p is None else min(max(p, 0.0), 1.0)
    if regime_label is None:
        missing.setdefault("regime_label", "regime unavailable")

    return InstrumentFeatures(
        inst_id=inputs.inst_id,
        asset_class=inputs.asset_class,
        returns_pct=returns,
        atr_pct_1h=atr_1h,
        atr_pct_1d=atr_1d,
        vol_ratio_24h_30d=vol_ratio,
        volume_z_30d=volume_z,
        ma_distance_pct=ma,
        breakout_high_20d=high_flag,
        breakout_low_20d=low_flag,
        funding_rate=funding_rate,
        funding_z_7d=funding_z,
        oi_change_24h_pct=oi_change,
        oi_series=inputs.oi_series if inputs.oi else None,
        long_short_ratio=ls_ratio,
        corr_btc_30d=corr_btc,
        corr_spy_30d=corr_spy,
        news_count_24h=len(news),
        news_max_importance_24h=news_max,
        catalysts=list(inputs.catalysts),
        catalyst_count_48h=len(all_catalysts),
        catalyst_max_importance_48h=cat_max,
        regime_label=regime_label,
        regime_probability=regime_p,
        regime_method=regime_method,
        data_as_of=min(max(stamps), as_of) if stamps else None,
        missing=missing,
        quality_notes=list(inputs.quality_notes),
    )


# -- loading from Postgres ---------------------------------------------------------------------------


def _load_back(
    session: Session, ctx: ToolContext, inst_id: str, bar: str, bars: int
) -> tuple[pd.DataFrame, list[str]]:
    as_of = ctx.now()
    candles = load_candles(
        session, inst_id, bar, as_of - bar_delta(bar) * bars, as_of, as_of=as_of, env=ctx.env
    )
    q = candles.quality
    notes = []
    if q.interior_gap:
        notes.append(f"{bar}: {q.missing_bars} missing bars inside the window")
    if q.duplicate_count:
        notes.append(f"{bar}: {q.duplicate_count} duplicate bars dropped")
    return candles.df, notes


def _float(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def load_shared_catalysts(session: Session, ctx: ToolContext) -> list[Catalyst]:
    cfg = features_config()
    as_of = ctx.now()
    end = as_of + timedelta(hours=int(cfg["catalyst_hours"]))
    countries = [str(c) for c in cfg["calendar_countries"]]
    rows = session.scalars(
        select(EconomicEvent)
        .where(
            EconomicEvent.scheduled_at > as_of,
            EconomicEvent.scheduled_at <= end,
            EconomicEvent.importance >= int(cfg["calendar_min_importance"]),
            EconomicEvent.country.in_(countries),
            EconomicEvent.created_at <= as_of,
        )
        .order_by(EconomicEvent.scheduled_at, EconomicEvent.name)
    ).all()
    return [
        Catalyst(
            kind="economic",
            name=r.name,
            at=r.scheduled_at,
            date=r.scheduled_at.date(),
            importance=r.importance,
            country=r.country,
            source=r.source,
        )
        for r in rows
    ]


def _earnings(session: Session, ctx: ToolContext, symbol: str) -> list[Catalyst]:
    cfg = features_config()
    as_of = ctx.now()
    end = as_of + timedelta(hours=int(cfg["catalyst_hours"]))
    rows = session.scalars(
        select(EarningsEvent)
        .where(
            EarningsEvent.symbol == symbol,
            EarningsEvent.date >= as_of.date(),
            EarningsEvent.date <= end.date(),
            EarningsEvent.created_at <= as_of,
        )
        .order_by(EarningsEvent.date)
    ).all()
    return [
        Catalyst(
            kind="earnings",
            name=f"{symbol} earnings",
            at=None,
            date=r.date,
            importance=None,
            source="earnings_events",
        )
        for r in rows
    ]


def _reference(
    session: Session, ctx: ToolContext, candidates: Sequence[str], bars: int
) -> tuple[str | None, pd.DataFrame | None]:
    known = set(
        session.scalars(
            select(Instrument.inst_id).where(listed(ctx), Instrument.inst_id.in_(list(candidates)))
        ).all()
    )
    for inst_id in candidates:
        if inst_id not in known:
            continue
        df, _ = _load_back(session, ctx, inst_id, "1D", bars)
        if not df.empty:
            return inst_id, df
    return None, None


def load_inputs(
    session: Session,
    ctx: ToolContext,
    inst: Instrument,
    *,
    shared_catalysts: Sequence[Catalyst],
    btc_daily: pd.DataFrame | None,
    spy_daily: pd.DataFrame | None,
) -> FeatureInputs:
    cfg = features_config()
    as_of = ctx.now()
    regime_cfg: Mapping[str, Any] = DEFAULTS["agents"]["history"]["detect_regime"]
    regime_bar, regime_bars = str(regime_cfg["bar"]), int(regime_cfg["lookback_bars"])
    daily_bars = int(cfg["daily_bars"])
    notes: list[str] = []
    if regime_bar == "1D":
        full, n = _load_back(session, ctx, inst.inst_id, "1D", max(daily_bars, regime_bars))
        daily = full.iloc[-daily_bars:]
        regime_frame: pd.DataFrame | None = full.iloc[-regime_bars:]
    else:
        daily, n = _load_back(session, ctx, inst.inst_id, "1D", daily_bars)
        regime_frame, _ = _load_back(session, ctx, inst.inst_id, regime_bar, regime_bars)
    notes += n
    hourly, n = _load_back(session, ctx, inst.inst_id, "1H", int(cfg["hourly_bars"]))
    notes += n

    perp = inst.inst_type.upper() in {"SWAP", "FUTURES"}
    funding: list[tuple[datetime, float]] | None = None
    no_funding: str | None = None
    if inst.inst_type.upper() == "SWAP":
        start = as_of - timedelta(days=int(cfg["funding_z_days"]))
        latest = session.execute(
            select(FundingRate.ts, FundingRate.funding_rate)
            .where(FundingRate.env == ctx.env, FundingRate.inst_id == inst.inst_id, FundingRate.ts <= as_of)
            .order_by(FundingRate.ts.desc())
            .limit(1)
        ).all()
        window = session.execute(
            select(FundingRate.ts, FundingRate.funding_rate).where(
                FundingRate.env == ctx.env,
                FundingRate.inst_id == inst.inst_id,
                FundingRate.ts > start,
                FundingRate.ts <= as_of,
            )
        ).all()
        funding = sorted({(t, float(r)) for t, r in [*latest, *window]})
        if not funding:
            no_funding = f"no funding history stored for {inst.inst_id} by {as_of.isoformat()}"
    else:
        no_funding = f"{inst.inst_type} instruments have no funding rate"

    oi: list[tuple[datetime, float]] = []
    oi_series: str | None = None
    no_oi: str | None = None
    if perp:
        lookback = as_of - timedelta(hours=int(cfg["oi_hours"]) * 3)
        for series in [inst.inst_id, *([inst.base_ccy.upper()] if inst.base_ccy else [])]:
            rows = session.execute(
                select(OpenInterest.ts, OpenInterest.oi_usd, OpenInterest.oi).where(
                    OpenInterest.env == ctx.env,
                    OpenInterest.inst_id == series,
                    OpenInterest.ts > lookback,
                    OpenInterest.ts <= as_of,
                )
            ).all()
            usd = [(t, float(u)) for t, u, _o in rows if u is not None]
            values = usd if len(usd) == len(rows) else [(t, float(o)) for t, _u, o in rows if o is not None]
            if values:
                oi, oi_series = sorted(values), series
                break
        if not oi:
            no_oi = f"no open interest stored for {inst.inst_id} in the last {int(cfg['oi_hours']) * 3}h"
    else:
        no_oi = f"{inst.inst_type} instruments have no open interest"

    long_short: tuple[datetime, float] | None = None
    no_ls: str | None = None
    if inst.base_ccy:
        row = session.execute(
            select(LongShortRatio.ts, LongShortRatio.ratio)
            .where(
                LongShortRatio.env == ctx.env,
                LongShortRatio.ccy == inst.base_ccy.upper(),
                LongShortRatio.period == str(cfg["long_short_period"]),
                LongShortRatio.ts <= as_of,
            )
            .order_by(LongShortRatio.ts.desc())
            .limit(1)
        ).first()
        if row is not None:
            long_short = (row[0], float(row[1]))
        else:
            no_ls = f"no {cfg['long_short_period']} long/short ratio stored for {inst.base_ccy}"
    else:
        no_ls = "instrument has no base currency"

    since = as_of - timedelta(hours=int(cfg["news_hours"]))
    news_rows = session.execute(
        select(NewsItem.published_at, NewsItem.importance).where(
            NewsItem.instruments.overlap([inst.inst_id]),
            NewsItem.published_at > since,
            NewsItem.published_at <= as_of,
            NewsItem.fetched_at <= as_of,
        )
    ).all()

    own: list[Catalyst] = []
    if inst.asset_class in _STOCK_CLASSES and inst.base_ccy:
        own = _earnings(session, ctx, inst.base_ccy.upper())

    return FeatureInputs(
        inst_id=inst.inst_id,
        asset_class=inst.asset_class,
        daily=daily,
        hourly=hourly,
        regime_frame=regime_frame,
        funding=funding,
        oi=oi,
        oi_series=oi_series,
        long_short=long_short,
        news=[(t, int(i)) for t, i in news_rows],
        catalysts=own,
        shared_catalysts=shared_catalysts,
        btc_daily=btc_daily,
        spy_daily=spy_daily,
        quality_notes=notes,
        no_funding_reason=no_funding,
        no_oi_reason=no_oi,
        no_long_short_reason=no_ls,
    )


def build_feature_table(ctx: ToolContext, universe: UniverseResult) -> FeatureTable:
    """Features of every kept instrument at ``ctx.now()``."""
    cfg = features_config()
    as_of = ctx.now()
    if universe.as_of != as_of:
        raise ValueError("universe was filtered at a different as_of")
    refs = cfg["references"]
    daily_bars = int(cfg["daily_bars"])
    rows: list[InstrumentFeatures] = []
    notes: list[str] = []
    with ctx.session_factory() as session:
        shared = load_shared_catalysts(session, ctx)
        btc_id, btc = _reference(session, ctx, [str(r) for r in refs["btc"]], daily_bars)
        spy_id, spy = _reference(session, ctx, [str(r) for r in refs["spy"]], daily_bars)
        if btc_id is None:
            notes.append("no BTC reference with 1D bars; corr_btc_30d is null")
        if spy_id is None:
            notes.append("no SPY/US100 reference with 1D bars; corr_spy_30d is null")
        kept: dict[str, Kept] = {k.inst_id: k for k in universe.kept}
        instruments = session.scalars(
            select(Instrument).where(listed(ctx), Instrument.inst_id.in_(list(kept)))
        ).all()
        by_id = {i.inst_id: i for i in instruments}
        for k in universe.kept:
            inst = by_id.get(k.inst_id)
            if inst is None:
                continue
            inputs = load_inputs(session, ctx, inst, shared_catalysts=shared, btc_daily=btc, spy_daily=spy)
            row = compute_features(inputs, as_of)
            row.market = k.market
            row.volume_24h_usd = k.volume_24h_usd
            if "spread_unknown" in k.notes:
                row.quality_notes.append("spread unknown: Atlas stores no order book in Phase 1")
            if "age_unknown" in k.notes:
                row.quality_notes.append("listing time unknown: the listed > 7 days check was skipped")
            rows.append(row)
    return FeatureTable(
        env=ctx.env,
        as_of=as_of,
        valid_until=as_of + timedelta(minutes=int(cfg["valid_minutes"])),
        settings_version=universe.settings_version,
        btc_reference=btc_id,
        spy_reference=spy_id,
        shared_catalysts=shared,
        rows=rows,
        rejected=universe.rejected,
        notes=notes,
    )


def summary_md(table: FeatureTable) -> str:
    return (
        f"Market Scout feature table as of {table.as_of.isoformat()}: {len(table.rows)} instruments "
        f"kept, {len(table.rejected)} rejected, {len(table.shared_catalysts)} economic catalysts in "
        f"the next {features_config()['catalyst_hours']}h. Valid until {table.valid_until.isoformat()}."
    )


def save_feature_table(session: Session, ctx: ToolContext, table: FeatureTable) -> ResearchReport:
    """Add the table as a research_reports row (flushed, not committed)."""
    stamp = ctx.clock()
    report = ResearchReport(
        env=table.env,
        agent=AGENT,
        inst_id=None,
        kind=KIND,
        content_md=summary_md(table),
        data=table.model_dump(mode="json"),
        confidence=None,
        valid_until=table.valid_until,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(report)
    session.flush()
    return report


__all__ = [
    "AGENT",
    "KIND",
    "Catalyst",
    "FeatureInputs",
    "FeatureTable",
    "InstrumentFeatures",
    "build_feature_table",
    "compute_features",
    "features_config",
    "load_inputs",
    "load_shared_catalysts",
    "quote_volume_series",
    "save_feature_table",
    "summary_md",
]
