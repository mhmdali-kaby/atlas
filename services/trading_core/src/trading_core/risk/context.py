"""Risk Engine inputs (brief §10): the context dataclasses and the loaders that fill them.

:func:`load_context` reads everything the nine checks need at one instant: the settings row
(re-read on every call, brief §3.2), trading_state (created here when absent), the verified
connection, the market-data hash in Redis (ATL-125), the instrument spec, ATR(1H) via atlas_quant,
equity, balances and positions from the snapshot tables, correlations, today's used exposure, the
day/week loss and the economic calendar. Nothing here talks to OKX. :mod:`trading_core.risk.engine`
runs the pure checks on the result.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from atlas_quant.data import load_candles
from atlas_quant.indicators import wilder_atr_values
from atlas_shared.db import (
    BalanceSnapshot,
    Bill,
    Candle,
    Connection,
    DepositWithdrawal,
    EconomicEvent,
    EquityPoint,
    Instrument,
    PositionSnapshot,
    Proposal,
    TradingState,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.market_data import read_snapshot
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from trading_core.risk.inputs import USD_LIKE, InstrumentSpec, infer_contract_type
from trading_core.risk.rules import (
    EngineSettings,
    OpenPosition,
    ScheduledEvent,
    correlation,
    daily_log_returns,
    rules_config,
)


class RiskEngineError(RuntimeError):
    """The proposal cannot be evaluated at all (unknown id, not an entry, no settings row)."""


# --- Inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProposalInfo:
    id: uuid.UUID
    env: str
    inst_id: str
    side: Literal["buy", "sell"]
    order_type: str
    entry: Decimal | None
    entry_zone: tuple[Decimal, Decimal] | None
    stop: Decimal | None
    expected_r: Decimal | None
    leverage_suggestion: int | None
    size_suggestion_pct: Decimal | None
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    inst_id: str
    inst_type: str
    asset_class: str
    state: str
    spec: InstrumentSpec


@dataclass(frozen=True)
class RiskContext:
    """Everything the nine checks read, loaded at one instant."""

    now: datetime
    proposal: ProposalInfo
    settings_version: int
    settings_data: Mapping[str, Any]
    paused: bool = False
    pause_reason: str | None = None
    active_breakers: tuple[str, ...] = ()
    connection_verified: bool = False
    ticker_at: datetime | None = None
    instrument: InstrumentInfo | None = None
    atr_1h: Decimal | None = None
    mark: Decimal | None = None
    equity: Decimal | None = None
    available_usd: Decimal = Decimal(0)
    fee_rate: Decimal = Decimal(0)
    fee_source: str = "config_fallback"
    usdc_usd_rate: Decimal | None = None
    positions: tuple[OpenPosition, ...] = ()
    correlations: Mapping[str, float | None] = field(default_factory=dict)
    used_exposure_today_usd: Decimal = Decimal(0)
    trades_today: int = 0
    day_loss_usd: Decimal = Decimal(0)
    day_base_usd: Decimal | None = None
    week_loss_usd: Decimal = Decimal(0)
    week_base_usd: Decimal | None = None
    consecutive_losses: int = 0
    events: tuple[ScheduledEvent, ...] = ()


# --- Loading (I/O) -----------------------------------------------------------------------------


def _owner_midnight(now: datetime, *, week: bool) -> datetime:
    tz = ZoneInfo(str(DEFAULTS["timezone"]["display"]))
    local = now.astimezone(tz)
    day = local.date() - timedelta(days=local.weekday() if week else 0)
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        d = Decimal(str(value))
    except ArithmeticError:
        return None
    return d if d.is_finite() else None


def ensure_trading_state(session: Session, env: str, now: datetime) -> TradingState:
    """The env's trading_state row, created (not paused, no breakers) when absent."""
    row = session.scalar(select(TradingState).where(TradingState.env == env))
    if row is None:
        session.execute(
            text(
                "INSERT INTO trading_state (id, env, paused, breakers, created_at, updated_at) "
                "VALUES (:id, :env, false, '{}'::jsonb, :now, :now) ON CONFLICT (env) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "env": env, "now": now},
        )
        row = session.scalar(select(TradingState).where(TradingState.env == env))
    assert row is not None
    return row


def _proposal_info(row: Proposal) -> ProposalInfo:
    if row.kind != "entry":
        raise RiskEngineError(f"proposal {row.id} is a {row.kind}; only entries are risk-checked here")
    if row.side not in ("buy", "sell"):
        raise RiskEngineError(f"proposal {row.id} has no side")
    zone = None
    if isinstance(row.entry_zone, (list, tuple)) and len(row.entry_zone) == 2:
        a, b = _dec(row.entry_zone[0]), _dec(row.entry_zone[1])
        if a is not None and b is not None:
            zone = (a, b)
    return ProposalInfo(
        id=row.id,
        env=row.env,
        inst_id=row.inst_id,
        side="buy" if row.side == "buy" else "sell",
        order_type=row.order_type or "limit",
        entry=row.entry,
        entry_zone=zone,
        stop=row.stop,
        expected_r=row.expected_r,
        leverage_suggestion=row.leverage_suggestion,
        size_suggestion_pct=row.size_suggestion_pct,
        correlation_id=str(row.cycle_id) if row.cycle_id else None,
    )


def _instrument_info(row: Instrument) -> InstrumentInfo:
    spec = InstrumentSpec(
        inst_id=row.inst_id,
        ct_type=infer_contract_type(row.inst_type, row.base_ccy, row.settle_ccy),
        settle_ccy=(row.settle_ccy or row.quote_ccy or "USDT").upper(),
        quote_ccy=(row.quote_ccy or row.settle_ccy or "USDT").upper(),
        ct_val=row.ct_val if row.ct_val and row.ct_val > 0 else Decimal(1),
        ct_mult=row.ct_mult if row.ct_mult and row.ct_mult > 0 else Decimal(1),
        lot_sz=row.lot_sz,
        min_sz=row.min_sz,
        tick_sz=row.tick_sz,
        max_lever=row.max_lever,
    )
    return InstrumentInfo(row.inst_id, row.inst_type, row.asset_class, row.state, spec)


def _atr_1h(
    session: Session, env: str, inst_id: str, now: datetime, cfg: Mapping[str, Any]
) -> Decimal | None:
    bar, window = str(cfg["atr_bar"]), int(cfg["atr_window"])
    start = now - timedelta(hours=int(cfg["atr_lookback_bars"]) + 1)
    df = load_candles(session, inst_id, bar, start, now, as_of=now, env=env).df
    if len(df) < window:
        return None
    values = wilder_atr_values(df, window)
    last = float(values[-1])
    return None if last != last or last <= 0 else Decimal(repr(last))


def _usd_rate(session: Session, env: str, ccy: str) -> Decimal | None:
    if ccy in USD_LIKE:
        return Decimal(1)
    row = session.execute(
        select(BalanceSnapshot.eq, BalanceSnapshot.eq_usd)
        .where(
            BalanceSnapshot.env == env,
            BalanceSnapshot.ccy == ccy,
            BalanceSnapshot.eq > 0,
            BalanceSnapshot.eq_usd.is_not(None),
        )
        .order_by(BalanceSnapshot.taken_at.desc())
        .limit(1)
    ).first()
    return None if row is None else row.eq_usd / row.eq


def _positions(session: Session, env: str) -> list[OpenPosition]:
    latest = session.scalar(select(func.max(PositionSnapshot.taken_at)).where(PositionSnapshot.env == env))
    if latest is None:
        return []
    rows = session.execute(
        select(PositionSnapshot, Instrument)
        .join(
            Instrument,
            (Instrument.env == PositionSnapshot.env) & (Instrument.inst_id == PositionSnapshot.inst_id),
            isouter=True,
        )
        .where(PositionSnapshot.env == env, PositionSnapshot.taken_at == latest, PositionSnapshot.pos != 0)
    ).all()
    out: list[OpenPosition] = []
    for pos, inst in rows:
        if pos.pos_side == "long":
            direction = 1
        elif pos.pos_side == "short":
            direction = -1
        else:
            direction = 1 if pos.pos > 0 else -1
        ct_val = inst.ct_val if inst is not None and inst.ct_val else Decimal(1)
        ct_mult = inst.ct_mult if inst is not None and inst.ct_mult else Decimal(1)
        size = abs(pos.pos) * ct_val * ct_mult
        inverse = (
            inst is not None
            and infer_contract_type(inst.inst_type, inst.base_ccy, inst.settle_ccy) == "inverse"
        )
        mark = pos.mark_px or pos.avg_px or Decimal(0)
        notional = size if inverse else size * mark
        out.append(
            OpenPosition(pos.inst_id, direction, notional, inst.asset_class if inst is not None else "other")
        )
    return out


def _correlations(
    session: Session, env: str, inst_id: str, others: set[str], now: datetime, cfg: Mapping[str, Any]
) -> dict[str, float | None]:
    if not others:
        return {}
    ids = {inst_id, *others}
    start = now - timedelta(days=int(cfg["correlation_lookback_days"]) + 1)
    rows = session.execute(
        select(Candle.inst_id, Candle.ts, Candle.c).where(
            Candle.env == env,
            Candle.bar == "1D",
            Candle.confirmed.is_(True),
            Candle.inst_id.in_(ids),
            Candle.ts >= start,
            Candle.ts <= now - timedelta(days=1),
        )
    ).all()
    closes: dict[str, list[tuple[datetime, Decimal]]] = {}
    for iid, ts, c in rows:
        closes.setdefault(iid, []).append((ts, c))
    rets = {k: daily_log_returns(v) for k, v in closes.items()}
    base = rets.get(inst_id, {})
    min_obs = int(cfg["correlation_min_obs"])
    return {o: correlation(base, rets.get(o, {}), min_obs) for o in sorted(others) if o != inst_id}


def _equity_at_or_after(session: Session, env: str, since: datetime) -> Decimal | None:
    return session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(EquityPoint.env == env, EquityPoint.ts >= since)
        .order_by(EquityPoint.ts)
        .limit(1)
    )


