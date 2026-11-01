"""The daily scoring hooks (ATL-110, decision #247 (6)): ``agents.schedule.scoring`` (00:15 Baghdad)
for Market Scout and News Intelligence, and (ATL-195) the Technical Analyst and the Derivatives &
Sentiment Analyst, (ATL-196) the Strategist and the Devil's Advocate, and (ATL-197) the Portfolio
Manager, the Orchestrator and the Historian, whose scorers live in ``atlas_agents.scorecards``. Each
is registered under its
own slug so a disabled agent is not scored.

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
from atlas_agents.scorecards import derivatives, technical
from atlas_agents.scorecards import devils_advocate as da_scorecard
from atlas_agents.scorecards import historian as historian_scores
from atlas_agents.scorecards import orchestrator as orchestrator_scores
from atlas_agents.scorecards import portfolio_manager as pm_scores
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


scoring.register_scorer(technical.AGENT, technical.score_day)


@HOOKS.schedule(technical.AGENT, scoring.JOB)
async def score_technical_analyst(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, technical.AGENT)
    return None


scoring.register_scorer(derivatives.AGENT, derivatives.score_day)


@HOOKS.schedule(derivatives.AGENT, scoring.JOB)
async def score_derivatives_sentiment(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, derivatives.AGENT)
    return None


scoring.register_scorer(pm_scores.AGENT, pm_scores.score_day)


@HOOKS.schedule(pm_scores.AGENT, scoring.JOB)
async def score_portfolio_manager(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, pm_scores.AGENT)
    return None


scoring.register_scorer(orchestrator_scores.AGENT, orchestrator_scores.score_day)


@HOOKS.schedule(orchestrator_scores.AGENT, scoring.JOB)
async def score_orchestrator(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    await run_scoring(host, fire, orchestrator_scores.AGENT)
    return None


@HOOKS.schedule(historian_scores.AGENT, scoring.JOB)
async def score_historian(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    """The Historian's 72h horizons need a longer lookback than the shared one, so it runs its own
    day range (agents.scoring.historian.lookback_days)."""
    await run_scoring(host, fire, historian_scores.AGENT, historian_scores.score_days)
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
    "score_derivatives_sentiment",
    "score_devils_advocate",
    "score_historian",
    "score_market_scout",
    "score_news_intelligence",
    "score_orchestrator",
    "score_portfolio_manager",
    "score_strategist",
    "score_technical_analyst",
]
