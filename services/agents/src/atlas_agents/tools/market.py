"""``okx_market`` read tools (brief §6.2, ATL-85), served from Postgres only (decision #245).

No tool here calls OKX. Market data is whatever trading_core's sync and backfill wrote to the
TimescaleDB tables; where Phase 1 stores nothing (the order book) the tool says so with
``available: false`` and a reason instead of inventing data.

Replay (``ctx.replay_as_of``) rules:

* candles: a bar is returned only once it had **closed** by ``ctx.now()`` (``ts + bar <= now``,
  the atlas_quant rule) and is confirmed, so the bar still forming at the cutoff never leaks;
* funding / open interest / long-short / taker volume: ``ts <= now`` via ``ctx.bound``;
* instruments: only those listed (``list_time``, else first synced ``created_at``) by the cutoff. The
  static spec columns (tick size, max leverage, state) are the current values; OKX rarely changes
  them, and the result says so in ``note``.

Every result's ``as_of`` is the newest row's timestamp; an empty result is stamped ``ctx.now()``
(the instant the table was checked) and carries a ``reason``. ``source`` is ``postgres:<table>``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from atlas_quant.grid import bar_delta
from atlas_shared.db import (
    Candle,
    FundingRate,
    Instrument,
    LongShortRatio,
    OpenInterest,
    TakerVolume,
)
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from atlas_agents.tools.catalogue import (
    CcyPeriodInput,
    GetCandlesInput,
    GetOrderbookInput,
    GetTickersInput,
    HistoryDaysInput,
    InstIdInput,
    ListInstrumentsInput,
)
from atlas_agents.tools.framework import ToolContext, ToolError, ToolResult, atlas_tool

STATIC_SPEC_NOTE: Final = (
    "Instrument spec columns are the latest synced values, not as they were at the replay cutoff."
)


def market_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["market_tools"]
    return config


def src(*tables: str) -> str:
    """``postgres:<table>`` (several tables joined with ``,``)."""
    return "postgres:" + ",".join(tables)


def newest(stamps: Sequence[datetime | None], ctx: ToolContext) -> datetime:
    """The newest timestamp, never later than ``ctx.now()``; ``ctx.now()`` when there is none."""
    present = [s for s in stamps if s is not None]
    if not present:
        return ctx.now()
    return min(max(present), ctx.now())


def closed_bars(ctx: ToolContext, bar: str) -> ColumnElement[bool]:
    """Bars of ``bar`` that are confirmed and had closed by ``ctx.now()`` (``ts + bar <= now``)."""
    return and_(
        Candle.env == ctx.env,
        Candle.bar == bar,
        Candle.confirmed.is_(True),
        Candle.ts <= ctx.now() - bar_delta(bar),
        ctx.bound(Candle.ts),
    )


def listed(ctx: ToolContext) -> ColumnElement[bool]:
    """Instruments of this env known by ``ctx.now()``."""
    return and_(
        Instrument.env == ctx.env,
        ctx.bound(func.coalesce(Instrument.list_time, Instrument.created_at)),
    )


def find_instrument(session: Session, ctx: ToolContext, inst_id: str) -> Instrument:
    row = session.scalars(select(Instrument).where(listed(ctx), Instrument.inst_id == inst_id)).first()
    if row is None:
        raise ToolError("not_found", f"no instrument {inst_id} in env {ctx.env}")
    return row


def quote_volume(candle: Candle) -> Decimal:
    """Quote-currency volume of a bar: OKX's volCcyQuote, else vol_ccy x close."""
    if candle.vol_ccy_quote is not None:
        return candle.vol_ccy_quote
    if candle.vol_ccy is not None:
        return candle.vol_ccy * candle.c
    return Decimal(0)


# -- tickers -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TickerCalc:
    inst_id: str
    last: Decimal
    last_ts: datetime
    open_24h: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h_quote: Decimal
    bars: int

    @property
    def change_24h_pct(self) -> Decimal | None:
        if self.open_24h == 0:
            return None
        return (self.last / self.open_24h - 1) * 100


