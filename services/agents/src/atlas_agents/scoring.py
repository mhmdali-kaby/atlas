"""Daily agent scores written by code (ATL-110, brief §7.1 Scoring, §7.2 latency target, §9.3,
decision #247 (6)).

A schedule hook (``agents.schedule.scoring``, 00:15 Baghdad) calls :func:`score_days` for Market
Scout and News Intelligence. Each run (re)scores every Baghdad day in
``[today - agents.scoring.lookback_days, today - 1]``: one ``agent_scores`` row per agent, env and
day, upserted, so running twice leaves one row. A day that cannot be scored yet writes nothing and
is logged; the next run tries it again while it is inside the lookback.

**Market Scout** (:func:`score_scout_day`). The day's current watchlist (highest version) is
scored from its ``created_at`` (t0) over ``scout.outcome_hours`` (24h, t1 = t0 + 24h):

* move of an instrument = (close at t1 - close at t0) / ATR, where a close "at" an instant is the
  close of the last ``price_bar`` (1H) candle closed by it (refused when it closed more than
  ``max_price_lag_minutes`` earlier), and ATR is Wilder ATR(``quant.atr_window``) of the
  ``atr_bar`` (1D) candles closed by t0;
* picks = the version's items not removed by the owner; the comparison set = the version's
  ``data.shortlist`` minus every item on the version;
* ``selection_edge_atr`` = median |move| of the picks - median |move| of the comparison set (None
  when no comparison instrument has a move);
* ``directional_accuracy`` (also the row's ``hit_rate``) = the share of long/short picks whose move
  has the bias's sign (neutral picks excluded; a zero move is a miss); ``n`` = picks with a move.

Skipped (nothing written) when there is no watchlist, it is empty, ``now < t1`` (the window has not
closed) or no pick has a move.

**News Intelligence** (:func:`score_news_day`). For each ``news_alert`` War Room message stored on
the Baghdad day, the first News Intelligence reply to it (``reply_to``) and the alert's news item
(``card.news_item_id``): latency = reply ``created_at`` - item ``fetched_at`` in seconds. The row
holds the median and p90 (linear interpolation, numpy's default) of the answered alerts, ``n`` =
answered alerts, ``hit_rate`` = the share within ``news.latency_target_seconds`` (90). Skipped when
no alert of that day was answered.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from atlas_quant import compute_stats, load_candles
from atlas_quant.grid import bar_delta
from atlas_shared.db import AgentScore, NewsItem, Watchlist, WatchlistItem, owner_day
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

SCOUT: Final = "market_scout"
NEWS: Final = "news_intelligence"
JOB: Final = "scoring"


def scoring_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["scoring"]
    return config


# -- pure maths -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PickMove:
    inst_id: str
    bias: str
    move_atr: float
    """Signed move over the outcome window in ATR units."""


def _median(values: Sequence[float]) -> float | None:
    return float(np.median(values)) if values else None


def selection_edge(picks: Sequence[PickMove], others: Sequence[float]) -> dict[str, Any]:
    """Scout's day score from the picks' signed moves and the comparison set's signed moves."""
    pick_abs = [abs(p.move_atr) for p in picks]
    other_abs = [abs(m) for m in others]
    median_picks = _median(pick_abs)
    median_others = _median(other_abs)
    edge = None if median_picks is None or median_others is None else median_picks - median_others
    directional = [p for p in picks if p.bias in ("long", "short")]
    correct = [p for p in directional if (p.move_atr > 0 if p.bias == "long" else p.move_atr < 0)]
    accuracy = len(correct) / len(directional) if directional else None
    return {
        "selection_edge_atr": edge,
        "median_pick_move_atr": median_picks,
        "median_shortlist_move_atr": median_others,
        "directional_accuracy": accuracy,
        "directional_n": len(directional),
        "directional_correct": len(correct),
        "comparison_n": len(other_abs),
    }


def latency_stats(latencies: Sequence[float], target_seconds: float) -> dict[str, Any]:
    """Median and p90 (linear interpolation) of ``latencies`` in seconds, and the share within
    ``target_seconds``."""
    if not latencies:
        return {"latency_median_s": None, "latency_p90_s": None, "within_target_share": None}
    arr = np.asarray(latencies, dtype="float64")
    return {
        "latency_median_s": float(np.median(arr)),
        "latency_p90_s": float(np.percentile(arr, 90)),
        "within_target_share": float(np.mean(arr <= target_seconds)),
    }


# -- reads ------------------------------------------------------------------------------------------


