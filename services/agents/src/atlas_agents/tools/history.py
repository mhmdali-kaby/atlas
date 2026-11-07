"""The ``history`` tools (brief §6.2, §7.3): candles from Postgres through atlas_quant.

Every tool loads its series with :func:`atlas_quant.load_candles` at ``as_of = ctx.now()`` (the
replay cutoff in replay mode, else the clock), so only bars closed by ``as_of`` reach the statistic,
and calls the matching atlas_quant function. Defaults (bar, lookback) live in
``agents.history.<tool>`` in config/defaults.yaml.

Each result carries the **sample block** the Historian prompt asks for (instrument, bar, period,
number of cases ``n``, the DataQuality summary of every series used, ``reference_data``) and a
``status``:

* ``ok`` — ``result`` holds the atlas_quant result;
* ``no_data`` — no candles (or, for event_study, no events) in the window; ``result`` is null;
* ``insufficient_data`` — candles exist but too few for the statistic; ``note`` says why.

A missing instrument is an answer, not an exception, so the model can say "no data" honestly.
``as_of`` of the envelope is the close time of the newest bar used (``ctx.now()`` with no data).

The statistics are CPU-bound (HMM, GARCH, vectorbt), so each tool body runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import pandas as pd
from atlas_quant import (
    AnalogsResult,
    BacktestResult,
    Candles,
    Costs,
    DataQuality,
    DrawdownProfile,
    EventStudyResult,
    GapRange,
    LeadLagResult,
    RegimeResult,
    RuleSpec,
    SeasonalityResult,
    StatsResult,
    VolForecast,
    backtest_rule,
    compute_stats,
    cross_asset_lead_lag,
    detect_regime,
    drawdown_profile,
    event_study,
    find_analogs,
    load_candles,
    seasonality,
    volatility_forecast,
)
from atlas_quant.grid import bar_delta
from atlas_shared.db import EarningsEvent, EconomicEvent, FundingRate, Instrument
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.tools.catalogue import (
    BacktestRuleInput,
    EventStudyInput,
    FindAnalogsInput,
    HistorySeriesInput,
    LeadLagInput,
    SeasonalityInput,
    StatsInput,
    VolForecastInput,
)
from atlas_agents.tools.framework import ToolContext, ToolError, ToolResult, atlas_tool

SOURCE = "atlas.candles"
Status = Literal["ok", "no_data", "insufficient_data"]
_YEAR = timedelta(days=365)


def history_config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["agents"]["history"]
    return cfg


def min_n() -> int:
    return int(history_config()["verdict"]["min_n"])


# -- the sample block ----------------------------------------------------------------------------------


class QualitySummary(BaseModel):
    """DataQuality of one loaded series, without the window echo."""

    model_config = ConfigDict(frozen=True)

    inst_id: str
    bar: str
    expected_bars: int
    returned_bars: int
    missing_bars: int
    duplicate_count: int
    zero_volume_count: int
    gap_in_window: bool
    # A hole between the first and last returned bar: backtest claims on it are does_not_support.
    interior_gap: bool
    gaps: list[GapRange]

    @classmethod
    def of(cls, q: DataQuality) -> QualitySummary:
        return cls(
            inst_id=q.inst_id,
            bar=q.bar,
            expected_bars=q.expected_bars,
            returned_bars=q.returned_bars,
            missing_bars=q.missing_bars,
            duplicate_count=q.duplicate_count,
            zero_volume_count=q.zero_volume_count,
            gap_in_window=q.gap_in_window,
            interior_gap=q.interior_gap,
            gaps=list(q.gaps),
        )


class Period(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: datetime | None
    end: datetime | None


class Sample(BaseModel):
    """What the Historian must state for every answer (brief §7.3)."""

    model_config = ConfigDict(frozen=True)

    inst_id: str | None
    inst_ids: list[str]
    bar: str
    period: Period
    # Cases behind the statistic: bars, returns, neighbours, event cases or trades (see each tool).
    n: int
    n_meaning: str
    bars_loaded: int
    weak_sample: bool  # n < agents.history.verdict.min_n
    unrepaired_gap: bool  # any series has an interior gap
    data_quality: list[QualitySummary]
    reference_data: bool = False  # True is reserved for non-OKX underlying data (later patch)


class _Out(BaseModel):
    sample: Sample
    status: Status
    note: str | None = None


class StatsOut(_Out):
    result: StatsResult | None


class RegimeOut(_Out):
    result: RegimeResult | None


class VolForecastOut(_Out):
    result: VolForecast | None


class DrawdownOut(_Out):
    result: DrawdownProfile | None


class SeasonalityOut(_Out):
    result: SeasonalityResult | None


class AnalogsOut(_Out):
    result: AnalogsResult | None


class LeadLagOut(_Out):
    result: LeadLagResult | None


class EventStudyOut(_Out):
    event_type: str
    events_used: list[datetime]
    result: EventStudyResult | None


class BacktestOut(_Out):
    taker_fee_source: Literal["caller", "config"]
    result: BacktestResult | None


# -- loading -------------------------------------------------------------------------------------------


def _series_cfg(tool: str) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = history_config()[tool]
    return cfg


def _bar(given: str | None, tool: str) -> str:
    return given or str(_series_cfg(tool)["bar"])


def _lookback(given: int | None, tool: str) -> int:
    return given if given is not None else int(_series_cfg(tool)["lookback_bars"])


def _load(s: Session, ctx: ToolContext, inst_id: str, bar: str, start: datetime) -> Candles:
    as_of = ctx.now()
    return load_candles(s, inst_id, bar, start, as_of, as_of=as_of, env=ctx.env)


def _load_back(s: Session, ctx: ToolContext, inst_id: str, bar: str, bars: int) -> Candles:
    return _load(s, ctx, inst_id, bar, ctx.now() - bar_delta(bar) * bars)


def _sample(candles: Sequence[Candles], bar: str, n: int, n_meaning: str, *, inst_id: str | None) -> Sample:
    step = bar_delta(bar)
    starts = [c.df.index[0] for c in candles if len(c.df)]
    ends = [c.df.index[-1] for c in candles if len(c.df)]
    period = (
        Period(start=min(starts).to_pydatetime(), end=(max(ends) + step).to_pydatetime())
        if starts
        else Period(start=None, end=None)
    )
    return Sample(
        inst_id=inst_id,
        inst_ids=[c.quality.inst_id for c in candles],
        bar=bar,
        period=period,
        n=n,
        n_meaning=n_meaning,
        bars_loaded=sum(len(c.df) for c in candles),
        weak_sample=n < min_n(),
        unrepaired_gap=any(c.quality.interior_gap for c in candles),
        data_quality=[QualitySummary.of(c.quality) for c in candles],
    )


def _as_of(ctx: ToolContext, candles: Sequence[Candles], bar: str) -> datetime:
    """Close time of the newest bar used; ctx.now() when nothing was loaded."""
    ends = [c.df.index[-1] for c in candles if len(c.df)]
    if not ends:
        return ctx.now()
    closed: datetime = (max(ends) + bar_delta(bar)).to_pydatetime()
    return min(closed, ctx.now())


def _no_data_note(inst_ids: Sequence[str], bar: str, ctx: ToolContext) -> str:
    return f"no {bar} candles for {', '.join(inst_ids)} in {ctx.env} closed by {ctx.now().isoformat()}"


def _asset_class(s: Session, ctx: ToolContext, inst_id: str) -> str | None:
    row = s.execute(
        select(Instrument.asset_class).where(Instrument.env == ctx.env, Instrument.inst_id == inst_id)
    ).scalar_one_or_none()
    return row


def _funding(s: Session, ctx: ToolContext, inst_id: str, start: datetime) -> pd.Series[float]:
    as_of = ctx.now()
    rows = s.execute(
        select(FundingRate.ts, FundingRate.funding_rate)
        .where(
            FundingRate.env == ctx.env,
            FundingRate.inst_id == inst_id,
            FundingRate.ts >= start,
            FundingRate.ts <= as_of,
            ctx.bound(FundingRate.ts),
        )
        .order_by(FundingRate.ts)
    ).all()
    index = pd.DatetimeIndex([r[0] for r in rows], tz=UTC) if rows else pd.DatetimeIndex([], tz=UTC)
    return pd.Series([float(r[1]) for r in rows], index=index, dtype="float64")


async def _threaded(fn: Any, *args: Any) -> ToolResult:
    result: ToolResult = await asyncio.to_thread(fn, *args)
    return result


# -- single-series tools -------------------------------------------------------------------------------


def _single(
    ctx: ToolContext,
    args: HistorySeriesInput,
    tool: str,
    out: type[_Out],
    compute: Any,
    n_of: Any,
    n_meaning: str,
) -> ToolResult:
    bar = _bar(args.bar, tool)
    with ctx.session_factory() as s:
        candles = _load_back(s, ctx, args.inst_id, bar, _lookback(args.lookback, tool))
        asset_class = _asset_class(s, ctx, args.inst_id)
    as_of = _as_of(ctx, [candles], bar)
    if candles.df.empty:
        sample = _sample([candles], bar, 0, n_meaning, inst_id=args.inst_id)
        data = out.model_validate(
            {
                "sample": sample,
                "status": "no_data",
                "note": _no_data_note([args.inst_id], bar, ctx),
                "result": None,
            }
        )
        return ToolResult(data=data, as_of=as_of, source=SOURCE)
    try:
        result = compute(candles.df, bar, asset_class)
    except ValueError as exc:
        sample = _sample([candles], bar, len(candles.df), n_meaning, inst_id=args.inst_id)
        data = out.model_validate(
            {"sample": sample, "status": "insufficient_data", "note": str(exc), "result": None}
        )
        return ToolResult(data=data, as_of=as_of, source=SOURCE)
    sample = _sample([candles], bar, int(n_of(result)), n_meaning, inst_id=args.inst_id)
    data = out.model_validate({"sample": sample, "status": "ok", "note": None, "result": result})
    return ToolResult(data=data, as_of=as_of, source=SOURCE)


@atlas_tool("history", "compute_stats", StatsInput, StatsOut)
async def compute_stats_tool(ctx: ToolContext, args: StatsInput) -> ToolResult:
    """Returns, volatility (realised, Parkinson, Garman-Klass), ATR, skew, kurtosis, max drawdown,
    average daily range, hour-of-day (UTC and Baghdad) and weekday tables for inst_id over the last
    `lookback` closed bars. sample.n = bars used. status no_data when there are no candles."""
    return await _threaded(
        _single,
        ctx,
        args,
        "compute_stats",
        StatsOut,
        lambda df, bar, ac: compute_stats(df, bar, asset_class=ac),
        lambda r: r.n,
        "bars",
    )


@atlas_tool("history", "detect_regime", HistorySeriesInput, RegimeOut)
async def detect_regime_tool(ctx: ToolContext, args: HistorySeriesInput) -> ToolResult:
    """Current regime label (trend_up, trend_down, range, high_vol, low_vol) with its probability
    and days since it started, from a Gaussian HMM (rule-based fallback; method and fallback_reason
    say which). sample.n = bars used."""
    return await _threaded(
        _single,
        ctx,
        args,
        "detect_regime",
        RegimeOut,
        lambda df, bar, ac: detect_regime(df),
        lambda r: r.n,
        "bars",
    )


@atlas_tool("history", "volatility_forecast", VolForecastInput, VolForecastOut)
async def volatility_forecast_tool(ctx: ToolContext, args: VolForecastInput) -> ToolResult:
    """GARCH(1,1) (EWMA fallback) forecast of sigma over the next `horizon` hours, in percent and in
    ATR units (sigma_atr, for stop-noise checks). sample.n = returns used."""
    return await _threaded(
        _single,
        ctx,
        args,
        "volatility_forecast",
        VolForecastOut,
        lambda df, bar, ac: volatility_forecast(df, float(args.horizon)),
        lambda r: r.n_returns,
        "returns",
    )


@atlas_tool("history", "drawdown_profile", HistorySeriesInput, DrawdownOut)
async def drawdown_profile_tool(ctx: ToolContext, args: HistorySeriesInput) -> ToolResult:
    """Deepest drawdown episodes (depth, duration, time to recover), the current drawdown and the
    median recovery time. sample.n = bars used."""
    return await _threaded(
        _single,
        ctx,
        args,
        "drawdown_profile",
        DrawdownOut,
        lambda df, bar, ac: drawdown_profile(df),
        lambda r: r.n,
        "bars",
    )


@atlas_tool("history", "seasonality", SeasonalityInput, SeasonalityOut)
async def seasonality_tool(ctx: ToolContext, args: SeasonalityInput) -> ToolResult:
    """Mean/median log return and t-stat by hour of day (UTC and Baghdad), weekday, month, or hour
    around funding times (by). sample.n = returns used."""
    return await _threaded(
        _single,
        ctx,
        args,
        "seasonality",
        SeasonalityOut,
        lambda df, bar, ac: seasonality(df, args.by),
        lambda r: r.n,
        "returns",
    )


# -- find_analogs --------------------------------------------------------------------------------------


def _analogs(ctx: ToolContext, args: FindAnalogsInput) -> ToolResult:
    cfg = _series_cfg("find_analogs")
    bar = args.bar or str(cfg["bar"])
    window = args.window if args.window is not None else int(cfg["window_bars"])
    as_of = ctx.now()
    start = as_of - _YEAR * args.lookback_years
    with ctx.session_factory() as s:
        candles = _load(s, ctx, args.inst_id, bar, start)
        funding = _funding(s, ctx, args.inst_id, start) if args.include_funding else None
    envelope_as_of = _as_of(ctx, [candles], bar)
    meaning = "independent analog windows (neighbours)"
    if candles.df.empty:
        sample = _sample([candles], bar, 0, meaning, inst_id=args.inst_id)
        out = AnalogsOut(
            sample=sample, status="no_data", note=_no_data_note([args.inst_id], bar, ctx), result=None
        )
        return ToolResult(data=out, as_of=envelope_as_of, source=SOURCE)
    try:
        result = find_analogs(
            candles.df,
            bar,
            window,
            as_of=as_of,
            k=args.k,
            horizons=args.horizons,
            funding=funding if funding is not None and len(funding) else None,
        )
    except ValueError as exc:
        raise ToolError("invalid_input", str(exc)) from exc
    status: Status = "ok" if result.neighbours else "insufficient_data"
    sample = _sample([candles], bar, len(result.neighbours), meaning, inst_id=args.inst_id)
    out = AnalogsOut(sample=sample, status=status, note=result.note, result=result)
    return ToolResult(data=out, as_of=envelope_as_of, source=SOURCE)


@atlas_tool("history", "find_analogs", FindAnalogsInput, AnalogsOut)
async def find_analogs_tool(ctx: ToolContext, args: FindAnalogsInput) -> ToolResult:
    """The k past windows most similar to the latest `window` closed bars (returns path, volatility,
    volume z, funding), with the forward return distribution per horizon (median, p10, p90,
    pct_positive) and the unconditional base rate. Never uses data after as_of. sample.n = analogs."""
    return await _threaded(_analogs, ctx, args)


# -- cross_asset_lead_lag ------------------------------------------------------------------------------


def _lead_lag(ctx: ToolContext, args: LeadLagInput) -> ToolResult:
    bar = _bar(args.bar, "cross_asset_lead_lag")
    lookback = _lookback(args.lookback, "cross_asset_lead_lag")
    with ctx.session_factory() as s:
        a = _load_back(s, ctx, args.a, bar, lookback)
        b = _load_back(s, ctx, args.b, bar, lookback)
    as_of = _as_of(ctx, [a, b], bar)
    meaning = "joined returns"
    missing = [c.quality.inst_id for c in (a, b) if c.df.empty]
    if missing:
        sample = _sample([a, b], bar, 0, meaning, inst_id=None)
        out = LeadLagOut(sample=sample, status="no_data", note=_no_data_note(missing, bar, ctx), result=None)
        return ToolResult(data=out, as_of=as_of, source=SOURCE)
    result = cross_asset_lead_lag(a.df, b.df, args.max_lag)
    status: Status = "ok" if result.best_lag is not None else "insufficient_data"
    sample = _sample([a, b], bar, result.n, meaning, inst_id=None)
    note = None if status == "ok" else "too few overlapping bars for any lag"
    out = LeadLagOut(sample=sample, status=status, note=note, result=result)
    return ToolResult(data=out, as_of=as_of, source=SOURCE)


@atlas_tool("history", "cross_asset_lead_lag", LeadLagInput, LeadLagOut)
async def cross_asset_lead_lag_tool(ctx: ToolContext, args: LeadLagInput) -> ToolResult:
    """Correlation of a's and b's log returns at lags -max_lag..+max_lag bars; positive lag means a
    leads b. Gives best lag, its correlation and the leader. sample.n = joined returns."""
    return await _threaded(_lead_lag, ctx, args)


# -- event_study ---------------------------------------------------------------------------------------


def _event_instruments(s: Session, ctx: ToolContext, args: EventStudyInput) -> list[Instrument]:
    cfg = _series_cfg("event_study")
    if args.inst_id:
        row = s.execute(
            select(Instrument).where(Instrument.env == ctx.env, Instrument.inst_id == args.inst_id)
        ).scalar_one_or_none()
        if row is None:
            # Unknown to the instruments table: still try its candles (base ccy from the id).
            return [Instrument(env=ctx.env, inst_id=args.inst_id, base_ccy=args.inst_id.split("-")[0])]
        return [row]
    rows = (
        s.execute(
            select(Instrument)
            .where(
                Instrument.env == ctx.env,
                Instrument.asset_class == args.asset_class,
                Instrument.state == "live",
            )
            .order_by(Instrument.inst_id)
            .limit(int(cfg["max_instruments"]))
        )
        .scalars()
        .all()
    )
    return list(rows)


def _economic_events(
    s: Session, ctx: ToolContext, spec: Mapping[str, Any], start: datetime
) -> list[datetime]:
    as_of = ctx.now()
    stmt = select(EconomicEvent.scheduled_at, EconomicEvent.name).where(
        EconomicEvent.scheduled_at >= start,
        EconomicEvent.scheduled_at <= as_of,
        ctx.bound(EconomicEvent.scheduled_at),
    )
    countries = [str(c) for c in spec.get("countries") or []]
    if countries:
        stmt = stmt.where(EconomicEvent.country.in_(countries))
    patterns = [re.compile(str(p), re.IGNORECASE) for p in spec["names"]]
    times = {ts for ts, name in s.execute(stmt).all() if any(p.search(name) for p in patterns)}
    return sorted(times)


def _earnings_events(s: Session, ctx: ToolContext, symbol: str, start: datetime) -> list[datetime]:
    hours: Mapping[str, Any] = _series_cfg("event_study")["earnings_hour_utc"]
    as_of = ctx.now()
    rows = s.execute(
        select(EarningsEvent.date, EarningsEvent.time).where(
            EarningsEvent.symbol == symbol,
            EarningsEvent.date >= start.date(),
            EarningsEvent.date <= as_of.date(),
        )
    ).all()
    out: list[datetime] = []
    for day, when in rows:
        hour = int(hours.get(str(when or ""), hours["default"]))
        ts = datetime.combine(day, time(hour), tzinfo=UTC)
        if ts <= as_of:
            out.append(ts)
    return sorted(out)


def _event_study(ctx: ToolContext, args: EventStudyInput) -> ToolResult:
    cfg = _series_cfg("event_study")
    if bool(args.inst_id) == bool(args.asset_class):
        raise ToolError("invalid_input", "give exactly one of inst_id or asset_class")
    event_type = args.event_type.strip().lower()
    known: Mapping[str, Any] = cfg["event_types"]
    if event_type != "earnings" and event_type not in known:
        raise ToolError(
            "unknown_event_type", f"event_type must be one of: earnings, {', '.join(sorted(known))}"
        )
    if event_type == "earnings" and not args.inst_id:
        raise ToolError("invalid_input", "earnings studies need inst_id (earnings dates are per company)")
    bar = args.bar or str(cfg["bar"])
    lo, hi = args.window
    if lo >= hi:
        raise ToolError("invalid_input", "window must be (hours before, hours after) with before < after")
    as_of = ctx.now()
    start = as_of - _YEAR * args.lookback_years
    with ctx.session_factory() as s:
        instruments = _event_instruments(s, ctx, args)
        if event_type == "earnings":
            events = _earnings_events(s, ctx, str(instruments[0].base_ccy or ""), start)
        else:
            events = _economic_events(s, ctx, known[event_type], start)
        events = events[-int(cfg["max_events"]) :]
        candles = [_load(s, ctx, i.inst_id, bar, start) for i in instruments]
    meaning = "(instrument, event) cases"
    with_data = [c for c in candles if not c.df.empty]
    as_of_env = _as_of(ctx, candles, bar)

    def _empty(note: str, status: Status = "no_data") -> ToolResult:
        sample = _sample(candles, bar, 0, meaning, inst_id=args.inst_id)
        out = EventStudyOut(
            sample=sample, status=status, note=note, event_type=event_type, events_used=events, result=None
        )
        return ToolResult(data=out, as_of=as_of_env, source=SOURCE)

    if not instruments:
        return _empty(f"no live {args.asset_class} instruments in {ctx.env}")
    if not with_data:
        return _empty(_no_data_note([i.inst_id for i in instruments], bar, ctx))
    if not events:
        return _empty(f"no {event_type} events between {start.isoformat()} and {as_of.isoformat()}")
    prices: pd.DataFrame | dict[str, pd.DataFrame]
    if args.inst_id:
        prices = with_data[0].df
    else:
        prices = {c.quality.inst_id: c.df for c in with_data}
    try:
        result = event_study(prices, events, (timedelta(hours=lo), timedelta(hours=hi)), bar=bar)
    except ValueError as exc:
        return _empty(str(exc), "insufficient_data")
    status: Status = "ok" if result.n_cases else "insufficient_data"
    sample = _sample(candles, bar, result.n_cases, meaning, inst_id=args.inst_id)
    out = EventStudyOut(
        sample=sample,
        status=status,
        note=None if status == "ok" else "no event had a complete window of closed bars",
        event_type=event_type,
        events_used=events,
        result=result,
    )
    return ToolResult(data=out, as_of=as_of_env, source=SOURCE)


@atlas_tool("history", "event_study", EventStudyInput, EventStudyOut)
async def event_study_tool(ctx: ToolContext, args: EventStudyInput) -> ToolResult:
    """Average and dispersion of moves around past events (event_type: cpi, ppi, fomc, nfp, gdp,
    retail_sales, unemployment from the economic calendar, or earnings for a stock perp) for one
    inst_id or an asset_class, over window (hours before, after). Returns per offset and ATR-scaled
    moves at 1/4/24 h. sample.n = (instrument, event) cases."""
    return await _threaded(_event_study, ctx, args)


# -- backtest_rule -------------------------------------------------------------------------------------


def _day(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=UTC)


def _backtest(ctx: ToolContext, args: BacktestRuleInput) -> ToolResult:
    cfg = _series_cfg("backtest")
    try:
        rule = RuleSpec.model_validate(args.rule_spec)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise ToolError("invalid_rule", details) from exc
    unknown = set(args.costs) - {"taker_fee_rate", "slippage_bps"}
    if unknown:
        raise ToolError("invalid_input", f"unknown costs keys: {', '.join(sorted(unknown))}")
    bar = args.bar or str(cfg["bar"])
    step = bar_delta(bar)
    as_of = ctx.now()
    if args.period is not None:
        p_start, p_end = _day(args.period[0]), min(_day(args.period[1]) + timedelta(days=1), as_of)
        if p_start >= p_end:
            raise ToolError("invalid_input", "period must start before its end and before as_of")
    else:
        p_start, p_end = as_of - step * int(cfg["lookback_bars"]), as_of
    load_from = p_start - step * int(cfg["warmup_bars"])
    fee_source: Literal["caller", "config"] = "caller" if "taker_fee_rate" in args.costs else "config"
    fee = float(args.costs.get("taker_fee_rate", float(cfg["taker_fee_rate"])))
    slippage = args.costs.get("slippage_bps")
    with ctx.session_factory() as s:
        candles = [_load(s, ctx, i, bar, load_from) for i in args.inst_ids]
        funding = {i: _funding(s, ctx, i, load_from) for i in args.inst_ids}
        asset_class = _asset_class(s, ctx, args.inst_ids[0])
    env_as_of = _as_of(ctx, candles, bar)
    meaning = "trades"
    inst_id = args.inst_ids[0] if len(args.inst_ids) == 1 else None
    missing = [c.quality.inst_id for c in candles if c.df.empty]
    with_data = [c for c in candles if not c.df.empty]

    def _empty(status: Status, note: str) -> ToolResult:
        sample = _sample(candles, bar, 0, meaning, inst_id=inst_id)
        out = BacktestOut(sample=sample, status=status, note=note, taker_fee_source=fee_source, result=None)
        return ToolResult(data=out, as_of=env_as_of, source=SOURCE)

    if not with_data:
        return _empty("no_data", _no_data_note(missing, bar, ctx))
    costs = Costs(
        taker_fee_rate=fee,
        slippage_bps=slippage,
        funding={k: v for k, v in funding.items() if len(v)},
    )
    try:
        result = backtest_rule(
            rule,
            {c.quality.inst_id: c.df for c in with_data},
            (p_start, p_end),
            costs,
            splits=args.splits,
            quality={c.quality.inst_id: c.quality for c in with_data},
            bar=bar,
            asset_class=asset_class,
        )
    except ValueError as exc:
        return _empty("insufficient_data", str(exc))
    note = f"no candles for {', '.join(missing)}; excluded" if missing else None
    sample = _sample(candles, bar, result.n_trades, meaning, inst_id=inst_id)
    out = BacktestOut(sample=sample, status="ok", note=note, taker_fee_source=fee_source, result=result)
    return ToolResult(data=out, as_of=env_as_of, source=SOURCE)


@atlas_tool("history", "backtest_rule", BacktestRuleInput, BacktestOut)
async def backtest_rule_tool(ctx: ToolContext, args: BacktestRuleInput) -> ToolResult:
    """Walk-forward backtest of a rule (rule_spec: name, side long|short, indicators {name: {kind:
    sma|ema|rsi, window}}, entry [conditions, all must hold], exit [any closes], optional stop_pct /
    take_profit_pct; condition = {left, op: crosses_above|crosses_below|above|below, right}) with
    taker fee, slippage (default 2 bps) and funding. Reports CAGR, Sharpe, Sortino, max DD, win rate,
    profit factor, trades, in- vs out-of-sample and overfit_warning. data_gap=true means any claim
    from it is does_not_support. sample.n = trades."""
    return await _threaded(_backtest, ctx, args)