def compute_tickers(session: Session, ctx: ToolContext, inst_ids: Sequence[str]) -> dict[str, TickerCalc]:
    """Last close, 24h open/high/low/change and quote volume per instrument, from closed
    ``ticker_bar`` candles in the ``ticker_window_hours`` ending at each instrument's newest bar."""
    if not inst_ids:
        return {}
    config = market_config()
    bar = str(config["ticker_bar"])
    window = timedelta(hours=int(config["ticker_window_hours"]))
    # Look back one extra window so an instrument whose newest bar is a little stale still has a
    # full 24h behind that bar.
    rows = session.scalars(
        select(Candle)
        .where(closed_bars(ctx, bar), Candle.inst_id.in_(inst_ids), Candle.ts > ctx.now() - 2 * window)
        .order_by(Candle.inst_id, Candle.ts)
    ).all()
    by_inst: dict[str, list[Candle]] = {}
    for row in rows:
        by_inst.setdefault(row.inst_id, []).append(row)
    out: dict[str, TickerCalc] = {}
    step = bar_delta(bar)
    for inst_id, candles in by_inst.items():
        last = candles[-1]
        window_start = last.ts + step - window
        recent = [c for c in candles if c.ts >= window_start]
        out[inst_id] = TickerCalc(
            inst_id=inst_id,
            last=last.c,
            last_ts=last.ts,
            open_24h=recent[0].o,
            high_24h=max(c.h for c in recent),
            low_24h=min(c.l for c in recent),
            volume_24h_quote=sum((quote_volume(c) for c in recent), Decimal(0)),
            bars=len(recent),
        )
    return out


# -- list_instruments --------------------------------------------------------------------------------


class InstrumentRow(BaseModel):
    inst_id: str
    inst_type: str
    asset_class: str
    inst_category: str | None
    base_ccy: str | None
    quote_ccy: str | None
    settle_ccy: str | None
    state: str
    max_lever: Decimal | None
    volume_24h_quote: Decimal | None


class ListInstrumentsOut(BaseModel):
    instruments: list[InstrumentRow]
    order: str
    volume_basis: str
    note: str | None = None
    reason: str | None = None


@atlas_tool("okx_market", "list_instruments", ListInstrumentsInput, ListInstrumentsOut)
async def list_instruments(ctx: ToolContext, args: ListInstrumentsInput) -> ToolResult:
    """Instruments of the active environment from the synced OKX universe, filtered by inst_type,
    asset_class, state (default live) and min_24h_volume_usd (24h quote volume from 1H candles;
    USDT/USDC quotes count as USD). Sorted by 24h quote volume, highest first."""
    with ctx.session_factory() as session:
        query = select(Instrument).where(listed(ctx))
        if args.inst_type is not None:
            query = query.where(Instrument.inst_type == args.inst_type)
        if args.asset_class is not None:
            query = query.where(Instrument.asset_class == args.asset_class)
        if args.state is not None:
            query = query.where(Instrument.state == args.state)
        instruments = session.scalars(query).all()
        tickers = compute_tickers(session, ctx, [i.inst_id for i in instruments])
    rows: list[InstrumentRow] = []
    for inst in instruments:
        ticker = tickers.get(inst.inst_id)
        volume = ticker.volume_24h_quote if ticker is not None else None
        if args.min_24h_volume_usd is not None and (
            volume is None or volume < Decimal(str(args.min_24h_volume_usd))
        ):
            continue
        rows.append(
            InstrumentRow(
                inst_id=inst.inst_id,
                inst_type=inst.inst_type,
                asset_class=inst.asset_class,
                inst_category=inst.inst_category,
                base_ccy=inst.base_ccy,
                quote_ccy=inst.quote_ccy,
                settle_ccy=inst.settle_ccy,
                state=inst.state,
                max_lever=inst.max_lever,
                volume_24h_quote=volume,
            )
        )
    rows.sort(key=lambda r: (r.volume_24h_quote is None, -(r.volume_24h_quote or 0), r.inst_id))
    rows = rows[: args.limit]
    stamps = [t.last_ts for t in tickers.values()] + [i.last_seen_at or i.updated_at for i in instruments]
    return ToolResult(
        data=ListInstrumentsOut(
            instruments=rows,
            order="volume_24h_quote desc",
            volume_basis=f"sum of closed {market_config()['ticker_bar']} candles' quote volume",
            note=STATIC_SPEC_NOTE if ctx.replaying else None,
            reason=None if rows else "no synced instrument matches the filters",
        ),
        as_of=newest(stamps, ctx),
        source=src("instruments", "candles"),
    )


