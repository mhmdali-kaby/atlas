"""``atlas_data`` read tools (brief §6.2, ATL-85), served from Postgres only (decision #245).

Every tool here only SELECTs. None imports trading_core or an OKX client (a test asserts it), and
none reads the ``connections`` table: an account result can never carry a connection id, UID,
label, API key, secret or passphrase (brief §1.6, §18).

* account summary / positions come from the Phase 0 snapshot tables trading_core's account sync
  writes (``equity_curve``, ``balances_snapshots``, ``positions_snapshots``). The "current"
  snapshot is the newest trading-account ``balances_snapshots.taken_at`` (each sync tick writes all
  its rows with one ``taken_at``), bounded by the replay cutoff.
* settings are built from an explicit allow-list of numeric keys (``SETTINGS_SNAPSHOT_KEYS``);
  nothing outside it can reach an agent. In replay the version in force at the cutoff is read from
  ``settings_history``.
* open orders and the watchlist have no table yet (Executor: Phase 2; watchlist: patch E), so those
  tools answer an empty list with ``available: false`` and the reason.
"""

from __future__ import annotations

import itertools
import math
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import BalanceSnapshot, Candle, EquityPoint, PositionSnapshot
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel
from sqlalchemy import Integer, String, Uuid, column, select, table
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Session

from atlas_agents.tools.catalogue import GetCorrelationsInput, GetWatchlistInput
from atlas_agents.tools.framework import NoInput, ToolContext, ToolError, ToolResult, atlas_tool
from atlas_agents.tools.market import closed_bars, market_config, newest, src

# The only settings keys an agent may see: numeric limits from brief §14 (SettingsSchema's
# NUMERIC_FIELDS minus the onboarding flag). Adding a key here is a deliberate, reviewed change;
# test_market_account_tools pins this set.
SETTINGS_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "max_loss_per_trade_pct",
        "max_position_pct",
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_leverage",
        "max_open_positions",
        "max_share_per_instrument_pct",
        "max_correlated_exposure_pct",
        "min_expected_r",
        "max_consecutive_losses",
        "autonomy_level",
        "max_instruments_per_day",
        "max_spread_bps",
        "min_24h_volume_usd_crypto_perp",
        "min_24h_volume_usd_stock_perp",
        "min_24h_volume_usd_commodity_perp",
        "min_24h_volume_usd_spot",
    }
)

NO_SPEND_NOTE: Final = (
    "Spend and realised loss are 0 because Atlas places no orders until the Executor exists "
    "(Phase 2). USDT money limits (Settings -> Money limits) are not configurable yet; only the "
    "percentage limits apply."
)

# Laravel-owned tables with no SQLAlchemy model; only the columns read here are declared.
_settings = table(
    "settings",
    column("id", Uuid),
    column("env", String),
    column("version", Integer),
    column("data", JSONB),
    column("updated_at", TIMESTAMP(timezone=True)),
)
_settings_history = table(
    "settings_history",
    column("settings_id", Uuid),
    column("version", Integer),
    column("data", JSONB),
    column("created_at", TIMESTAMP(timezone=True)),
)


def _sync_instant(session: Session, ctx: ToolContext) -> datetime | None:
    """``taken_at`` of the newest account-sync tick known by ``ctx.now()``."""
    stamp = session.scalar(
        select(BalanceSnapshot.taken_at)
        .where(
            BalanceSnapshot.env == ctx.env,
            BalanceSnapshot.account == "trading",
            ctx.bound(BalanceSnapshot.taken_at),
        )
        .order_by(BalanceSnapshot.taken_at.desc())
        .limit(1)
    )
    if stamp is None:
        stamp = session.scalar(
            select(PositionSnapshot.taken_at)
            .where(PositionSnapshot.env == ctx.env, ctx.bound(PositionSnapshot.taken_at))
            .order_by(PositionSnapshot.taken_at.desc())
            .limit(1)
        )
    return stamp