def _close_at(session: Session, env: str, inst_id: str, at: datetime) -> tuple[float | None, str | None]:
    cfg = scoring_config()["scout"]
    bar = str(cfg["price_bar"])
    lag = timedelta(minutes=int(cfg["max_price_lag_minutes"]))
    candles = load_candles(session, inst_id, bar, at - lag - timedelta(days=1), at, as_of=at, env=env)
    df = candles.df
    if df.empty:
        return None, f"no {bar} candle closed by {at.isoformat()}"
    last_ts = pd.Timestamp(df.index[-1]).to_pydatetime()
    if last_ts + bar_delta(bar) < at - lag:
        return None, f"last {bar} candle before {at.isoformat()} closed more than {lag} earlier"
    return float(df["c"].iloc[-1]), None


def _atr_at(session: Session, env: str, inst_id: str, at: datetime) -> tuple[float | None, str | None]:
    cfg = scoring_config()["scout"]
    bar = str(cfg["atr_bar"])
    start = at - bar_delta(bar) * int(cfg["atr_bars"])
    df = load_candles(session, inst_id, bar, start, at, as_of=at, env=env).df
    if df.empty:
        return None, f"no {bar} candles closed by {at.isoformat()}"
    atr = compute_stats(df, bar).atr
    if atr is None or not atr > 0:
        return None, f"no ATR on {len(df)} {bar} candles"
    return float(atr), None


def instrument_move(
    session: Session, env: str, inst_id: str, start: datetime, end: datetime
) -> tuple[dict[str, Any] | None, str | None]:
    """{p0, p1, atr, move_atr} for ``inst_id`` over [start, end], or (None, reason)."""
    p0, why = _close_at(session, env, inst_id, start)
    if p0 is None:
        return None, why
    p1, why = _close_at(session, env, inst_id, end)
    if p1 is None:
        return None, why
    atr, why = _atr_at(session, env, inst_id, start)
    if atr is None:
        return None, why
    return {"p0": p0, "p1": p1, "atr": atr, "move_atr": (p1 - p0) / atr}, None


def _current_watchlist(session: Session, env: str, day: date) -> Watchlist | None:
    return session.scalars(
        select(Watchlist)
        .where(Watchlist.env == env, Watchlist.date == day)
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).first()


# -- writes -----------------------------------------------------------------------------------------


def _dec(value: float | None, places: int) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, places)))