# -- get_tickers -------------------------------------------------------------------------------------


class TickerRow(BaseModel):
    inst_id: str
    last: Decimal
    last_bar_ts: datetime
    open_24h: Decimal
    high_24h: Decimal
    low_24h: Decimal
    change_24h_pct: Decimal | None
    volume_24h_quote: Decimal
    bars_in_window: int


class TickersOut(BaseModel):
    inst_type: str
    tickers: list[TickerRow]
    basis: str
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_tickers", GetTickersInput, TickersOut)
async def get_tickers(ctx: ToolContext, args: GetTickersInput) -> ToolResult:
    """Ticker-style summary for every live instrument of inst_type, computed from stored closed 1H
    candles (not a live OKX ticker): last close, 24h open/high/low, 24h change % and 24h quote
    volume. Sorted by 24h quote volume, highest first."""
    with ctx.session_factory() as session:
        inst_ids = session.scalars(
            select(Instrument.inst_id).where(
                listed(ctx), Instrument.inst_type == args.inst_type, Instrument.state == "live"
            )
        ).all()
        tickers = compute_tickers(session, ctx, list(inst_ids))
    rows = [
        TickerRow(
            inst_id=t.inst_id,
            last=t.last,
            last_bar_ts=t.last_ts,
            open_24h=t.open_24h,
            high_24h=t.high_24h,
            low_24h=t.low_24h,
            change_24h_pct=t.change_24h_pct,
            volume_24h_quote=t.volume_24h_quote,
            bars_in_window=t.bars,
        )
        for t in tickers.values()
    ]
    rows.sort(key=lambda r: (-r.volume_24h_quote, r.inst_id))
    config = market_config()
    return ToolResult(
        data=TickersOut(
            inst_type=args.inst_type,
            tickers=rows,
            basis=f"closed {config['ticker_bar']} candles over {config['ticker_window_hours']}h",
            order="volume_24h_quote desc",
            reason=None if rows else f"no closed candles for live {args.inst_type} instruments",
        ),
        as_of=newest([t.last_ts for t in tickers.values()], ctx),
        source=src("candles", "instruments"),
    )


# -- get_candles -------------------------------------------------------------------------------------


class CandleRow(BaseModel):
    ts: datetime
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 - OHLC field name
    c: Decimal
    vol: Decimal
    vol_quote: Decimal


class CandlesOut(BaseModel):
    inst_id: str
    bar: str
    candles: list[CandleRow]
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_candles", GetCandlesInput, CandlesOut)
async def get_candles(ctx: ToolContext, args: GetCandlesInput) -> ToolResult:
    """Closed, confirmed OHLCV candles for inst_id on bar, start <= ts < end (ts is the bar's open
    time, UTC), at most limit bars, NEWEST FIRST. vol is in contracts/base units; vol_quote is the
    quote-currency volume."""
    with ctx.session_factory() as session:
        query = select(Candle).where(closed_bars(ctx, args.bar), Candle.inst_id == args.inst_id)
        if args.start is not None:
            query = query.where(Candle.ts >= args.start)
        if args.end is not None:
            query = query.where(Candle.ts < args.end)
        candles = session.scalars(query.order_by(Candle.ts.desc()).limit(args.limit)).all()
    rows = [
        CandleRow(ts=c.ts, o=c.o, h=c.h, l=c.l, c=c.c, vol=c.vol, vol_quote=quote_volume(c)) for c in candles
    ]
    return ToolResult(
        data=CandlesOut(
            inst_id=args.inst_id,
            bar=args.bar,
            candles=rows,
            order="ts desc",
            reason=None if rows else f"no closed {args.bar} candles for {args.inst_id} in the window",
        ),
        as_of=newest([c.ts for c in candles], ctx),
        source=src("candles"),
    )


# -- get_orderbook -----------------------------------------------------------------------------------