def _latest_equity(session: Session, ctx: ToolContext) -> EquityPoint | None:
    return session.scalars(
        select(EquityPoint)
        .where(EquityPoint.env == ctx.env, ctx.bound(EquityPoint.ts))
        .order_by(EquityPoint.ts.desc())
        .limit(1)
    ).first()


def _positions_at(session: Session, ctx: ToolContext, taken_at: datetime | None) -> list[PositionSnapshot]:
    if taken_at is None:
        return []
    return list(
        session.scalars(
            select(PositionSnapshot)
            .where(PositionSnapshot.env == ctx.env, PositionSnapshot.taken_at == taken_at)
            .order_by(PositionSnapshot.inst_id, PositionSnapshot.pos_side)
        ).all()
    )


# -- get_account_summary -----------------------------------------------------------------------------


class BalanceRow(BaseModel):
    account: str
    ccy: str
    eq: Decimal
    avail_bal: Decimal | None
    frozen_bal: Decimal | None
    eq_usd: Decimal | None


class AccountSummaryOut(BaseModel):
    env: str
    total_equity_usd: Decimal | None
    unrealised_pnl_usd: Decimal | None
    equity_ts: datetime | None
    trading_equity_usd: Decimal
    funding_equity_usd: Decimal
    balances: list[BalanceRow]
    balances_taken_at: datetime | None
    open_positions: int
    reason: str | None = None


@atlas_tool("atlas_data", "get_account_summary", NoInput, AccountSummaryOut)
async def get_account_summary(ctx: ToolContext, args: NoInput) -> ToolResult:
    """The OKX account of the active environment as last synced: total equity and unrealised PnL in
    USD, equity per account (trading, funding), balances per currency (largest USD value first) and
    the number of open positions. Numbers only; no account identifiers."""
    with ctx.session_factory() as session:
        equity = _latest_equity(session, ctx)
        taken_at = _sync_instant(session, ctx)
        balances = (
            list(
                session.scalars(
                    select(BalanceSnapshot).where(
                        BalanceSnapshot.env == ctx.env, BalanceSnapshot.taken_at == taken_at
                    )
                ).all()
            )
            if taken_at is not None
            else []
        )
        positions = _positions_at(session, ctx, taken_at)
    rows = sorted(
        (
            BalanceRow(
                account=b.account,
                ccy=b.ccy,
                eq=b.eq,
                avail_bal=b.avail_bal,
                frozen_bal=b.frozen_bal,
                eq_usd=b.eq_usd,
            )
            for b in balances
        ),
        key=lambda r: (-(r.eq_usd or Decimal(0)), r.account, r.ccy),
    )

    def account_total(name: str) -> Decimal:
        return sum((r.eq_usd or Decimal(0) for r in rows if r.account == name), Decimal(0))

    nothing = equity is None and taken_at is None
    return ToolResult(
        data=AccountSummaryOut(
            env=ctx.env,
            total_equity_usd=equity.total_eq_usd if equity is not None else None,
            unrealised_pnl_usd=equity.upl_usd if equity is not None else None,
            equity_ts=equity.ts if equity is not None else None,
            trading_equity_usd=account_total("trading"),
            funding_equity_usd=account_total("funding"),
            balances=rows,
            balances_taken_at=taken_at,
            open_positions=len([p for p in positions if p.pos != 0]),
            reason="no account sync has been stored for this environment" if nothing else None,
        ),
        as_of=newest([equity.ts if equity is not None else None, taken_at], ctx),
        source=src("equity_curve", "balances_snapshots", "positions_snapshots"),
    )


# -- get_positions -----------------------------------------------------------------------------------


class PositionRow(BaseModel):
    inst_id: str
    pos_side: str
    pos: Decimal
    avg_px: Decimal | None
    mark_px: Decimal | None
    liq_px: Decimal | None
    lever: Decimal | None
    upl: Decimal | None
    upl_ratio: Decimal | None
    margin: Decimal | None
    mgn_mode: str | None
    opened_at: datetime | None


