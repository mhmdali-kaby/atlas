"""Paper trades: never-executed entry proposals simulated on stored candles (ATL-196, brief §7.6
Scoring "regret of skipped ideas", §7.7 Scoring).

Which proposals. Entry proposals whose latest version (no revision after it) has a status in
``agents.scoring.paper.statuses`` (rejected, expired, advise_only: vote, risk, owner and timeout
rejections, an unfilled entry, autonomy 1) and no Atlas entry order with a filled size on any version
of the chain. The latest version is simulated from its ``created_at`` (``decided_at``).

How (:func:`simulate`, pure, Decimal):

* **Entry.** market: the open of the first bar at or after decided_at. limit/post_only: the limit is
  the entry, else the zone edge the Executor uses (high for a buy, low for a sell); it fills only in a
  bar that trades *through* it (low < limit for a buy, high > limit for a sell) opening before
  decided_at + entry timeout, at the better of the open and the limit. No fill by then = not filled
  (no R: it is not a loss).
* **Exits.** In each bar from the fill: the stop first (a bar touching the stop and a target is a stop;
  a bar that opens past the stop exits at the open), then each take-profit in order at its price for
  its pct. In a limit fill bar that did not fill at the open only the stop is checked (the order inside
  the bar is unknown). The rest closes at the close of the last bar ending by entry + time stop
  (``time_stop``), else by entry + ``max_hold_hours`` (``horizon``).
* **R.** Per unit of |entry - stop|: r_gross = sum of pct x side x (exit - entry) / risk; fees_r =
  fee_rate x (entry + sum of pct x exit) / risk; realised_r = r_gross - fees_r. The stop does not trail
  (the Portfolio Manager's rules are not simulated).

A trade whose window has not closed yet is pending: nothing is written and the next run retries it.
Rows are upserted on proposal_id, so re-running never duplicates them.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from atlas_shared.db import Instrument, Order, PaperTrade, Proposal
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import and_, exists, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

log = logging.getLogger(__name__)

PLACES: Final = Decimal("0.00000001")
HUNDRED: Final = Decimal(100)


def paper_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["scoring"]["paper"]
    return config


# -- pure simulation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    ts: datetime
    """Bar open time (UTC)."""
    end: datetime
    """Bar close time."""
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741
    c: Decimal


@dataclass(frozen=True)
class TakeProfit:
    price: Decimal
    pct: Decimal


@dataclass(frozen=True)
class PaperSpec:
    side: str
    """buy | sell"""
    order_type: str
    """market | limit | post_only"""
    limit_px: Decimal | None
    stop: Decimal
    take_profits: Sequence[TakeProfit]
    time_stop_minutes: int | None
    decided_at: datetime


@dataclass(frozen=True)
class Leg:
    reason: str
    """stop | take_profit | time_stop | horizon"""
    price: Decimal
    ts: datetime
    pct: Decimal
    r: Decimal

    def as_json(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "price": str(self.price),
            "ts": self.ts.isoformat(),
            "pct": str(self.pct),
            "r": str(self.r),
        }


@dataclass(frozen=True)
class PaperResult:
    status: str
    """filled | not_filled"""
    exit_reason: str
    entry_px: Decimal | None = None
    entry_ts: datetime | None = None
    exit_px: Decimal | None = None
    exit_ts: datetime | None = None
    r_gross: Decimal | None = None
    fees_r: Decimal | None = None
    realised_r: Decimal | None = None
    tp1_hit: bool | None = None
    legs: list[Leg] = field(default_factory=list)


@dataclass(frozen=True)
class Walk:
    """The exits from a fill: legs in order and whether TP1 came before the stop."""

    legs: list[Leg]
    tp1_hit: bool


def _q(value: Decimal) -> Decimal:
    return value.quantize(PLACES)


def walk_exits(
    bars: Sequence[Bar],
    start: int,
    *,
    side: str,
    entry: Decimal,
    stop: Decimal,
    take_profits: Sequence[TakeProfit],
    end: datetime,
    end_reason: str,
    now: datetime,
    first_bar_stop_only: bool,
) -> Walk | None:
    """Walk ``bars[start:]`` (bars ending by ``end``) from a fill at ``entry``; None while the window
    is still open at ``now`` (or no bar covers it)."""
    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("entry equals stop")
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    remaining = HUNDRED
    hit = [False] * len(take_profits)
    legs: list[Leg] = []
    tp1_hit = False

    def leg(reason: str, price: Decimal, ts: datetime, pct: Decimal) -> Leg:
        return Leg(reason, price, ts, pct, _q(sign * (price - entry) / risk * pct / HUNDRED))

    last: Bar | None = None
    for k in range(start, len(bars)):
        bar = bars[k]
        if bar.end > end:
            break
        last = bar
        stop_hit = bar.l <= stop if side == "buy" else bar.h >= stop
        if stop_hit:
            gapped = bar.o <= stop if side == "buy" else bar.o >= stop
            legs.append(leg("stop", bar.o if gapped else stop, bar.end, remaining))
            return Walk(legs, tp1_hit)
        if k == start and first_bar_stop_only:
            continue
        for i, tp in enumerate(take_profits):
            if hit[i]:
                continue
            reached = bar.h >= tp.price if side == "buy" else bar.l <= tp.price
            if not reached:
                continue
            hit[i] = True
            if i == 0:
                tp1_hit = True
            pct = min(tp.pct, remaining)
            if pct > 0:
                legs.append(leg("take_profit", tp.price, bar.end, pct))
                remaining -= pct
        if remaining <= 0:
            return Walk(legs, tp1_hit)
    if now < end or last is None:
        return None
    legs.append(leg(end_reason, last.c, last.end, remaining))
    return Walk(legs, tp1_hit)


def simulate(
    spec: PaperSpec,
    bars: Sequence[Bar],
    *,
    entry_timeout: timedelta,
    max_hold: timedelta,
    fee_rate: Decimal,
    now: datetime,
) -> PaperResult | None:
    """The paper outcome of ``spec`` on ``bars`` (ascending, no gaps assumed); None = pending (a
    window still open at ``now``, or no candle in the entry window)."""
    after = [b for b in bars if b.ts >= spec.decided_at]
    fill_at: int | None = None
    entry: Decimal | None = None
    stop_only = False
    if spec.order_type == "market":
        if not after:
            return None
        fill_at, entry = 0, after[0].o
    else:
        if spec.limit_px is None:
            raise ValueError("a limit entry needs a limit price")
        px = spec.limit_px
        deadline = spec.decided_at + entry_timeout
        window = [b for b in after if b.ts < deadline]
        for k, bar in enumerate(window):
            through = bar.l < px if spec.side == "buy" else bar.h > px
            if through:
                at_open = bar.o <= px if spec.side == "buy" else bar.o >= px
                fill_at, entry, stop_only = k, bar.o if at_open else px, not at_open
                break
        if fill_at is None:
            if now < deadline or not window:
                return None
            return PaperResult(status="not_filled", exit_reason="not_filled")
    assert entry is not None
    fill_bar = after[fill_at]
    hold = timedelta(minutes=spec.time_stop_minutes) if spec.time_stop_minutes else max_hold
    walk = walk_exits(
        after,
        fill_at,
        side=spec.side,
        entry=entry,
        stop=spec.stop,
        take_profits=spec.take_profits,
        end=fill_bar.ts + hold,
        end_reason="time_stop" if spec.time_stop_minutes else "horizon",
        now=now,
        first_bar_stop_only=stop_only,
    )
    if walk is None:
        return None
    risk = abs(entry - spec.stop)
    r_gross = sum((x.r for x in walk.legs), Decimal(0))
    exit_value = sum((x.price * x.pct / HUNDRED for x in walk.legs), Decimal(0))
    fees_r = _q(fee_rate * (entry + exit_value) / risk)
    last = walk.legs[-1]
    final = "take_profit" if last.reason == "take_profit" else last.reason
    return PaperResult(
        status="filled",
        exit_reason=final,
        entry_px=entry,
        entry_ts=fill_bar.ts,
        exit_px=_q(exit_value),
        exit_ts=last.ts,
        r_gross=_q(r_gross),
        fees_r=fees_r,
        realised_r=_q(r_gross - fees_r),
        tp1_hit=walk.tp1_hit,
        legs=walk.legs,
    )


# -- proposal -> spec -------------------------------------------------------------------------------


def _dec(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def limit_price(p: Proposal) -> Decimal | None:
    """The Executor's limit: the entry, else the zone's high for a buy and low for a sell."""
    if p.entry is not None:
        return Decimal(p.entry)
    zone = p.entry_zone
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        values = [_dec(v) for v in zone]
        if any(v is None for v in values):
            return None
        low, high = sorted(v for v in values if v is not None)
        return high if p.side == "buy" else low
    return None


def take_profits(p: Proposal) -> list[TakeProfit]:
    out: list[TakeProfit] = []
    for tp in p.take_profits or []:
        if not isinstance(tp, Mapping):
            continue
        price, pct = _dec(tp.get("price")), _dec(tp.get("pct"))
        if price is not None and pct is not None and pct > 0:
            out.append(TakeProfit(price, pct))
    return out


def spec_for(p: Proposal) -> PaperSpec | None:
    if p.side not in ("buy", "sell") or p.stop is None or p.created_at is None:
        return None
    order_type = p.order_type or "market"
    px = None if order_type == "market" else limit_price(p)
    if order_type != "market" and px is None:
        return None
    return PaperSpec(
        side=p.side,
        order_type=order_type,
        limit_px=px,
        stop=Decimal(p.stop),
        take_profits=take_profits(p),
        time_stop_minutes=p.time_stop_minutes,
        decided_at=p.created_at,
    )


# -- reads ------------------------------------------------------------------------------------------


def load_bars(
    session: Session, env: str, inst_id: str, start: datetime, end: datetime, now: datetime
) -> tuple[list[Bar], str | None]:
    """Closed bars in [start, end) of the first of ``paper.bars`` that has any there."""
    for bar in paper_config()["bars"]:
        step = bar_delta(str(bar))
        df = load_candles(session, inst_id, str(bar), start - step, end, as_of=now, env=env).df
        if df.empty:
            continue
        rows = [
            Bar(
                ts=ts.to_pydatetime(),
                end=ts.to_pydatetime() + step,
                o=Decimal(str(o)),
                h=Decimal(str(h)),
                l=Decimal(str(lo)),
                c=Decimal(str(c)),
            )
            for ts, o, h, lo, c in zip(df.index, df["o"], df["h"], df["l"], df["c"], strict=True)
        ]
        return rows, str(bar)
    return [], None


def entry_timeout(session: Session, env: str) -> timedelta:
    """executor.entry_timeout_minutes from the env's settings row, else the default."""
    row = session.execute(text("SELECT data FROM settings WHERE env = :e"), {"e": env}).first()
    data = row[0] if row is not None and isinstance(row[0], dict) else {}
    block = data.get("executor") if isinstance(data.get("executor"), dict) else data
    value = block.get("entry_timeout_minutes") if isinstance(block, dict) else None
    minutes = int(value if value is not None else DEFAULTS["executor"]["entry_timeout_minutes"])
    return timedelta(minutes=minutes)