class OrderbookOut(BaseModel):
    inst_id: str
    available: bool
    reason: str
    bids: list[list[Decimal]]
    asks: list[list[Decimal]]


@atlas_tool("okx_market", "get_orderbook", GetOrderbookInput, OrderbookOut)
async def get_orderbook(ctx: ToolContext, args: GetOrderbookInput) -> ToolResult:
    """Order book for inst_id. Atlas does not store order books yet (the WebSocket books feed
    arrives in Phase 2), so this always answers available=false with the reason; never infer depth
    or spread from it."""
    with ctx.session_factory() as session:
        find_instrument(session, ctx, args.inst_id)
    return ToolResult(
        data=OrderbookOut(
            inst_id=args.inst_id,
            available=False,
            reason="Atlas stores no order book in Phase 1; the books feed arrives with the Phase 2 "
            "WebSocket market data. Depth, spread and slippage are unknown.",
            bids=[],
            asks=[],
        ),
        as_of=ctx.now(),
        source=src("instruments"),
    )


# -- funding / open interest -------------------------------------------------------------------------


class FundingRow(BaseModel):
    ts: datetime
    funding_rate: Decimal
    realized_rate: Decimal | None


class FundingOut(BaseModel):
    inst_id: str
    history_days: int
    latest: FundingRow | None
    history: list[FundingRow]
    mean_rate: Decimal | None
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_funding", HistoryDaysInput, FundingOut)
async def get_funding(ctx: ToolContext, args: HistoryDaysInput) -> ToolResult:
    """Funding rates for a perpetual inst_id: the latest settled/recorded rate and every rate in
    the last history_days (0 = latest only), NEWEST FIRST, plus their mean. Rates are per funding
    interval as decimals (0.0001 = 0.01%)."""
    with ctx.session_factory() as session:
        base = select(FundingRate).where(
            FundingRate.env == ctx.env, FundingRate.inst_id == args.inst_id, ctx.bound(FundingRate.ts)
        )
        if args.history_days == 0:
            query = base.order_by(FundingRate.ts.desc()).limit(1)
        else:
            query = base.where(FundingRate.ts >= ctx.now() - timedelta(days=args.history_days)).order_by(
                FundingRate.ts.desc()
            )
        found = session.scalars(query).all()
    rows = [FundingRow(ts=r.ts, funding_rate=r.funding_rate, realized_rate=r.realized_rate) for r in found]
    mean = sum((r.funding_rate for r in rows), Decimal(0)) / len(rows) if rows else None
    return ToolResult(
        data=FundingOut(
            inst_id=args.inst_id,
            history_days=args.history_days,
            latest=rows[0] if rows else None,
            history=rows,
            mean_rate=mean,
            order="ts desc",
            reason=None if rows else f"no funding rates stored for {args.inst_id} in the window",
        ),
        as_of=newest([r.ts for r in found], ctx),
        source=src("funding_rates"),
    )


class OpenInterestRow(BaseModel):
    ts: datetime
    oi: Decimal | None
    oi_ccy: Decimal | None
    oi_usd: Decimal | None


class OpenInterestOut(BaseModel):
    inst_id: str
    history_days: int
    latest: OpenInterestRow | None
    history: list[OpenInterestRow]
    change_pct: Decimal | None
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_open_interest", HistoryDaysInput, OpenInterestOut)
async def get_open_interest(ctx: ToolContext, args: HistoryDaysInput) -> ToolResult:
    """Open interest for inst_id (an instId, or a bare currency such as BTC for the currency-level
    series): latest value and the last history_days (0 = latest only), NEWEST FIRST. oi is in
    contracts, oi_ccy in coin, oi_usd in USD. change_pct compares the newest oi_usd (else oi) with
    the oldest in the window."""
    with ctx.session_factory() as session:
        base = select(OpenInterest).where(
            OpenInterest.env == ctx.env, OpenInterest.inst_id == args.inst_id, ctx.bound(OpenInterest.ts)
        )
        if args.history_days == 0:
            query = base.order_by(OpenInterest.ts.desc()).limit(1)
        else:
            query = base.where(OpenInterest.ts >= ctx.now() - timedelta(days=args.history_days)).order_by(
                OpenInterest.ts.desc()
            )
        found = session.scalars(query).all()
    rows = [OpenInterestRow(ts=r.ts, oi=r.oi, oi_ccy=r.oi_ccy, oi_usd=r.oi_usd) for r in found]
    return ToolResult(
        data=OpenInterestOut(
            inst_id=args.inst_id,
            history_days=args.history_days,
            latest=rows[0] if rows else None,
            history=rows,
            change_pct=_change_pct(rows),
            order="ts desc",
            reason=None if rows else f"no open interest stored for {args.inst_id} in the window",
        ),
        as_of=newest([r.ts for r in found], ctx),
        source=src("open_interest"),
    )