class PositionsOut(BaseModel):
    env: str
    taken_at: datetime | None
    positions: list[PositionRow]
    units: str
    reason: str | None = None


@atlas_tool("atlas_data", "get_positions", NoInput, PositionsOut)
async def get_positions(ctx: ToolContext, args: NoInput) -> ToolResult:
    """Open positions on the OKX account as of the last account sync (largest margin first): side,
    size (pos, in contracts or coin as OKX reports it), entry (avg_px), mark, liquidation price,
    leverage, unrealised PnL and its ratio, margin and margin mode."""
    with ctx.session_factory() as session:
        taken_at = _sync_instant(session, ctx)
        found = [p for p in _positions_at(session, ctx, taken_at) if p.pos != 0]
    rows = sorted(
        (
            PositionRow(
                inst_id=p.inst_id,
                pos_side=p.pos_side,
                pos=p.pos,
                avg_px=p.avg_px,
                mark_px=p.mark_px,
                liq_px=p.liq_px,
                lever=p.lever,
                upl=p.upl,
                upl_ratio=p.upl_ratio,
                margin=p.margin,
                mgn_mode=p.mgn_mode,
                opened_at=p.c_time,
            )
            for p in found
        ),
        key=lambda r: (-abs(r.margin or Decimal(0)), r.inst_id),
    )
    reason = None
    if taken_at is None:
        reason = "no account sync has been stored for this environment"
    elif not rows:
        reason = "no open positions at the last account sync"
    return ToolResult(
        data=PositionsOut(
            env=ctx.env,
            taken_at=taken_at,
            positions=rows,
            units="upl and margin in the instrument's settle currency; pos as OKX reports it",
            reason=reason,
        ),
        as_of=newest([taken_at], ctx),
        source=src("positions_snapshots", "balances_snapshots"),
    )


# -- get_open_orders ---------------------------------------------------------------------------------


class OpenOrdersOut(BaseModel):
    available: bool
    orders: list[dict[str, Any]]
    reason: str


@atlas_tool("atlas_data", "get_open_orders", NoInput, OpenOrdersOut)
async def get_open_orders(ctx: ToolContext, args: NoInput) -> ToolResult:
    """Open (working) orders and algo orders on the account. Read-only. Atlas stores no orders
    before the Executor exists (Phase 2), so this answers available=false with an empty list."""
    return ToolResult(
        data=OpenOrdersOut(
            available=False,
            orders=[],
            reason="Atlas places and stores no orders until the Executor (Phase 2); there is no "
            "orders table yet. Orders placed outside Atlas are not synced either.",
        ),
        as_of=ctx.now(),
        source="postgres:none (orders table arrives in Phase 2)",
    )


# -- get_settings_snapshot ---------------------------------------------------------------------------


class SettingsSnapshotOut(BaseModel):
    env: str
    version: int
    values: dict[str, int | Decimal]
    units: str


