"""A story cluster's status and price move since first seen, computed in code (ATL-106).

Decision #247 (4), brief §7.2. The model cannot relabel either value:

* **status** — from the ``reputable`` / ``official`` flags of the distinct ``news_sources`` keys
  that carried the story (items known by ``ctx.now()``): CONFIRMED when any source is official or
  2+ distinct sources are reputable; REPORTED when exactly one is reputable; else RUMOUR. A source
  key with no ``news_sources`` row is trusted as neither. ``status_sources`` names the sources
  that decided it (the official ones, the reputable ones, or for a RUMOUR every source carrying it).
* **price_move_since_first_seen_atr** — per linked instrument, via
  :func:`atlas_quant.move_since_atr`: (price now - close of the last ``bar`` closed by first_seen) /
  Wilder ATR(``quant.atr_window``) at first_seen. Price now is the
  ``agents.market_tools.mark_price_bar`` proxy (newest closed 1m close by ``ctx.now()``). Null with
  a reason when a candle is missing.

Replay: items are bounded like every news tool (``fetched_at`` and ``published_at`` <=
``ctx.now()``), the reference bar and ATR use only bars closed by first_seen, and price now only
bars closed by ``ctx.now()``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from atlas_quant import load_candles, move_since_atr
from atlas_quant.grid import bar_delta
from atlas_shared.db import Candle, NewsItem, NewsSource
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from atlas_agents.tools.framework import ToolContext

NewsStatus = Literal["CONFIRMED", "REPORTED", "RUMOUR"]
StatusRule = Literal[
    "official_source", "two_reputable_sources", "one_reputable_source", "no_reputable_source"
]


class StatusDecision(BaseModel):
    status: NewsStatus
    rule: StatusRule
    decided_by: list[str]
    """The source keys that decided the status."""


class PriceMoveOut(BaseModel):
    inst_id: str
    value: float | None
    """Signed move since first seen in ATR units; null when it cannot be computed (see reason)."""
    reason: str | None = None
    ref_ts: datetime | None = None
    """Open time of the reference bar (the last bar closed by first_seen)."""
    ref_close: float | None = None
    atr: float | None = None
    price_now: float | None = None
    price_ts: datetime | None = None
    """Close time of the candle price_now comes from."""


class ClusterStatusOut(BaseModel):
    cluster_id: str
    status: NewsStatus
    status_rule: StatusRule
    status_sources: list[str]
    sources: list[str]
    """Every distinct source key that carried the story by now."""
    first_seen: datetime
    price_move_since_first_seen_atr: list[PriceMoveOut]


def decide_status(sources: Iterable[str], trust: Mapping[str, tuple[bool, bool]]) -> StatusDecision:
    """``trust``: source key -> (reputable, official). Unknown keys are neither."""
    distinct = sorted(set(sources))
    official = [s for s in distinct if trust.get(s, (False, False))[1]]
    if official:
        return StatusDecision(status="CONFIRMED", rule="official_source", decided_by=official)
    reputable = [s for s in distinct if trust.get(s, (False, False))[0]]
    if len(reputable) >= 2:
        return StatusDecision(status="CONFIRMED", rule="two_reputable_sources", decided_by=reputable)
    if len(reputable) == 1:
        return StatusDecision(status="REPORTED", rule="one_reputable_source", decided_by=reputable)
    return StatusDecision(status="RUMOUR", rule="no_reputable_source", decided_by=distinct)


def _move_config() -> Mapping[str, Any]:
    config: Mapping[str, Any] = DEFAULTS["agents"]["news_price_move"]
    return config


def price_move_since(session: Session, ctx: ToolContext, inst_id: str, first_seen: datetime) -> PriceMoveOut:
    """price_move_since_first_seen_atr for one instrument (see the module docstring)."""
    conf = _move_config()
    bar = str(conf["bar"])
    step = bar_delta(bar)
    mark_bar = str(DEFAULTS["agents"]["market_tools"]["mark_price_bar"])
    mark_step = bar_delta(mark_bar)
    now = ctx.now()
    latest = session.scalars(
        select(Candle)
        .where(
            and_(
                Candle.env == ctx.env,
                Candle.inst_id == inst_id,
                Candle.bar == mark_bar,
                Candle.confirmed.is_(True),
                Candle.ts <= now - mark_step,
                ctx.bound(Candle.ts),
            )
        )
        .order_by(Candle.ts.desc())
        .limit(1)
    ).first()
    if latest is None:
        return PriceMoveOut(
            inst_id=inst_id, value=None, reason=f"no closed {mark_bar} candle for {inst_id} to price it now"
        )
    price_now = float(latest.c)
    price_ts = latest.ts + mark_step
    as_of = min(first_seen, now)
    history_bars = int(conf["history_bars"])
    candles = load_candles(
        session,
        inst_id,
        bar,
        start=as_of - step * (history_bars + 1),
        end=as_of,
        as_of=as_of,
        env=ctx.env,
    )
    move = move_since_atr(candles.df, as_of, price_now, bar=bar)
    return PriceMoveOut(
        inst_id=inst_id,
        value=move.value,
        reason=move.reason,
        ref_ts=move.ref_ts,
        ref_close=move.ref_close,
        atr=move.atr,
        price_now=price_now,
        price_ts=price_ts,
    )


def cluster_statuses(ctx: ToolContext, cluster_ids: Sequence[str]) -> dict[str, ClusterStatusOut]:
    """cluster id -> its code-computed status and price moves, from the items known by
    ``ctx.now()``. Ids with no such item (or not UUIDs) are absent from the result."""
    wanted: list[uuid.UUID] = []
    for raw in dict.fromkeys(cluster_ids):
        try:
            wanted.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    if not wanted:
        return {}
    with ctx.session_factory() as session:
        rows = session.execute(
            select(NewsItem.cluster_id, NewsItem.source, NewsItem.published_at, NewsItem.instruments).where(
                NewsItem.cluster_id.in_(wanted),
                ctx.bound(NewsItem.fetched_at),
                ctx.bound(NewsItem.published_at),
            )
        ).all()
        grouped: dict[str, list[tuple[str, datetime, list[str]]]] = {}
        for cluster_id, source, published_at, instruments in rows:
            grouped.setdefault(str(cluster_id), []).append((source, published_at, list(instruments or [])))
        keys = sorted({source for items in grouped.values() for source, _p, _i in items})
        trust = {
            key: (bool(reputable), bool(official))
            for key, reputable, official in session.execute(
                select(NewsSource.key, NewsSource.reputable, NewsSource.official).where(
                    NewsSource.key.in_(keys)
                )
            ).all()
        }
        out: dict[str, ClusterStatusOut] = {}
        for cid, items in grouped.items():
            decision = decide_status((source for source, _p, _i in items), trust)
            first_seen = min(published for _s, published, _i in items)
            instruments = sorted({inst for _s, _p, insts in items for inst in insts})
            out[cid] = ClusterStatusOut(
                cluster_id=cid,
                status=decision.status,
                status_rule=decision.rule,
                status_sources=decision.decided_by,
                sources=sorted({source for source, _p, _i in items}),
                first_seen=first_seen,
                price_move_since_first_seen_atr=[
                    price_move_since(session, ctx, inst, first_seen) for inst in instruments
                ],
            )
    return out


__all__ = [
    "ClusterStatusOut",
    "NewsStatus",
    "PriceMoveOut",
    "StatusDecision",
    "StatusRule",
    "cluster_statuses",
    "decide_status",
    "price_move_since",
]
