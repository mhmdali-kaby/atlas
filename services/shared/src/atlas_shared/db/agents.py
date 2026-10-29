"""Writes for the agent runtime tables (ATL-78) that need more than ``session.add``."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_shared.db.models import AgentUsageDaily
from atlas_shared.defaults import DEFAULTS


def usage_day(at: datetime) -> date:
    """The owner-timezone (Asia/Baghdad) calendar date a usage timestamp counts against."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(ZoneInfo(str(DEFAULTS["timezone"]["display"]))).date()


def add_usage(
    session: Session,
    *,
    agent: str,
    env: str,
    day: date,
    tokens_in: int = 0,
    tokens_out: int = 0,
    turns: int = 0,
    runs: int = 0,
) -> AgentUsageDaily:
    """Add to the (agent, env, day) usage row, creating it on first use, in one statement.

    Safe under concurrent writers: ON CONFLICT (agent, env, day) DO UPDATE adds to the stored
    counters instead of overwriting them. Flushes but does not commit."""
    table: Table = AgentUsageDaily.__table__  # type: ignore[assignment]
    now = datetime.now(UTC)
    stmt = insert(table).values(
        agent=agent,
        env=env,
        day=day,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=turns,
        runs=runs,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.agent, table.c.env, table.c.day],
        set_={
            "tokens_in": table.c.tokens_in + stmt.excluded.tokens_in,
            "tokens_out": table.c.tokens_out + stmt.excluded.tokens_out,
            "turns": table.c.turns + stmt.excluded.turns,
            "runs": table.c.runs + stmt.excluded.runs,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)
    return session.scalars(
        select(AgentUsageDaily)
        .where(AgentUsageDaily.agent == agent, AgentUsageDaily.env == env, AgentUsageDaily.day == day)
        .execution_options(populate_existing=True)
    ).one()