def fee_rate(session: Session, env: str, inst_id: str) -> Decimal:
    cfg = DEFAULTS["backtest"]
    inst_type = session.scalar(
        select(Instrument.inst_type).where(Instrument.env == env, Instrument.inst_id == inst_id)
    )
    rates: Mapping[str, Any] = cfg["taker_fee_rates"]
    if inst_type is not None and inst_type in rates:
        return Decimal(str(rates[inst_type]))
    return Decimal(str(cfg["fallback_taker_fee_rate"]))


def chain_ids(session: Session, head: Proposal) -> list[uuid.UUID]:
    """The head's id and every earlier version's."""
    ids = [head.id]
    parent = head.parent_id
    while parent is not None and parent not in ids:
        ids.append(parent)
        parent = session.scalar(select(Proposal.parent_id).where(Proposal.id == parent))
    return ids


def entry_filled(session: Session, ids: Sequence[uuid.UUID]) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    Order.proposal_id.in_(ids),
                    Order.purpose == "entry",
                    Order.origin == "atlas",
                    Order.filled_sz > 0,
                )
            )
        )
    )


def heads(
    session: Session, env: str, start: datetime, end: datetime, *, author: str | None = "strategist"
) -> list[Proposal]:
    """Latest versions of the entry proposals created in [start, end)."""
    child = aliased(Proposal)
    stmt = (
        select(Proposal)
        .where(
            Proposal.env == env,
            Proposal.kind == "entry",
            Proposal.created_at >= start,
            Proposal.created_at < end,
            ~exists().where(and_(child.parent_id == Proposal.id)),
        )
        .order_by(Proposal.created_at, Proposal.id)
    )
    if author is not None:
        stmt = stmt.where(Proposal.author == author)
    return list(session.scalars(stmt))