def _change_pct(rows: Sequence[OpenInterestRow]) -> Decimal | None:
    if len(rows) < 2:
        return None
    for field in ("oi_usd", "oi"):
        new, old = getattr(rows[0], field), getattr(rows[-1], field)
        if new is not None and old is not None and old != 0:
            change: Decimal = (new / old - 1) * 100
            return change
    return None


# -- long/short ratio and taker volume ---------------------------------------------------------------


class RatioRow(BaseModel):
    ts: datetime
    ratio: Decimal


class LongShortOut(BaseModel):
    ccy: str
    period: str
    series: list[RatioRow]
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_long_short_ratio", CcyPeriodInput, LongShortOut)
async def get_long_short_ratio(ctx: ToolContext, args: CcyPeriodInput) -> ToolResult:
    """OKX long/short account ratio for a currency (e.g. BTC) on period 5m|1H|1D, the most recent
    rows NEWEST FIRST. ratio > 1 means more accounts are net long than short."""
    limit = int(market_config()["series_limit"])
    with ctx.session_factory() as session:
        found = session.scalars(
            select(LongShortRatio)
            .where(
                LongShortRatio.env == ctx.env,
                LongShortRatio.ccy == args.ccy,
                LongShortRatio.period == args.period,
                ctx.bound(LongShortRatio.ts),
            )
            .order_by(LongShortRatio.ts.desc())
            .limit(limit)
        ).all()
    return ToolResult(
        data=LongShortOut(
            ccy=args.ccy,
            period=args.period,
            series=[RatioRow(ts=r.ts, ratio=r.ratio) for r in found],
            order="ts desc",
            reason=None if found else f"no long/short ratio stored for {args.ccy} {args.period}",
        ),
        as_of=newest([r.ts for r in found], ctx),
        source=src("long_short_ratio"),
    )


class TakerRow(BaseModel):
    ts: datetime
    buy_vol: Decimal
    sell_vol: Decimal
    buy_sell_ratio: Decimal | None


class TakerVolumeOut(BaseModel):
    ccy: str
    period: str
    series: list[TakerRow]
    order: str
    reason: str | None = None


@atlas_tool("okx_market", "get_taker_volume", CcyPeriodInput, TakerVolumeOut)
async def get_taker_volume(ctx: ToolContext, args: CcyPeriodInput) -> ToolResult:
    """OKX taker buy and sell volume for a currency (e.g. BTC) on period 5m|1H|1D, the most recent
    rows NEWEST FIRST, with buy/sell ratio (null when sell volume is 0)."""
    limit = int(market_config()["series_limit"])
    with ctx.session_factory() as session:
        found = session.scalars(
            select(TakerVolume)
            .where(
                TakerVolume.env == ctx.env,
                TakerVolume.ccy == args.ccy,
                TakerVolume.period == args.period,
                ctx.bound(TakerVolume.ts),
            )
            .order_by(TakerVolume.ts.desc())
            .limit(limit)
        ).all()
    rows = [
        TakerRow(
            ts=r.ts,
            buy_vol=r.buy_vol,
            sell_vol=r.sell_vol,
            buy_sell_ratio=None if r.sell_vol == 0 else r.buy_vol / r.sell_vol,
        )
        for r in found
    ]
    return ToolResult(
        data=TakerVolumeOut(
            ccy=args.ccy,
            period=args.period,
            series=rows,
            order="ts desc",
            reason=None if rows else f"no taker volume stored for {args.ccy} {args.period}",
        ),
        as_of=newest([r.ts for r in found], ctx),
        source=src("taker_volume"),
    )