def _number(value: object) -> int | Decimal | None:
    """An int or a finite decimal; anything else (text, bool, nested) is dropped."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str | float):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    return None


def load_settings(session: Session, ctx: ToolContext) -> tuple[int, dict[str, int | Decimal], datetime]:
    """(version, allow-listed numeric values, as_of) of the settings in force at ``ctx.now()``."""
    if ctx.replaying:
        row = session.execute(
            select(_settings_history.c.version, _settings_history.c.data, _settings_history.c.created_at)
            .join(_settings, _settings.c.id == _settings_history.c.settings_id)
            .where(_settings.c.env == ctx.env, ctx.bound(_settings_history.c.created_at))
            .order_by(_settings_history.c.version.desc())
            .limit(1)
        ).first()
    else:
        row = session.execute(
            select(_settings.c.version, _settings.c.data, _settings.c.updated_at).where(
                _settings.c.env == ctx.env
            )
        ).first()
    if row is None:
        raise ToolError("no_data", f"no settings saved for env {ctx.env} by {ctx.now().isoformat()}")
    version, data, stamp = row
    values: dict[str, int | Decimal] = {}
    if isinstance(data, dict):
        for key in sorted(SETTINGS_SNAPSHOT_KEYS):
            number = _number(data.get(key))
            if number is not None:
                values[key] = number
    return int(version), values, newest([stamp], ctx)


@atlas_tool("atlas_data", "get_settings_snapshot", NoInput, SettingsSnapshotOut)
async def get_settings_snapshot(ctx: ToolContext, args: NoInput) -> ToolResult:
    """The owner's current limits (read-only, numbers only): risk per trade, position size, daily
    and weekly loss, leverage, open positions, correlation and per-instrument caps, min expected R,
    consecutive-loss pause, autonomy level and market filters, with the settings version. You
    cannot change these; only the owner can, in Settings."""
    with ctx.session_factory() as session:
        version, values, as_of = load_settings(session, ctx)
    return ToolResult(
        data=SettingsSnapshotOut(
            env=ctx.env,
            version=version,
            values=values,
            units="*_pct are percent of equity; *_usd are USD; max_spread_bps in basis points",
        ),
        as_of=as_of,
        source=src("settings_history" if ctx.replaying else "settings"),
    )


# -- get_today_budget_left ---------------------------------------------------------------------------


class BudgetOut(BaseModel):
    env: str
    day: date
    settings_version: int
    equity_usd: Decimal | None
    max_risk_per_trade_usd: Decimal | None
    max_position_usd: Decimal | None
    daily_loss_limit_usd: Decimal | None
    daily_loss_used_usd: Decimal
    daily_loss_left_usd: Decimal | None
    new_exposure_spent_today_usd: Decimal
    max_open_positions: int | None
    open_positions: int
    positions_left: int | None
    note: str


def _pct_of(equity: Decimal | None, pct: int | Decimal | None) -> Decimal | None:
    if equity is None or pct is None:
        return None
    return equity * Decimal(pct) / 100


@atlas_tool("atlas_data", "get_today_budget_left", NoInput, BudgetOut)
async def get_today_budget_left(ctx: ToolContext, args: NoInput) -> ToolResult:
    """What is left of today's (Asia/Baghdad day) risk budget under the owner's settings: max risk
    per trade, max position size and daily loss limit in USD from the latest equity, loss used and
    left today, and open-position slots left. Spend is 0 until Atlas can place orders."""
    with ctx.session_factory() as session:
        version, values, settings_as_of = load_settings(session, ctx)
        equity = _latest_equity(session, ctx)
        taken_at = _sync_instant(session, ctx)
        open_positions = len([p for p in _positions_at(session, ctx, taken_at) if p.pos != 0])
    equity_usd = equity.total_eq_usd if equity is not None else None
    daily_limit = _pct_of(equity_usd, values.get("max_daily_loss_pct"))
    used = Decimal(0)
    max_open = values.get("max_open_positions")
    max_open_int = int(max_open) if max_open is not None else None
    day = ctx.now().astimezone(ZoneInfo(str(DEFAULTS["timezone"]["display"]))).date()
    return ToolResult(
        data=BudgetOut(
            env=ctx.env,
            day=day,
            settings_version=version,
            equity_usd=equity_usd,
            max_risk_per_trade_usd=_pct_of(equity_usd, values.get("max_loss_per_trade_pct")),
            max_position_usd=_pct_of(equity_usd, values.get("max_position_pct")),
            daily_loss_limit_usd=daily_limit,
            daily_loss_used_usd=used,
            daily_loss_left_usd=None if daily_limit is None else daily_limit - used,
            new_exposure_spent_today_usd=Decimal(0),
            max_open_positions=max_open_int,
            open_positions=open_positions,
            positions_left=None if max_open_int is None else max(0, max_open_int - open_positions),
            note=NO_SPEND_NOTE,
        ),
        as_of=newest([settings_as_of, equity.ts if equity is not None else None, taken_at], ctx),
        source=src("settings", "equity_curve", "positions_snapshots"),
    )


# -- get_watchlist -----------------------------------------------------------------------------------


class WatchlistOut(BaseModel):
    date: date
    available: bool
    items: list[dict[str, Any]]
    reason: str


@atlas_tool("atlas_data", "get_watchlist", GetWatchlistInput, WatchlistOut)
async def get_watchlist(ctx: ToolContext, args: GetWatchlistInput) -> ToolResult:
    """Today's (or date's, Asia/Baghdad) watchlist chosen by Market Scout. Market Scout is not built
    yet, so this answers available=false with an empty list."""
    day = args.date or ctx.now().astimezone(ZoneInfo(str(DEFAULTS["timezone"]["display"]))).date()
    return ToolResult(
        data=WatchlistOut(
            date=day,
            available=False,
            items=[],
            reason="No watchlist exists yet: Market Scout and the watchlists table arrive in Phase 1 "
            "patch E.",
        ),
        as_of=ctx.now(),
        source="postgres:none (watchlists table arrives in patch E)",
    )


# -- get_correlations --------------------------------------------------------------------------------


class CorrelationPair(BaseModel):
    a: str
    b: str
    corr: float | None
    n: int


class CorrelationsOut(BaseModel):
    lookback_days: int
    method: str
    pairs: list[CorrelationPair]
    missing: list[str]
    order: str
    reason: str | None = None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


@atlas_tool("atlas_data", "get_correlations", GetCorrelationsInput, CorrelationsOut)
async def get_correlations(ctx: ToolContext, args: GetCorrelationsInput) -> ToolResult:
    """Pairwise Pearson correlation of daily log returns between inst_ids over the last
    lookback_days, from closed 1D candles, on the days both instruments have a return (n). corr is
    null when n is too small or a series is flat. Sorted by |corr|, highest first."""
    bar = "1D"
    inst_ids = list(dict.fromkeys(args.inst_ids))
    start = ctx.now() - timedelta(days=args.lookback_days + 1)
    with ctx.session_factory() as session:
        candles = session.execute(
            select(Candle.inst_id, Candle.ts, Candle.c)
            .where(closed_bars(ctx, bar), Candle.inst_id.in_(inst_ids), Candle.ts >= start)
            .order_by(Candle.inst_id, Candle.ts)
        ).all()
    closes: dict[str, list[tuple[datetime, Decimal]]] = {}
    for inst_id, ts, close in candles:
        closes.setdefault(inst_id, []).append((ts, close))
    returns: dict[str, dict[datetime, float]] = {}
    for inst_id, series in closes.items():
        rets: dict[datetime, float] = {}
        for (prev_ts, prev), (ts, cur) in itertools.pairwise(series):
            if prev > 0 and cur > 0 and ts - prev_ts == timedelta(days=1):
                rets[ts] = math.log(float(cur) / float(prev))
        returns[inst_id] = rets
    min_obs = int(market_config()["min_correlation_obs"])
    pairs: list[CorrelationPair] = []
    present = [i for i in inst_ids if i in returns]
    for idx, a in enumerate(present):
        for b in present[idx + 1 :]:
            common = sorted(set(returns[a]) & set(returns[b]))
            xs = [returns[a][t] for t in common]
            ys = [returns[b][t] for t in common]
            corr = pearson(xs, ys) if len(common) >= min_obs else None
            pairs.append(CorrelationPair(a=a, b=b, corr=corr, n=len(common)))
    pairs.sort(key=lambda p: (p.corr is None, -abs(p.corr or 0.0), p.a, p.b))
    missing = [i for i in inst_ids if i not in returns]
    return ToolResult(
        data=CorrelationsOut(
            lookback_days=args.lookback_days,
            method="pearson on daily log returns of closed 1D candle closes",
            pairs=pairs,
            missing=missing,
            order="abs(corr) desc",
            reason=None if pairs else "fewer than two of the instruments have closed 1D candles",
        ),
        as_of=newest([ts for _i, ts, _c in candles], ctx),
        source=src("candles"),
    )


__all__ = ["SETTINGS_SNAPSHOT_KEYS", "load_settings", "pearson"]
