"""Which instruments the market feed subscribes to (ATL-125).

``market_ws.always_subscribe`` (BTC-USDT-SWAP) + the picks on today's (Asia/Baghdad) watchlist that
the owner has not removed + every instrument with an open position at the last account sync.
:func:`watchlist_instruments` and :func:`held_instruments` are the two halves; the circuit breakers
(ATL-145) use them for "a held or watchlist instrument".
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from atlas_shared.db import BalanceSnapshot, PositionSnapshot, Watchlist, WatchlistItem
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Connection, Engine, select


def owner_day(now: datetime) -> datetime:
    return now.astimezone(ZoneInfo(DEFAULTS["timezone"]["display"]))


def always_subscribe() -> set[str]:
    return {str(i) for i in DEFAULTS["market_ws"]["always_subscribe"]}


def watchlist_instruments(conn: Connection, env: str, now: datetime) -> set[str]:
    """Picks on today's (Asia/Baghdad) newest watchlist version that the owner has not removed."""
    wl_id = conn.execute(
        select(Watchlist.id)
        .where(Watchlist.env == env, Watchlist.date == owner_day(now).date())
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).scalar()
    if wl_id is None:
        return set()
    return set(
        conn.execute(
            select(WatchlistItem.inst_id).where(
                WatchlistItem.watchlist_id == wl_id, WatchlistItem.removed_by_owner_at.is_(None)
            )
        ).scalars()
    )


def held_instruments(conn: Connection, env: str) -> set[str]:
    """Instruments with a non-zero position at the newest account sync tick."""
    # The account sync writes every position of one tick with the same taken_at; a tick with no
    # positions writes only balances, so the newest balances tick says "no positions now".
    taken_at = conn.execute(
        select(BalanceSnapshot.taken_at)
        .where(BalanceSnapshot.env == env, BalanceSnapshot.account == "trading")
        .order_by(BalanceSnapshot.taken_at.desc())
        .limit(1)
    ).scalar()
    if taken_at is None:
        taken_at = conn.execute(
            select(PositionSnapshot.taken_at)
            .where(PositionSnapshot.env == env)
            .order_by(PositionSnapshot.taken_at.desc())
            .limit(1)
        ).scalar()
    if taken_at is None:
        return set()
    return set(
        conn.execute(
            select(PositionSnapshot.inst_id).where(
                PositionSnapshot.env == env,
                PositionSnapshot.taken_at == taken_at,
                PositionSnapshot.pos != 0,
            )
        ).scalars()
    )


def desired_instruments(engine: Engine, env: str, now: datetime | None = None) -> set[str]:
    at = now or datetime.now(UTC)
    wanted = always_subscribe()
    with engine.connect() as conn:
        wanted.update(watchlist_instruments(conn, env, at))
        wanted.update(held_instruments(conn, env))
    return wanted


__all__ = ["always_subscribe", "desired_instruments", "held_instruments", "watchlist_instruments"]