# -- writes -----------------------------------------------------------------------------------------


def upsert_paper_trade(
    session: Session, p: Proposal, result: PaperResult, *, fee: Decimal, bar: str | None, now: datetime
) -> None:
    values: dict[str, Any] = {
        "env": p.env,
        "proposal_id": p.id,
        "version": p.version,
        "inst_id": p.inst_id,
        "side": p.side,
        "order_type": p.order_type or "market",
        "proposal_status": p.status,
        "proposal_status_reason": p.status_reason,
        "status": result.status,
        "decided_at": p.created_at,
        "entry_px": result.entry_px,
        "entry_ts": result.entry_ts,
        "stop": p.stop,
        "exit_px": result.exit_px,
        "exit_ts": result.exit_ts,
        "exit_reason": result.exit_reason,
        "realised_r": result.realised_r,
        "r_gross": result.r_gross,
        "fees_r": result.fees_r,
        "fee_rate": fee,
        "tp1_hit": result.tp1_hit,
        "bar": bar,
        "legs": [x.as_json() for x in result.legs],
        "simulated_at": now,
        "updated_at": now,
    }
    stmt = insert(PaperTrade).values(id=uuid.uuid4(), created_at=now, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["proposal_id"],
        set_={k: stmt.excluded[k] for k in values if k != "proposal_id"},
    )
    session.execute(stmt)