# -- mark price and instrument spec ------------------------------------------------------------------


class MarkPriceOut(BaseModel):
    inst_id: str
    price: Decimal | None
    is_proxy: bool
    proxy: str
    bar_ts: datetime | None
    bar_close_ts: datetime | None
    age_seconds: int | None
    reason: str | None = None


@atlas_tool("okx_market", "get_mark_price", InstIdInput, MarkPriceOut)
async def get_mark_price(ctx: ToolContext, args: InstIdInput) -> ToolResult:
    """A PROXY for the mark price of inst_id: the close of the newest closed 1m candle (Atlas does
    not store OKX mark prices yet). age_seconds says how old that close is; treat a large age as
    stale data."""
    bar = str(market_config()["mark_price_bar"])
    with ctx.session_factory() as session:
        candle = session.scalars(
            select(Candle)
            .where(closed_bars(ctx, bar), Candle.inst_id == args.inst_id)
            .order_by(Candle.ts.desc())
            .limit(1)
        ).first()
    proxy = f"close of the newest closed {bar} candle, not OKX's mark price"
    if candle is None:
        return ToolResult(
            data=MarkPriceOut(
                inst_id=args.inst_id,
                price=None,
                is_proxy=True,
                proxy=proxy,
                bar_ts=None,
                bar_close_ts=None,
                age_seconds=None,
                reason=f"no closed {bar} candles stored for {args.inst_id}",
            ),
            as_of=ctx.now(),
            source=src("candles"),
        )
    close_ts = candle.ts + bar_delta(bar)
    return ToolResult(
        data=MarkPriceOut(
            inst_id=args.inst_id,
            price=candle.c,
            is_proxy=True,
            proxy=proxy,
            bar_ts=candle.ts,
            bar_close_ts=close_ts,
            age_seconds=max(0, int((ctx.now() - close_ts).total_seconds())),
        ),
        as_of=newest([candle.ts], ctx),
        source=src("candles"),
    )


class InstrumentSpecOut(BaseModel):
    inst_id: str
    inst_type: str
    inst_family: str | None
    inst_category: str | None
    asset_class: str
    base_ccy: str | None
    quote_ccy: str | None
    settle_ccy: str | None
    ct_val: Decimal | None
    ct_val_ccy: str | None
    ct_mult: Decimal | None
    lot_sz: Decimal
    min_sz: Decimal
    tick_sz: Decimal
    max_lever: Decimal | None
    state: str
    list_time: datetime | None
    exp_time: datetime | None
    note: str | None = None


@atlas_tool("okx_market", "get_instrument_spec", InstIdInput, InstrumentSpecOut)
async def get_instrument_spec(ctx: ToolContext, args: InstIdInput) -> ToolResult:
    """Contract spec for inst_id as last synced from OKX: contract value (ct_val in ct_val_ccy),
    lot size, minimum size, tick size, max leverage, state, listing and expiry times. Round prices
    to tick_sz and sizes down to lot_sz."""
    with ctx.session_factory() as session:
        inst = find_instrument(session, ctx, args.inst_id)
        spec = InstrumentSpecOut(
            inst_id=inst.inst_id,
            inst_type=inst.inst_type,
            inst_family=inst.inst_family,
            inst_category=inst.inst_category,
            asset_class=inst.asset_class,
            base_ccy=inst.base_ccy,
            quote_ccy=inst.quote_ccy,
            settle_ccy=inst.settle_ccy,
            ct_val=inst.ct_val,
            ct_val_ccy=inst.ct_val_ccy,
            ct_mult=inst.ct_mult,
            lot_sz=inst.lot_sz,
            min_sz=inst.min_sz,
            tick_sz=inst.tick_sz,
            max_lever=inst.max_lever,
            state=inst.state,
            list_time=inst.list_time,
            exp_time=inst.exp_time,
            note=STATIC_SPEC_NOTE if ctx.replaying else None,
        )
        stamp = inst.last_seen_at or inst.updated_at
    return ToolResult(data=spec, as_of=newest([stamp], ctx), source=src("instruments"))
