"""agent_states writes (ATL-78 table): one row per (agent, env), upserted."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal

from atlas_shared.db import AgentState
from sqlalchemy import Table, case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

State = Literal["idle", "thinking", "waiting", "degraded", "rate_limited", "paused", "disabled"]
STATES: Final[tuple[State, ...]] = (
    "idle",
    "thinking",
    "waiting",
    "degraded",
    "rate_limited",
    "paused",
    "disabled",
)


def get_state(session: Session, *, agent: str, env: str) -> AgentState | None:
    return session.scalars(
        select(AgentState)
        .where(AgentState.agent == agent, AgentState.env == env)
        .execution_options(populate_existing=True)
    ).one_or_none()


def set_state(
    session: Session,
    *,
    agent: str,
    env: str,
    state: State,
    current_task: str | None = None,
    last_error: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Upsert the (agent, env) row and return the state it had before (None if it had no row).

    ``state_since`` moves only when the state actually changes. Flushes, does not commit."""
    if state not in STATES:
        raise ValueError(f"Unknown agent state [{state}]")
    at = now or datetime.now(UTC)
    existing = get_state(session, agent=agent, env=env)
    previous = existing.state if existing is not None else None

    table: Table = AgentState.__table__  # type: ignore[assignment]
    stmt = insert(table).values(
        agent=agent,
        env=env,
        state=state,
        current_task=current_task,
        last_error=last_error,
        state_since=at,
        created_at=at,
        updated_at=at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.agent, table.c.env],
        set_={
            "state": stmt.excluded.state,
            "current_task": stmt.excluded.current_task,
            "last_error": stmt.excluded.last_error,
            "state_since": case(
                (table.c.state != stmt.excluded.state, stmt.excluded.state_since),
                else_=table.c.state_since,
            ),
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)
    return previous