def _net_flows(session: Session, env: str, since: datetime) -> Decimal:
    """Deposits minus withdrawals since ``since``, in USD for USD-like currencies only."""
    rows = session.execute(
        select(DepositWithdrawal.kind, DepositWithdrawal.ccy, DepositWithdrawal.amount).where(
            DepositWithdrawal.env == env, DepositWithdrawal.ts >= since
        )
    ).all()
    total = Decimal(0)
    for kind, ccy, amount in rows:
        if ccy.upper() in USD_LIKE | {"USDC"}:
            total += amount if kind == "deposit" else -amount
    return total


def _consecutive_losses(session: Session, env: str) -> int:
    """Losing closes in a row, newest first, from trade bills with a realised pnl (OKX type 2)."""
    pnls = session.scalars(
        select(Bill.pnl)
        .where(Bill.env == env, Bill.type == "2", Bill.pnl.is_not(None), Bill.pnl != 0)
        .order_by(Bill.ts.desc())
        .limit(200)
    ).all()
    count = 0
    for pnl in pnls:
        if pnl is None or pnl >= 0:
            break
        count += 1
    return count


@dataclass(frozen=True, slots=True)
class LossFigures:
    """Check 6's inputs: the day/week loss in USD against its start equity, and the losing streak.
    The circuit breakers (ATL-145) read the same figures, so the two can never disagree."""

    equity: Decimal | None
    day_loss_usd: Decimal
    day_base_usd: Decimal | None
    week_loss_usd: Decimal
    week_base_usd: Decimal | None
    consecutive_losses: int
    day_start: datetime
    week_start: datetime


