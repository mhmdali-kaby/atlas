"""``okx_market.get_ta_table``: the Technical Analyst's pre-computed table (brief §7.4, ATL-135).

One call assembles, for one instrument, everything §7.4 says code computes and the agent reads:

* per timeframe (``agents.ta.timeframes``): ATL-132's trend / momentum / volatility groups and the
  newest swings (:func:`atlas_agents.ta.table`), plus ATL-133's nearest support/resistance zones,
  the five pattern flags, relative volume, OBV trend, the session VWAP and the anchored VWAP, and
  how many bars late the newest closed bar is (``bars_late``);
* ``levels``: prior day and week high/low/close and the volume profile (both from the 1H frame,
  UTC sessions), and round numbers for stock/commodity perps;
* ``book``: the live books5 summary (spread, imbalance) and the book walked at the Risk Engine's max
  position notional for both sides (``slippage.buy`` / ``slippage.sell``), or why it is unavailable;
* ``spec``: tick size, lot size, min size and contract value.

Candles come from Postgres through :func:`atlas_quant.load_candles` at ``as_of = ctx.now()``, so a
replayed day never sees a bar that had not closed by the cutoff. ``bars_late`` = whole bars between
the newest closed bar's close and now: 0 when the bar that should be newest is there.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

import pandas as pd
from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents import ta
from atlas_agents.ta._structure import SwingPoint
from atlas_agents.ta.levels import build_zones, prior_period_levels, round_numbers
from atlas_agents.ta.microstructure import summarise_book, walk_book
from atlas_agents.ta.patterns import detect_patterns
from atlas_agents.ta.swings import Swing
from atlas_agents.ta.volume import anchored_vwap, obv_trend, relative_volume, session_vwap, volume_profile
from atlas_agents.tools.account import _latest_equity, _settings, _settings_history
from atlas_agents.tools.catalogue import GetOrderbookInput, InstIdInput
from atlas_agents.tools.framework import ToolContext, ToolResult, atlas_tool
from atlas_agents.tools.market import OrderbookOut, find_instrument, get_orderbook, newest

SOURCE: Final = "postgres:candles,instruments,settings,equity_curve; redis:atlas:md (books5)"
LEVELS_BAR: Final = "1H"
ATR_BAR: Final = "1H"


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["technical_analyst"]
    return cfg


def _jsonable(value: Any) -> Any:
    """Plain JSON values: datetimes as ISO strings, Decimals as strings, NaN/inf as None."""
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump())
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime | pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value.normalize()) if value.is_finite() else None
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    return value


def bars_late(last_close: datetime | None, now: datetime, bar: str) -> int | None:
    """Whole ``bar`` periods between the newest closed bar's close and ``now`` (None: no bar)."""
    if last_close is None:
        return None
    return max(0, int((now - last_close) // bar_delta(bar)))


def _points(frame: pd.DataFrame, swings: Sequence[Swing]) -> list[SwingPoint]:
    index = frame.index
    return [
        SwingPoint(
            ts=s.ts,
            price=s.price,
            kind=s.kind,
            confirmed_at=pd.Timestamp(index[min(s.confirmed_pos, len(index) - 1)]).to_pydatetime(),
        )
        for s in swings
    ]


def _nearest_zones(zones: Sequence[Any], price: float | None, keep: int) -> list[Any]:
    if price is None or keep <= 0:
        return []
    near = sorted(zones, key=lambda z: abs(z.mid - price))[:keep]
    return sorted(near, key=lambda z: z.mid)


def _structure(bar: str, frame: pd.DataFrame, entry: dict[str, Any], keep: int) -> dict[str, Any]:
    """ATL-133's structure for one timeframe, added to its ATL-132 entry."""
    out: dict[str, Any] = {"zones": [], "patterns": {}, "volume": {}}
    if frame.empty:
        return out
    atr = entry["volatility"].get("atr")
    _frame, swings = ta.compute(frame)
    points = _points(frame, swings)
    out["volume"] = {
        "rel_volume": relative_volume(frame),
        "obv": obv_trend(frame),
        "session_vwap": session_vwap(frame),
        "anchored_vwap": anchored_vwap(frame, points),
    }
    if atr is None or atr <= 0:
        out["note"] = "ATR undefined: no zones or pattern flags"
        return out
    zones = build_zones(frame, points, float(atr), timeframe=bar)
    out["zones"] = _nearest_zones(zones, entry.get("last_close"), keep)
    out["patterns"] = {name: flag for name, flag in detect_patterns(frame, points, float(atr)).items()}
    return out


def _settings_data(session: Session, ctx: ToolContext) -> Mapping[str, Any]:
    """The settings JSON in force at ``ctx.now()`` (only the two keys read below are used)."""
    if ctx.replaying:
        query = (
            select(_settings_history.c.data)
            .join(_settings, _settings.c.id == _settings_history.c.settings_id)
            .where(_settings.c.env == ctx.env, ctx.bound(_settings_history.c.created_at))
            .order_by(_settings_history.c.version.desc())
            .limit(1)
        )
    else:
        query = select(_settings.c.data).where(_settings.c.env == ctx.env)
    data = session.scalar(query)
    return data if isinstance(data, Mapping) else {}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def max_notional(session: Session, ctx: ToolContext) -> tuple[Decimal | None, str]:
    """The Risk Engine's max position notional: sizing equity (min of equity and the bot capital
    cap, decision #250) x ``max_position_pct``. (None, why) when either is unknown."""
    data = _settings_data(session, ctx)
    pct = _decimal(data.get("max_position_pct"))
    equity_row = _latest_equity(session, ctx)
    equity = equity_row.total_eq_usd if equity_row is not None else None
    if pct is None or pct <= 0:
        return None, "max_position_pct is not set"
    if equity is None or equity <= 0:
        return None, "no account equity has been synced"
    cap = _decimal(data.get("bot_capital_cap_usdt"))
    sizing = min(equity, cap) if cap is not None and cap > 0 else equity
    basis = "bot capital cap" if cap is not None and 0 < cap < equity else "account equity"
    return sizing * pct / 100, f"sizing equity ({basis}) x max_position_pct {pct}%"


def _load_frames(session: Session, ctx: ToolContext, inst_id: str) -> dict[str, pd.DataFrame]:
    as_of = ctx.now()
    bars: Mapping[str, Any] = config()["history_bars"]
    frames: dict[str, pd.DataFrame] = {}
    for bar in ta.ta_config()["timeframes"]:
        count = int(bars.get(bar, 500))
        start = as_of - bar_delta(bar) * (count + 1)
        frames[bar] = load_candles(session, inst_id, bar, start, as_of, as_of=as_of, env=ctx.env).df
    return frames


def _book(book: OrderbookOut, notional: Decimal | None, why: str, ct_val: Decimal | None) -> dict[str, Any]:
    out: dict[str, Any] = {"available": book.available, "reason": book.reason, "max_notional_usd": notional}
    out["max_notional_basis"] = why
    if not book.available or not book.bids or not book.asks:
        out["slippage"] = None
        return out
    out["summary"] = summarise_book(book.bids, book.asks)
    if notional is None or notional <= 0:
        out["slippage"] = None
        return out
    unit = ct_val if ct_val is not None and ct_val > 0 else Decimal(1)
    out["slippage"] = {
        side: walk_book(book.bids, book.asks, side, notional, ct_val=unit) for side in ("buy", "sell")
    }
    out["depth_note"] = (
        "books5 holds 5 levels a side: filled=false means the 5 levels do not cover the notional"
    )
    return out


def _db_part(ctx: ToolContext, inst_id: str) -> dict[str, Any]:
    with ctx.session_factory() as session:
        inst = find_instrument(session, ctx, inst_id)
        frames = _load_frames(session, ctx, inst_id)
        notional, why = max_notional(session, ctx)
        spec = {
            "tick_sz": inst.tick_sz,
            "lot_sz": inst.lot_sz,
            "min_sz": inst.min_sz,
            "ct_val": inst.ct_val,
            "asset_class": inst.asset_class,
        }
    keep = int(config()["zones_reported"])
    now = ctx.now()
    built = ta.table(inst_id, frames)
    for bar, entry in built["timeframes"].items():
        frame = frames.get(bar, pd.DataFrame())
        entry.update(_structure(bar, frame, entry, keep))
        entry["bars_late"] = bars_late(entry["as_of"], now, bar)
    levels_frame = frames.get(LEVELS_BAR, pd.DataFrame())
    last = built["timeframes"].get(LEVELS_BAR, {}).get("last_close")
    levels: dict[str, Any] = {"source_bar": LEVELS_BAR}
    if not levels_frame.empty:
        profile = volume_profile(levels_frame)
        levels["prior"] = prior_period_levels(levels_frame)
        levels["volume_profile"] = None if profile is None else profile.model_dump(exclude={"rows"})
    levels["round_numbers"] = round_numbers(last, str(spec["asset_class"])) if last else []
    return {
        "built": built,
        "levels": levels,
        "spec": spec,
        "notional": notional,
        "notional_why": why,
    }


def stale_timeframes(table_data: Mapping[str, Any], defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, Any]:
    """Timeframes of ``stale_check_bars`` whose newest closed bar is more than ``stale_bars`` bars
    late (or missing): ``{bar: bars_late | None}``. Empty means fresh."""
    cfg = config(defaults)
    limit = int(cfg["stale_bars"])
    out: dict[str, Any] = {}
    frames: Mapping[str, Any] = table_data.get("timeframes", {})
    for bar in cfg["stale_check_bars"]:
        late = (frames.get(bar) or {}).get("bars_late")
        if late is None or int(late) > limit:
            out[str(bar)] = late
    return out


async def build_ta_table(ctx: ToolContext, inst_id: str) -> tuple[dict[str, Any], datetime]:
    """(the JSON-ready table, its as_of). Raises ToolError not_found for an unknown instrument."""
    part = await asyncio.to_thread(_db_part, ctx, inst_id)
    book_result = await get_orderbook(ctx, GetOrderbookInput(inst_id=inst_id, depth=50))
    book = book_result.data
    assert isinstance(book, OrderbookOut)
    spec = part["spec"]
    built = part["built"]
    data_as_of = built["data_as_of"]
    table_data: dict[str, Any] = {
        "inst_id": inst_id,
        "data_as_of": data_as_of,
        "spec": spec,
        "timeframes": built["timeframes"],
        "levels": part["levels"],
        "book": _book(book, part["notional"], part["notional_why"], spec["ct_val"]),
        "stale_limit_bars": int(config()["stale_bars"]),
    }
    plain = _jsonable(table_data)
    plain["stale"] = stale_timeframes(plain)
    return plain, newest([data_as_of], ctx)


class TaTableOut(BaseModel):
    """The table as built by :func:`build_ta_table` (nested, documented in the module docstring)."""

    model_config = ConfigDict(extra="allow")

    inst_id: str
    data_as_of: str | None
    spec: dict[str, Any]
    timeframes: dict[str, Any]
    levels: dict[str, Any]
    book: dict[str, Any]
    stale: dict[str, Any]
    stale_limit_bars: int


@atlas_tool("okx_market", "get_ta_table", InstIdInput, TaTableOut)
async def get_ta_table(ctx: ToolContext, args: InstIdInput) -> ToolResult:
    """The Technical Analyst's pre-computed table for inst_id (all numbers computed by code): per
    timeframe (1D, 4H, 1H, 15m, 5m) EMA 20/50/200 with slopes, alignment, ADX, swings, RSI, MACD,
    ROC, divergence, ATR, Bollinger squeeze, Keltner breakout, the nearest support/resistance
    zones (ids like "1H:z3" for levels_used), pattern flags, relative volume, OBV, session and
    anchored VWAP and bars_late; prior day/week levels, the volume profile (POC/VAH/VAL) and round
    numbers; the order book summary and slippage at the Risk Engine's max position size; and the
    instrument's tick_sz/lot_sz. Use these numbers; do not recompute them."""
    plain, as_of = await build_ta_table(ctx, args.inst_id)
    return ToolResult(data=TaTableOut.model_validate(plain), as_of=as_of, source=SOURCE)


__all__ = [
    "SOURCE",
    "TaTableOut",
    "bars_late",
    "build_ta_table",
    "get_ta_table",
    "max_notional",
    "stale_timeframes",
]
