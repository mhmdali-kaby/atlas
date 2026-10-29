"""Per-agent daily budgets in tokens and turns (decision #242: never USD).

A day is the Asia/Baghdad calendar day (``usage_day``). Tokens are ``tokens_in + tokens_out`` from
``agent_usage_daily``; tokens_in already includes cache reads and writes (see session.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from atlas_shared.db import AgentUsageDaily
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.registry import AgentSpec

Metric = Literal["tokens", "turns"]


@dataclass(frozen=True)
class Usage:
    tokens: int = 0
    turns: int = 0

    def plus(self, tokens: int, turns: int) -> Usage:
        return Usage(self.tokens + tokens, self.turns + turns)


def today_usage(session: Session, *, agent: str, env: str, day: date) -> Usage:
    row = session.scalars(
        select(AgentUsageDaily)
        .where(AgentUsageDaily.agent == agent, AgentUsageDaily.env == env, AgentUsageDaily.day == day)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        return Usage()
    return Usage(tokens=int(row.tokens_in) + int(row.tokens_out), turns=int(row.turns))


def exhausted(spec: AgentSpec, usage: Usage) -> list[Metric]:
    """The budgets ``usage`` has already reached (empty = the agent may run)."""
    hit: list[Metric] = []
    if usage.tokens >= spec.daily_tokens:
        hit.append("tokens")
    if usage.turns >= spec.daily_turns:
        hit.append("turns")
    return hit


def crossed(spec: AgentSpec, before: Usage, after: Usage, fraction: Decimal) -> list[Metric]:
    """The budgets whose ``fraction`` mark this run crossed (was below, is now at or above)."""
    out: list[Metric] = []
    token_mark = Decimal(spec.daily_tokens) * fraction
    turn_mark = Decimal(spec.daily_turns) * fraction
    if before.tokens < token_mark <= after.tokens:
        out.append("tokens")
    if before.turns < turn_mark <= after.turns:
        out.append("turns")
    return out


def budget_payload(
    spec: AgentSpec, *, day: date, usage: Usage, metrics: list[Metric], fraction: Decimal
) -> dict[str, Any]:
    """Payload of ``system.agent_budget`` (see docs/handoff/ATL-83-agent-runtime.md)."""
    return {
        "agent": spec.slug,
        "day": day.isoformat(),
        "threshold_pct": format((fraction * 100).normalize(), "f"),
        "crossed": list(metrics),
        "tokens_used": usage.tokens,
        "tokens_budget": spec.daily_tokens,
        "turns_used": usage.turns,
        "turns_budget": spec.daily_turns,
    }