def upsert_score(
    session: Session,
    *,
    agent: str,
    env: str,
    day: date,
    n: int,
    hit_rate: float | None,
    data: Mapping[str, Any],
    now: datetime,
) -> None:
    """One row per (agent, env, day): insert, or overwrite n/hit_rate/data on a re-score."""
    stmt = insert(AgentScore).values(
        id=uuid.uuid4(),
        agent=agent,
        env=env,
        day=day,
        n=n,
        hit_rate=_dec(hit_rate, 4),
        data=dict(data),
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["agent", "env", "day"],
        set_={
            "n": stmt.excluded.n,
            "hit_rate": stmt.excluded.hit_rate,
            "data": stmt.excluded.data,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)


# -- per agent --------------------------------------------------------------------------------------


def score_scout_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score Market Scout's watchlist of Baghdad ``day``; returns the row's data, or None (skipped,
    logged)."""
    now = ctx.now()
    hours = int(scoring_config()["scout"]["outcome_hours"])
    with ctx.session_factory() as session:
        wl = _current_watchlist(session, ctx.env, day)
        if wl is None:
            log.info("scoring: no market_scout watchlist for %s (%s); skipped", day, ctx.env)
            return None
        if wl.status == "empty" or wl.created_at is None:
            log.info("scoring: market_scout watchlist for %s is %s; skipped", day, wl.status)
            return None
        start = wl.created_at
        end = start + timedelta(hours=hours)
        if now < end:
            log.info("scoring: market_scout %s outcome window closes at %s; skipped", day, end.isoformat())
            return None
        items = session.scalars(
            select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id).order_by(WatchlistItem.rank)
        ).all()
        on_list = {i.inst_id for i in items}
        shortlist = [str(s) for s in (wl.data or {}).get("shortlist") or []]
        picks: list[PickMove] = []
        pick_rows: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for item in items:
            if item.removed_by_owner_at is not None:
                continue
            move, why = instrument_move(session, ctx.env, item.inst_id, start, end)
            if move is None:
                skipped.append({"inst_id": item.inst_id, "reason": str(why)})
                continue
            picks.append(PickMove(item.inst_id, item.bias, move["move_atr"]))
            pick_rows.append({"inst_id": item.inst_id, "bias": item.bias, **move})
        others: list[float] = []
        for inst_id in shortlist:
            if inst_id in on_list:
                continue
            move, why = instrument_move(session, ctx.env, inst_id, start, end)
            if move is None:
                skipped.append({"inst_id": inst_id, "reason": str(why)})
                continue
            others.append(move["move_atr"])
        if not picks:
            log.info("scoring: market_scout %s has no pick with price data (%s); skipped", day, skipped)
            return None
        stats = selection_edge(picks, others)
        data = {
            **stats,
            "watchlist_id": str(wl.id),
            "version": wl.version,
            "status": wl.status,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "picks": pick_rows,
            "skipped": skipped,
            "metrics": {
                "selection_edge_atr": stats["selection_edge_atr"],
                "directional_accuracy": stats["directional_accuracy"],
            },
        }
        upsert_score(
            session,
            agent=SCOUT,
            env=ctx.env,
            day=day,
            n=len(picks),
            hit_rate=stats["directional_accuracy"],
            data=data,
            now=now,
        )
        session.commit()
    return data


_ALERT_REPLIES = text(
    "SELECT a.id AS alert_id, a.card->>'news_item_id' AS news_item_id, "
    "  (SELECT min(r.created_at) FROM warroom_messages r "
    "    WHERE r.reply_to = a.id AND r.author = :agent AND r.author_type = 'agent') AS replied_at "
    "FROM warroom_messages a "
    "WHERE a.env = :env AND a.kind = 'news_alert' AND a.created_at >= :start AND a.created_at < :end "
    "ORDER BY a.created_at"
)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(str(DEFAULTS["timezone"]["display"]))
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    return start, start + timedelta(days=1)


def score_news_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score News Intelligence's ingest-to-alert latency on Baghdad ``day``; the row's data, or
    None (skipped, logged)."""
    now = ctx.now()
    start, end = _day_bounds(day)
    if now < end:
        log.info("scoring: news_intelligence %s has not ended; skipped", day)
        return None
    target = float(scoring_config()["news"]["latency_target_seconds"])
    with ctx.session_factory() as session:
        rows = session.execute(
            _ALERT_REPLIES, {"env": ctx.env, "agent": NEWS, "start": start, "end": end}
        ).all()
        alerts: list[dict[str, Any]] = []
        unanswered: list[str] = []
        latencies: list[float] = []
        for alert_id, item_id, replied_at in rows:
            if replied_at is None or item_id is None:
                unanswered.append(str(alert_id))
                continue
            try:
                item_uuid = uuid.UUID(str(item_id))
            except ValueError:
                unanswered.append(str(alert_id))
                continue
            fetched_at = session.scalar(select(NewsItem.fetched_at).where(NewsItem.id == item_uuid))
            if fetched_at is None:
                unanswered.append(str(alert_id))
                continue
            latency = (replied_at - fetched_at).total_seconds()
            latencies.append(latency)
            alerts.append({"alert_id": str(alert_id), "news_item_id": str(item_uuid), "latency_s": latency})
        if not latencies:
            log.info(
                "scoring: news_intelligence %s has no answered alert (%d alerts); skipped", day, len(rows)
            )
            return None
        stats = latency_stats(latencies, target)
        data = {
            **stats,
            "latency_target_s": target,
            "alerts": alerts,
            "unanswered_alert_ids": unanswered,
            "metrics": {
                "latency_median_s": stats["latency_median_s"],
                "latency_p90_s": stats["latency_p90_s"],
            },
        }
        upsert_score(
            session,
            agent=NEWS,
            env=ctx.env,
            day=day,
            n=len(latencies),
            hit_rate=stats["within_target_share"],
            data=data,
            now=now,
        )
        session.commit()
    return data


SCORERS: Final = {SCOUT: score_scout_day, NEWS: score_news_day}


def score_days(ctx: ToolContext, agent: str, today: date | None = None) -> dict[date, dict[str, Any] | None]:
    """(Re)score ``agent`` for every Baghdad day in [today - lookback_days, today - 1]."""
    scorer = SCORERS[agent]
    current = today or owner_day(ctx.now())
    lookback = int(scoring_config()["lookback_days"])
    out: dict[date, dict[str, Any] | None] = {}
    for back in range(lookback, 0, -1):
        day = current - timedelta(days=back)
        try:
            out[day] = scorer(ctx, day)
        except Exception:  # one bad day must not stop the rest
            log.exception("scoring: %s for %s failed", agent, day)
            out[day] = None
    return out


__all__ = [
    "JOB",
    "NEWS",
    "SCORERS",
    "SCOUT",
    "PickMove",
    "instrument_move",
    "latency_stats",
    "score_days",
    "score_news_day",
    "score_scout_day",
    "scoring_config",
    "selection_edge",
    "upsert_score",
]