def loss_figures(session: Session, env: str, now: datetime) -> LossFigures:
    """Loss = first equity point since Baghdad midnight (or Monday) + net deposits since then -
    current equity. With no equity point since the period began the loss is 0 and the base None."""
    equity = session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(EquityPoint.env == env)
        .order_by(EquityPoint.ts.desc())
        .limit(1)
    )
    day_start, week_start = _owner_midnight(now, week=False), _owner_midnight(now, week=True)

    def loss(since: datetime) -> tuple[Decimal, Decimal | None]:
        base = _equity_at_or_after(session, env, since)
        if base is None or equity is None:
            return Decimal(0), base
        return base + _net_flows(session, env, since) - equity, base

    day_loss, day_base = loss(day_start)
    week_loss, week_base = loss(week_start)
    return LossFigures(
        equity=equity,
        day_loss_usd=day_loss,
        day_base_usd=day_base,
        week_loss_usd=week_loss,
        week_base_usd=week_base,
        consecutive_losses=_consecutive_losses(session, env),
        day_start=day_start,
        week_start=week_start,
    )


def _used_today(session: Session, env: str, since: datetime, statuses: list[str]) -> tuple[Decimal, int]:
    """Approved notional and trade count of proposals that reached the Executor today (latest risk
    check per proposal)."""
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(final_notional_usd), 0), COUNT(*) FROM ("
            " SELECT DISTINCT ON (rc.proposal_id) rc.final_notional_usd"
            " FROM risk_checks rc JOIN proposals p ON p.id = rc.proposal_id"
            " WHERE rc.env = :env AND rc.created_at >= :since AND rc.result <> 'rejected'"
            " AND p.status = ANY(:statuses)"
            " ORDER BY rc.proposal_id, rc.created_at DESC) latest"
        ),
        {"env": env, "since": since, "statuses": statuses},
    ).one()
    return Decimal(row[0]), int(row[1])


