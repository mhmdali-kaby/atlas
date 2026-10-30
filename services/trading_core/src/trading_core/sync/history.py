"""Closed positions and fills history sync (ATL-172, brief §13 History, decision #236).

Every ``sync.history_interval_seconds`` for the active env, if it has a verified OKX connection
(same guard as the account and bills syncs):

1. Page ``account/positions-history`` (last 3 months) with ``after`` = the oldest ``uTime`` of the
   previous page.
2. For each instType in ``sync.history_fill_inst_types``, page ``trade/fills-history`` (last 3 months)
   with ``after`` = the last ``billId`` of the previous page.
3. On later runs (a successful ``history`` sync exists) paging stops once a page reaches records older
   than the last success minus ``sync.history_overlap_seconds``; the upserts make the overlap free.
4. In one transaction: upsert fills on ``(env, inst_id, trade_id)`` (linking ``order_id`` by OKX
   ``ordId``), then upsert closed positions on ``(env, pos_id, closed_at)``, linking each to the Atlas
   entry order that opened it (and so to its proposal and agent), and mark ``sync_status`` (stream
   ``history``) successful and reconciled.

Any failure writes nothing but ``sync_status.last_error``. R is not stored: the Laravel API derives
it from the linked proposal's stop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas_shared.db import Fill, PositionClosed, SyncStatus
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert

from trading_core.okx import OkxClient
from trading_core.okx.models import PositionHistory
from trading_core.okx.trade import OkxTradeClient
from trading_core.okx.trade_models import FillDetail
from trading_core.sync.account import (
    NO_CONNECTION,
    AppKeyMissing,
    ClientFactory,
    _default_client,
    verified_connection,
)

logger = logging.getLogger("trading_core.sync.history")

STREAM = "history"
PAGE_LIMIT = 100  # OKX's maximum page size for positions-history and fills-history
INSERT_CHUNK = 1000
SIDES = frozenset({"long", "short"})
POS_SIDES = frozenset({"net", "long", "short"})
ORDER_SIDE = {"long": "buy", "short": "sell"}

_CFG = DEFAULTS["sync"]


@dataclass(frozen=True)
class HistorySnapshot:
    positions: list[PositionHistory]
    fills: list[FillDetail]


@dataclass(frozen=True)
class HistorySyncResult:
    env: str
    synced_at: datetime
    incremental: bool
    positions_fetched: int
    positions_new: int
    fills_fetched: int
    fills_new: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "synced_at": self.synced_at.isoformat().replace("+00:00", "Z"),
            "incremental": self.incremental,
            "positions_fetched": self.positions_fetched,
            "positions_new": self.positions_new,
            "fills_fetched": self.fills_fetched,
            "fills_new": self.fills_new,
        }


async def paginate_until[T](
    fetch: Callable[[str | None], Awaitable[list[T]]],
    cursor_of: Callable[[T], str],
    ts_of: Callable[[T], datetime | None],
    since: datetime | None,
    limit: int = PAGE_LIMIT,
) -> list[T]:
    """Newest-first paging: call ``fetch(after)`` until a page is short, the cursor stops moving, or
    (when ``since`` is set) the page reaches a record older than ``since``."""
    rows: list[T] = []
    after: str | None = None
    while True:
        page = await fetch(after)
        rows.extend(page)
        if len(page) < limit:
            return rows
        if since is not None:
            oldest = min((t for t in map(ts_of, page) if t is not None), default=None)
            if oldest is not None and oldest < since:
                return rows
        nxt = cursor_of(page[-1])
        if nxt == after:
            logger.warning("pagination cursor did not advance; stopping", extra={"after": after})
            return rows
        after = nxt


def _ms(dt: datetime | None) -> str:
    return "" if dt is None else str(int(dt.timestamp() * 1000))


async def fetch_history(
    okx: OkxClient, *, since: datetime | None, limit: int = PAGE_LIMIT
) -> HistorySnapshot:
    """All reads for one run. Raises on the first failure, so nothing partial is ever written."""
    positions = await paginate_until(
        lambda a: okx.get_positions_history(after=a, limit=limit),
        lambda p: _ms(p.u_time),
        lambda p: p.u_time,
        since,
        limit,
    )
    trade = OkxTradeClient(okx)
    fills: list[FillDetail] = []
    for inst_type in _CFG["history_fill_inst_types"]:

        async def page(after: str | None, it: str = str(inst_type)) -> list[FillDetail]:
            return await trade.get_fills_history(it, after=after, limit=limit)

        fills += await paginate_until(page, lambda f: f.bill_id or "", lambda f: f.ts, since, limit)
    return HistorySnapshot(positions=positions, fills=fills)


def _chunks(rows: Sequence[dict[str, Any]]) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), INSERT_CHUNK):
        yield rows[i : i + INSERT_CHUNK]


def fill_row(
    env: str, f: FillDetail, order_ids: dict[str, uuid.UUID], now: datetime
) -> dict[str, Any] | None:
    """None for rows the fills table cannot hold (no price/size, or a zero size)."""
    if f.fill_px is None or f.fill_sz is None or f.fill_sz <= 0 or f.side not in ("buy", "sell"):
        return None
    return {
        "id": uuid.uuid4(),
        "env": env,
        "order_id": order_ids.get(f.ord_id),
        "trade_id": f.trade_id,
        "ord_id": f.ord_id,
        "cl_ord_id": f.cl_ord_id,
        "bill_id": f.bill_id,
        "inst_id": f.inst_id,
        "side": f.side,
        "pos_side": f.pos_side if f.pos_side in POS_SIDES else None,
        "fill_px": f.fill_px,
        "fill_sz": f.fill_sz,
        "fee": f.fee,
        "fee_ccy": f.fee_ccy,
        "exec_type": f.exec_type,
        "fill_pnl": f.fill_pnl,
        "ts": f.ts,
        "raw": f.model_dump(mode="json", by_alias=True),
        "created_at": now,
        "updated_at": now,
    }


def position_side(p: PositionHistory) -> str | None:
    if p.direction in SIDES:
        return p.direction
    if p.pos_side in SIDES:
        return p.pos_side
    return None


_LINK_SQL = text(
    """
    SELECT o.proposal_id, p.author
    FROM orders o
    JOIN proposals p ON p.id = o.proposal_id
    JOIN fills f ON f.env = o.env AND (f.order_id = o.id OR (o.ord_id IS NOT NULL AND f.ord_id = o.ord_id))
    WHERE o.env = :env AND o.origin = 'atlas' AND o.purpose = 'entry'
      AND o.inst_id = :inst_id AND o.side = :side
    GROUP BY o.id, o.proposal_id, p.author
    HAVING MIN(f.ts) BETWEEN :lo AND :hi
    ORDER BY ABS(EXTRACT(EPOCH FROM (MIN(f.ts) - CAST(:opened AS timestamptz)))) ASC
    LIMIT 1
    """
)


def link_position(conn: Any, env: str, p: PositionHistory, side: str) -> tuple[uuid.UUID | None, str | None]:
    """(proposal_id, agent) of the Atlas entry order whose first fill opened this position: the one
    closest to ``cTime`` within [cTime - tolerance, uTime]. (None, None) for external positions."""
    if p.c_time is None or p.u_time is None:
        return None, None
    tolerance = timedelta(seconds=int(_CFG["history_link_tolerance_seconds"]))
    row = conn.execute(
        _LINK_SQL,
        {
            "env": env,
            "inst_id": p.inst_id,
            "side": ORDER_SIDE[side],
            "lo": p.c_time - tolerance,
            "hi": p.u_time,
            "opened": p.c_time,
        },
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


def position_row(env: str, p: PositionHistory, conn: Any, now: datetime) -> dict[str, Any] | None:
    side = position_side(p)
    size = p.close_total_pos if p.close_total_pos is not None else p.open_max_pos
    if side is None or p.u_time is None or size is None or p.pos_side not in POS_SIDES:
        logger.warning(
            "positions-history row skipped",
            extra={"pos_id": p.pos_id, "direction": p.direction, "pos_side": p.pos_side},
        )
        return None
    proposal_id, agent = link_position(conn, env, p, side)
    return {
        "id": uuid.uuid4(),
        "env": env,
        "pos_id": p.pos_id,
        "inst_id": p.inst_id,
        "inst_type": p.inst_type,
        "mgn_mode": p.mgn_mode,
        "pos_side": p.pos_side,
        "side": side,
        "close_type": p.type,
        "lever": p.lever,
        "ccy": p.ccy,
        "size": size,
        "entry_px": p.open_avg_px,
        "exit_px": p.close_avg_px,
        "realised_pnl": p.realized_pnl,
        "pnl": p.pnl,
        "fee": p.fee,
        "funding_fee": p.funding_fee,
        "liq_penalty": p.liq_penalty,
        "pnl_ratio": p.pnl_ratio,
        "opened_at": p.c_time,
        "closed_at": p.u_time,
        "proposal_id": proposal_id,
        "agent": agent,
        "origin": "atlas" if proposal_id is not None else "external",
        "raw": p.model_dump(mode="json", by_alias=True),
        "created_at": now,
        "updated_at": now,
    }


def _order_ids(conn: Any, env: str, ord_ids: set[str]) -> dict[str, uuid.UUID]:
    if not ord_ids:
        return {}
    rows = conn.execute(
        text("SELECT ord_id, id FROM orders WHERE env = :env AND ord_id = ANY(:ids)"),
        {"env": env, "ids": sorted(ord_ids)},
    ).all()
    return {r[0]: r[1] for r in rows}


def _upsert_fills(conn: Any, rows: list[dict[str, Any]], now: datetime) -> int:
    new = 0
    for chunk in _chunks(rows):
        stmt = insert(Fill).values(list(chunk))
        upsert = stmt.on_conflict_do_update(
            index_elements=[Fill.env, Fill.inst_id, Fill.trade_id],
            set_={
                "order_id": func.coalesce(Fill.order_id, stmt.excluded.order_id),
                "bill_id": func.coalesce(Fill.bill_id, stmt.excluded.bill_id),
                "fill_pnl": stmt.excluded.fill_pnl,
                "updated_at": now,
            },
        ).returning(text("(xmax = 0) AS inserted"))
        new += sum(1 for r in conn.execute(upsert).all() if r[0])
    return new


def _upsert_positions(conn: Any, rows: list[dict[str, Any]], now: datetime) -> int:
    new = 0
    for chunk in _chunks(rows):
        stmt = insert(PositionClosed).values(list(chunk))
        proposal = func.coalesce(PositionClosed.proposal_id, stmt.excluded.proposal_id)
        upsert = stmt.on_conflict_do_update(
            index_elements=[PositionClosed.env, PositionClosed.pos_id, PositionClosed.closed_at],
            set_={
                "size": stmt.excluded.size,
                "entry_px": stmt.excluded.entry_px,
                "exit_px": stmt.excluded.exit_px,
                "realised_pnl": stmt.excluded.realised_pnl,
                "pnl": stmt.excluded.pnl,
                "fee": stmt.excluded.fee,
                "funding_fee": stmt.excluded.funding_fee,
                "liq_penalty": stmt.excluded.liq_penalty,
                "pnl_ratio": stmt.excluded.pnl_ratio,
                "proposal_id": proposal,
                "agent": func.coalesce(PositionClosed.agent, stmt.excluded.agent),
                "origin": func.coalesce(func.nullif(PositionClosed.origin, "external"), stmt.excluded.origin),
                "raw": stmt.excluded.raw,
                "updated_at": now,
            },
        ).returning(text("(xmax = 0) AS inserted"))
        new += sum(1 for r in conn.execute(upsert).all() if r[0])
    return new


def write_history(
    engine: Engine, env: str, snap: HistorySnapshot, now: datetime, *, incremental: bool
) -> HistorySyncResult:
    """Store one run (fills, closed positions, sync_status) in a single transaction."""
    unique_fills = {(f.inst_id, f.trade_id): f for f in snap.fills}
    unique_positions = {(p.pos_id, p.u_time): p for p in snap.positions}
    with engine.begin() as conn:
        order_ids = _order_ids(conn, env, {f.ord_id for f in unique_fills.values()})
        fills = [r for f in unique_fills.values() if (r := fill_row(env, f, order_ids, now)) is not None]
        fills_new = _upsert_fills(conn, fills, now)
        # Fills first: linking a position to its entry order reads them.
        positions = [
            r for p in unique_positions.values() if (r := position_row(env, p, conn, now)) is not None
        ]
        positions_new = _upsert_positions(conn, positions, now)
        _write_status(conn, env, now, error=None)
    return HistorySyncResult(
        env=env,
        synced_at=now,
        incremental=incremental,
        positions_fetched=len(positions),
        positions_new=positions_new,
        fills_fetched=len(fills),
        fills_new=fills_new,
    )


def _write_status(conn: Any, env: str, now: datetime, *, error: str | None) -> None:
    values: dict[str, Any] = {
        "env": env,
        "stream": STREAM,
        "last_error": error,
        "created_at": now,
        "updated_at": now,
    }
    update_set: dict[str, Any] = {"last_error": error, "updated_at": now}
    if error is None:
        values |= {"last_success_at": now, "reconciled_at": now}
        update_set |= {"last_success_at": now, "reconciled_at": now}
    stmt = insert(SyncStatus).values(values)
    conn.execute(
        stmt.on_conflict_do_update(index_elements=[SyncStatus.env, SyncStatus.stream], set_=update_set)
    )


def record_failure(engine: Engine, env: str, error: str, now: datetime | None = None) -> None:
    with engine.begin() as conn:
        _write_status(conn, env, now or datetime.now(UTC), error=error[:2000])


def last_success(engine: Engine, env: str) -> datetime | None:
    with engine.connect() as conn:
        value: datetime | None = conn.scalar(
            select(SyncStatus.last_success_at).where(SyncStatus.env == env, SyncStatus.stream == STREAM)
        )
    return value


async def sync_history(
    env: str, *, okx: OkxClient, engine: Engine, now: Callable[[], datetime] | None = None
) -> HistorySyncResult:
    """Fetch and store closed positions and fills for ``env``. On failure, record it and re-raise."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        last = await asyncio.to_thread(last_success, engine, env)
        since = None if last is None else last - timedelta(seconds=int(_CFG["history_overlap_seconds"]))
        snap = await fetch_history(okx, since=since)
        result = await asyncio.to_thread(
            lambda: write_history(engine, env, snap, clock(), incremental=since is not None)
        )
    except Exception as exc:
        await _record(engine, env, exc)
        raise
    logger.info("history synced", extra=result.as_dict())
    return result


async def _record(engine: Engine, env: str, exc: BaseException) -> None:
    try:
        await asyncio.to_thread(record_failure, engine, env, f"{type(exc).__name__}: {exc}")
    except Exception:
        logger.exception("could not record history sync failure")


def history_job(
    env: str,
    engine: Engine,
    app_key: str,
    client_factory: ClientFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], Any]:
    """The scheduler job. Looks the verified connection up every run, like the bills sync."""
    factory = client_factory or _default_client

    async def run() -> dict[str, Any]:
        row = await asyncio.to_thread(verified_connection, engine, env)
        if row is None:
            logger.info("history sync skipped: no verified connection", extra={"env": env})
            return {"env": env, "skipped": True, "reason": NO_CONNECTION}
        try:
            if not app_key:
                raise AppKeyMissing("APP_KEY is not set; cannot decrypt the connection")
            client = factory(row, app_key)
        except Exception as exc:
            await _record(engine, env, exc)
            raise
        async with client as okx:
            result = await sync_history(env, okx=okx, engine=engine, now=now)
        return {**result.as_dict(), "skipped": False}

    return run