def simulate_proposal(session: Session, p: Proposal, now: datetime) -> PaperResult | None:
    """Simulate one never-executed proposal and upsert its row; None = pending or unusable."""
    spec = spec_for(p)
    if spec is None:
        log.info("paper: proposal %s has no usable entry/stop; skipped", p.id)
        return None
    timeout = entry_timeout(session, p.env)
    max_hold = timedelta(hours=int(paper_config()["max_hold_hours"]))
    hold = timedelta(minutes=spec.time_stop_minutes) if spec.time_stop_minutes else max_hold
    window_end = spec.decided_at + timeout + hold + timedelta(days=1)
    bars, bar = load_bars(session, p.env, p.inst_id, spec.decided_at, window_end, now)
    fee = fee_rate(session, p.env, p.inst_id)
    try:
        result = simulate(spec, bars, entry_timeout=timeout, max_hold=max_hold, fee_rate=fee, now=now)
    except ValueError as exc:
        log.info("paper: proposal %s cannot be simulated (%s); skipped", p.id, exc)
        return None
    if result is None:
        return None
    upsert_paper_trade(session, p, result, fee=fee, bar=bar, now=now)
    return result


def ensure_paper_trades(
    session: Session, env: str, start: datetime, end: datetime, now: datetime
) -> dict[uuid.UUID, PaperResult]:
    """(Re)simulate every never-executed entry proposal (latest version) created in [start, end).
    Returns the resolved ones by proposal id; pending ones are left out (and not written)."""
    statuses = {str(s) for s in paper_config()["statuses"]}
    out: dict[uuid.UUID, PaperResult] = {}
    for p in heads(session, env, start, end, author=None):
        if p.status not in statuses or entry_filled(session, chain_ids(session, p)):
            continue
        result = simulate_proposal(session, p, now)
        if result is not None:
            out[p.id] = result
    return out


__all__ = [
    "Bar",
    "Leg",
    "PaperResult",
    "PaperSpec",
    "TakeProfit",
    "Walk",
    "chain_ids",
    "ensure_paper_trades",
    "entry_filled",
    "heads",
    "limit_price",
    "load_bars",
    "paper_config",
    "simulate",
    "simulate_proposal",
    "spec_for",
    "walk_exits",
]
