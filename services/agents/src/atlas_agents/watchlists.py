"""Writing Market Scout's watchlists (ATL-102, decision #247 (3), brief §5, §7.1).

:func:`save_watchlist` is the one writer of ``watchlists`` + ``watchlist_items``. It validates every
pick against :class:`~atlas_agents.schemas.WatchlistPick` before it touches the database, so an
invalid pick writes nothing, and it writes both tables inside one savepoint, so a database error
leaves neither. Each call adds a new version for (env, date): the current watchlist is the highest
version (``atlas_data.get_watchlist`` reads it). :func:`current_watchlist` + :func:`listed_ids` are
the shared readers of that current version for the agents (owner-removed items left out).

``date`` is the Asia/Baghdad business day (:func:`baghdad_today`), never a UTC date.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal, get_args

from atlas_shared.db import Watchlist, WatchlistItem, owner_day
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from atlas_agents.schemas import WatchlistPick

if TYPE_CHECKING:
    from atlas_agents.tools import ToolContext

WatchlistStatus = Literal["draft", "published", "partial_evidence", "empty"]
STATUSES: Final[frozenset[str]] = frozenset(get_args(WatchlistStatus))
ENVS: Final[frozenset[str]] = frozenset({"demo", "live"})


class WatchlistError(ValueError):
    """A watchlist that save_watchlist refuses to write (bad env, date, status or pick set)."""


baghdad_today = owner_day
"""The Asia/Baghdad date of an instant: a re-export of :func:`atlas_shared.db.owner_day`."""


def _validate(
    env: str, day: date, picks: Sequence[WatchlistPick | Mapping[str, Any]], status: str
) -> list[WatchlistPick]:
    if env not in ENVS:
        raise WatchlistError(f"env must be one of {sorted(ENVS)}, got {env!r}")
    # datetime is a subclass of date; a timestamp here is exactly the bug a DATE column prevents.
    if isinstance(day, datetime) or not isinstance(day, date):
        raise WatchlistError("date must be a datetime.date (the Asia/Baghdad day), not a timestamp")
    if status not in STATUSES:
        raise WatchlistError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
    # Raises pydantic.ValidationError (a ValueError) on the first invalid pick, before any write.
    valid = [WatchlistPick.model_validate(p) for p in picks]
    ranks = [p.rank for p in valid]
    inst_ids = [p.inst_id for p in valid]
    if len(set(ranks)) != len(ranks):
        raise WatchlistError(f"pick ranks must be unique, got {ranks}")
    if len(set(inst_ids)) != len(inst_ids):
        raise WatchlistError(f"pick instruments must be unique, got {inst_ids}")
    if status == "empty" and valid:
        raise WatchlistError("status 'empty' must have no picks")
    if status != "empty" and not valid:
        raise WatchlistError(f"status {status!r} needs at least one pick; use 'empty' with a reason")
    return valid


def save_watchlist(
    session: Session,
    env: str,
    date: date,
    picks: Sequence[WatchlistPick | Mapping[str, Any]],
    status: WatchlistStatus,
    *,
    created_by: str = "market_scout",
    reason: str | None = None,
    edit_window_ends_at: datetime | None = None,
    message_id: uuid.UUID | None = None,
    data: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
) -> Watchlist:
    """Validate ``picks`` and write the next version of (env, date)'s watchlist with its items.

    Nothing is written when validation fails (pydantic.ValidationError or WatchlistError). Both
    tables are written in one savepoint; the call flushes but does not commit, so the caller's
    commit makes the watchlist visible. ``created_at`` defaults to now (UTC); evals seeding a past
    day pass the instant it was known, which is what replay reads are bounded on."""
    valid = _validate(env, date, picks, status)
    now = created_at or datetime.now(UTC)
    if now.tzinfo is None:
        raise WatchlistError("created_at must be timezone-aware")
    with session.begin_nested():
        # Serialise writers of one (env, date) so two re-runs cannot pick the same version.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"watchlists:{env}:{date.isoformat()}"},
        )
        latest = session.scalar(
            select(func.max(Watchlist.version)).where(Watchlist.env == env, Watchlist.date == date)
        )
        watchlist = Watchlist(
            id=uuid.uuid4(),
            env=env,
            date=date,
            version=(latest or 0) + 1,
            created_by=created_by,
            status=status,
            edit_window_ends_at=edit_window_ends_at,
            reason=reason,
            message_id=message_id,
            data=dict(data or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(watchlist)
        session.flush()
        for pick in valid:
            session.add(
                WatchlistItem(
                    watchlist_id=watchlist.id,
                    inst_id=pick.inst_id,
                    rank=pick.rank,
                    asset_class=pick.asset_class,
                    bias=pick.bias,
                    conviction=Decimal(str(pick.conviction)),
                    thesis=pick.thesis,
                    catalysts=[c.model_dump(mode="json") for c in pick.catalysts],
                    key_levels=dict(pick.key_levels),
                    invalidation=pick.invalidation,
                    pinned_by_owner=False,
                    data_as_of=pick.data_as_of,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
    return watchlist


def current_watchlist(session: Session, ctx: ToolContext, day: date) -> Watchlist | None:
    """The latest version of ``day``'s watchlist in ``ctx.env`` saved within ``ctx.bound``."""
    return session.scalars(
        select(Watchlist)
        .where(Watchlist.env == ctx.env, Watchlist.date == day, ctx.bound(Watchlist.created_at))
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).first()


def listed_ids(session: Session, watchlist: Watchlist | None) -> list[str]:
    """Instruments on ``watchlist`` that the owner has not removed, in rank order."""
    if watchlist is None:
        return []
    items = session.scalars(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == watchlist.id, WatchlistItem.removed_by_owner_at.is_(None))
        .order_by(WatchlistItem.rank)
    ).all()
    return [i.inst_id for i in items]


__all__ = [
    "STATUSES",
    "WatchlistError",
    "WatchlistStatus",
    "baghdad_today",
    "current_watchlist",
    "listed_ids",
    "save_watchlist",
]