def load_context(
    session: Session, rds: Any, proposal: Proposal, now: datetime, config: Mapping[str, Any] | None = None
) -> RiskContext:
    cfg = config or rules_config()
    p = _proposal_info(proposal)
    env = p.env
    settings = session.execute(
        text("SELECT version, data FROM settings WHERE env = :env"), {"env": env}
    ).first()
    if settings is None or not isinstance(settings[1], dict):
        raise RiskEngineError(f"no settings row for {env}")
    state = ensure_trading_state(session, env, now)
    breakers = state.breakers if isinstance(state.breakers, dict) else {}
    active = tuple(sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active")))
    verified = (
        session.scalar(
            select(func.count())
            .select_from(Connection)
            .where(
                Connection.exchange == "okx", Connection.env == env, Connection.last_verified_at.is_not(None)
            )
        )
        or 0
    )

    snap = read_snapshot(rds, env, p.inst_id)
    ticker_at = snap.channel_at.get("tickers")
    mark = _dec((snap.mark or {}).get("markPx")) or _dec((snap.ticker or {}).get("last"))

    inst_row = session.scalar(
        select(Instrument).where(Instrument.env == env, Instrument.inst_id == p.inst_id)
    )
    inst = _instrument_info(inst_row) if inst_row is not None else None

    losses = loss_figures(session, env, now)
    equity = losses.equity
    available = Decimal(0)
    usdc_rate = None
    if inst is not None:
        settle = inst.spec.settle_ccy if inst.spec.ct_type != "spot" else inst.spec.quote_ccy
        bal = session.execute(
            select(BalanceSnapshot.avail_bal, BalanceSnapshot.eq, BalanceSnapshot.eq_usd)
            .where(
                BalanceSnapshot.env == env,
                BalanceSnapshot.account == "trading",
                BalanceSnapshot.ccy == settle,
            )
            .order_by(BalanceSnapshot.taken_at.desc())
            .limit(1)
        ).first()
        rate = _usd_rate(session, env, settle)
        if bal is not None and bal.avail_bal is not None and rate is not None:
            available = bal.avail_bal * rate
        if inst.spec.quote_ccy == "USDC":
            usdc_rate = _usd_rate(session, env, "USDC")

    positions = _positions(session, env)
    held = {x.inst_id for x in positions}
    used_usd, trades = _used_today(session, env, losses.day_start, [str(s) for s in cfg["used_statuses"]])
    window = EngineSettings.from_settings_data(settings[1]).event_blackout_minutes
    events = session.execute(
        select(
            EconomicEvent.name, EconomicEvent.country, EconomicEvent.importance, EconomicEvent.scheduled_at
        ).where(
            EconomicEvent.importance >= int(cfg["high_impact_importance"]),
            EconomicEvent.scheduled_at >= now,
            EconomicEvent.scheduled_at <= now + timedelta(minutes=window),
        )
    ).all()

    return RiskContext(
        now=now,
        proposal=p,
        settings_version=int(settings[0]),
        settings_data=settings[1],
        paused=bool(state.paused),
        pause_reason=state.reason,
        active_breakers=active,
        connection_verified=verified > 0,
        ticker_at=ticker_at,
        instrument=inst,
        atr_1h=_atr_1h(session, env, p.inst_id, now, cfg) if inst is not None else None,
        mark=mark,
        equity=equity,
        available_usd=available,
        fee_rate=Decimal(str(cfg["fallback_taker_fee_rate"])),
        fee_source="config_fallback",
        usdc_usd_rate=usdc_rate,
        positions=tuple(positions),
        correlations=_correlations(session, env, p.inst_id, held, now, cfg),
        used_exposure_today_usd=used_usd,
        trades_today=trades,
        day_loss_usd=losses.day_loss_usd,
        day_base_usd=losses.day_base_usd,
        week_loss_usd=losses.week_loss_usd,
        week_base_usd=losses.week_base_usd,
        consecutive_losses=losses.consecutive_losses,
        events=tuple(ScheduledEvent(n, c, int(i), t) for n, c, i, t in events),
    )


__all__ = [
    "InstrumentInfo",
    "LossFigures",
    "ProposalInfo",
    "RiskContext",
    "RiskEngineError",
    "ensure_trading_state",
    "load_context",
    "loss_figures",
]
