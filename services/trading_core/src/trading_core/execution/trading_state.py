"""Pause and resume trading (brief §10): ``trading_state.paused`` plus the ``system.trading_state`` event.

``resume`` clears every owner-clearable circuit breaker first; the self-clearing ``data_stale`` breaker
is left to the breaker monitor, so resuming on stale data keeps new orders blocked.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import TradingState
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.execution.config import SYSTEM_STREAM
from trading_core.execution.okx_access import publish
from trading_core.risk.breakers import SELF_CLEARING, Breakers
from trading_core.risk.context import ensure_trading_state

SOURCE = "service:trading_core.kill"


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _state_payload(row: TradingState, by: str, cleared: list[str] | None = None) -> dict[str, Any]:
    breakers = row.breakers if isinstance(row.breakers, dict) else {}
    return {
        "paused": bool(row.paused),
        "reason": row.reason,
        "paused_at": iso(row.paused_at) if row.paused_at else None,
        "by": by,
        "active_breakers": sorted(k for k, v in breakers.items() if isinstance(v, dict) and v.get("active")),
        "breakers_cleared": cleared or [],
    }


def set_paused(
    engine: Engine,
    env: str,
    paused: bool,
    *,
    reason: str | None,
    by: str = "owner",
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
    cleared: list[str] | None = None,
) -> dict[str, Any]:
    """Write ``trading_state.paused`` for ``env``, publish ``system.trading_state``, return its payload."""
    at = (now or (lambda: datetime.now(UTC)))()
    with Session(engine) as s, s.begin():
        ensure_trading_state(s, env, at)
        row = s.scalar(
            select(TradingState)
            .where(TradingState.env == env)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        row.paused = paused
        row.reason = reason if paused else None
        row.paused_at = at if paused else None
        row.updated_at = at
        payload = _state_payload(row, by, cleared)
    rds = redis_client if redis_client is not None else bus.make_client()
    publish(rds, SYSTEM_STREAM, "system.trading_state", env, payload, source=SOURCE)
    return payload


def pause(
    engine: Engine,
    env: str,
    *,
    reason: str | None = None,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return set_paused(
        engine, env, True, reason=reason or "Paused by the owner.", redis_client=redis_client, now=now
    )


def resume(
    engine: Engine,
    env: str,
    *,
    redis_client: redis.Redis | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Clear every owner-clearable breaker (all but the self-clearing ``data_stale``), then unpause."""
    rds = redis_client if redis_client is not None else bus.make_client()
    breakers = Breakers(engine, env, redis_client=rds, now=now)
    cleared = [
        n for n in sorted(breakers.active()) if n not in SELF_CLEARING and breakers.clear(n, by="owner")
    ]
    return set_paused(engine, env, False, reason=None, redis_client=rds, now=now, cleared=cleared)


__all__ = ["SOURCE", "iso", "pause", "resume", "set_paused"]
