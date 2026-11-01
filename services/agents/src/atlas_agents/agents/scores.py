"""The daily scoring hooks (ATL-110, decision #247 (6)): ``agents.schedule.scoring`` (00:15 Baghdad)
for Market Scout and News Intelligence, each registered under its own slug so a disabled agent is
not scored.

Scoring is code, not a model run, so no ``agent_runs`` row says "done today" and the supervisor
would fire the hook on every tick after 00:15. The hook remembers the day it scored in memory; a
restart scores the day again, which only overwrites the same rows (the job is idempotent).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from atlas_agents import scoring
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.scorecards import devils_advocate as da_scorecard
from atlas_agents.scorecards import strategist as strategist_scorecard
from atlas_agents.session import RunResult
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

_scored: dict[tuple[str, str], object] = {}
"""(agent, env) -> the Baghdad day last scored by this process."""

DaysScorer = Callable[[ToolContext, date], dict[date, dict[str, Any] | None]]


async def run_scoring(
    host: AgentHost, fire: ScheduleFire, agent: str, days: DaysScorer | None = None
) -> None:
    """Score ``agent``'s recent days once per Baghdad day. ``days(ctx, today)`` replaces ATL-110's
    ``scoring.score_days(ctx, agent, today)`` for scorers with their own lookback (ATL-196)."""
    key = (agent, host.env)
    if _scored.get(key) == fire.day:
        return
    ctx = host.tool_context(agent)
    if days is None:
        results = await asyncio.to_thread(scoring.score_days, ctx, agent, fire.day)
    else:
        results = await asyncio.to_thread(days, ctx, fire.day)
    _scored[key] = fire.day
    log.info(
        "scoring: %s scored %s, skipped %s",
        agent,
        [d.isoformat() for d, r in results.items() if r is not None],
        [d.isoformat() for d, r in results.items() if r is None],
    )


@HOOKS.schedule(scoring.SCOUT, scoring.JOB)
async def score_market_scout(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, scoring.SCOUT)
    return None


@HOOKS.schedule(scoring.NEWS, scoring.JOB)
async def score_news_intelligence(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, scoring.NEWS)
    return None


@HOOKS.schedule(strategist_scorecard.AGENT, scoring.JOB)
async def score_strategist(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    """ATL-196: the Strategist's scores, after (re)simulating the days' paper trades."""
    await run_scoring(host, fire, strategist_scorecard.AGENT, strategist_scorecard.score_days)
    return None


@HOOKS.schedule(da_scorecard.AGENT, scoring.JOB)
async def score_devils_advocate(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    """ATL-196: the Devil's Advocate's caught, false-alarm and miss rates."""
    await run_scoring(host, fire, da_scorecard.AGENT, da_scorecard.score_days)
    return None


__all__ = [
    "run_scoring",
    "score_devils_advocate",
    "score_market_scout",
    "score_news_intelligence",
    "score_strategist",
]
