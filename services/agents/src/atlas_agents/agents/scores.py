"""The daily scoring hooks (ATL-110, decision #247 (6)): ``agents.schedule.scoring`` (00:15 Baghdad)
for Market Scout and News Intelligence, and (ATL-195) the Technical Analyst and the Derivatives &
Sentiment Analyst, and (ATL-197) the Portfolio Manager, the Orchestrator and the Historian, whose
scorers live in ``atlas_agents.scorecards``. Each is registered under its
own slug so a disabled agent is not scored.

Scoring is code, not a model run, so no ``agent_runs`` row says "done today" and the supervisor
would fire the hook on every tick after 00:15. The hook remembers the day it scored in memory; a
restart scores the day again, which only overwrites the same rows (the job is idempotent).
"""

from __future__ import annotations

import asyncio
import logging

from atlas_agents import scoring
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.scorecards import derivatives, technical
from atlas_agents.scorecards import historian as historian_scores
from atlas_agents.scorecards import orchestrator as orchestrator_scores
from atlas_agents.scorecards import portfolio_manager as pm_scores
from atlas_agents.session import RunResult

log = logging.getLogger(__name__)

_scored: dict[tuple[str, str], object] = {}
"""(agent, env) -> the Baghdad day last scored by this process."""


async def run_scoring(host: AgentHost, fire: ScheduleFire, agent: str) -> None:
    key = (agent, host.env)
    if _scored.get(key) == fire.day:
        return
    ctx = host.tool_context(agent)
    results = await asyncio.to_thread(scoring.score_days, ctx, agent, fire.day)
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
    key = (historian_scores.AGENT, host.env)
    if _scored.get(key) == fire.day:
        return None
    ctx = host.tool_context(historian_scores.AGENT)
    results = await asyncio.to_thread(historian_scores.score_days, ctx, fire.day)
    _scored[key] = fire.day
    log.info(
        "scoring: historian scored %s, skipped %s",
        [d.isoformat() for d, r in results.items() if r is not None],
        [d.isoformat() for d, r in results.items() if r is None],
    )
    return None


__all__ = [
    "run_scoring",
    "score_derivatives_sentiment",
    "score_historian",
    "score_market_scout",
    "score_news_intelligence",
    "score_orchestrator",
    "score_portfolio_manager",
    "score_technical_analyst",
]
