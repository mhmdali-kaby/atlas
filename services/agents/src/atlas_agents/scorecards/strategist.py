"""The Strategist's daily score (ATL-196, brief §7.6 Scoring).

The sample is the Strategist's entry proposals created on the Baghdad day, latest version of each
chain ("submitted"). One ``agent_scores`` row per env and day, upserted:

* ``avg_realised_r`` over taken proposals that have closed (``taken_n``), R as in
  :mod:`atlas_agents.scorecards.outcomes`;
* ``rejection_rate`` = submitted proposals whose newest risk check on that version is ``rejected`` /
  submitted (``risk_rejected_n`` / ``submitted_n``);
* ``brier_score`` = mean (probability_estimate - o)^2 where o = 1 when TP1 was reached before the
  stop (real for taken trades, simulated for paper trades; not-filled paper trades and proposals
  without a probability are left out), with ``mean_p`` and ``observed`` of the same sample
  (``brier_n``); the row's ``hit_rate`` is ``observed``;
* ``regret_r`` = mean realised R of the day's filled paper trades (``regret_n``).

The day is skipped until it has ended; proposals still open, or taken and not yet closed, are listed
under ``pending`` and the day is re-scored on later runs inside ``lookback_days``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Final

from atlas_shared.db import RiskCheck, owner_day
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select

from atlas_agents import scoring
from atlas_agents.scorecards import outcomes
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "strategist"


def config() -> Any:
    return DEFAULTS["agents"]["scoring"]["strategist"]


def brier(pairs: list[tuple[float, int]]) -> dict[str, float | None]:
    """Brier score of (probability, outcome 0/1) pairs, with the mean stated probability and the
    observed frequency of the same sample."""
    if not pairs:
        return {"brier_score": None, "mean_p": None, "observed": None}
    n = len(pairs)
    return {
        "brier_score": sum((p - o) ** 2 for p, o in pairs) / n,
        "mean_p": sum(p for p, _ in pairs) / n,
        "observed": sum(o for _, o in pairs) / n,
    }


def score_strategist_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    now = ctx.now()
    _start, end = outcomes.day_bounds(day)
    if now < end:
        log.info("scoring: strategist %s has not ended; skipped", day)
        return None
    with ctx.session_factory() as session:
        proposals, results = outcomes.day_outcomes(session, ctx.env, day, now)
        session.commit()  # paper trades are kept even when nothing is scored
        if not proposals:
            log.info("scoring: strategist has no proposal on %s (%s); skipped", day, ctx.env)
            return None
        rejected = 0
        taken_r: list[float] = []
        regret: list[float] = []
        pairs: list[tuple[float, int]] = []
        rows: list[dict[str, Any]] = []
        for p in proposals:
            result = session.scalar(
                select(RiskCheck.result)
                .where(RiskCheck.proposal_id == p.id)
                .order_by(RiskCheck.created_at.desc(), RiskCheck.id.desc())
                .limit(1)
            )
            if result == "rejected":
                rejected += 1
            o = results[p.id]
            if o.kind == "taken" and o.realised_r is not None:
                taken_r.append(float(o.realised_r))
            if o.kind == "paper" and o.realised_r is not None:
                regret.append(float(o.realised_r))
            if o.kind in ("taken", "paper") and o.tp1_hit is not None and p.probability is not None:
                pairs.append((float(p.probability), 1 if o.tp1_hit else 0))
            rows.append(
                {
                    "proposal_id": str(p.id),
                    "version": p.version,
                    "inst_id": p.inst_id,
                    "side": p.side,
                    "status": p.status,
                    "risk_result": result,
                    "probability": outcomes.as_float(p.probability),
                    "outcome": o.kind,
                    "realised_r": outcomes.as_float(o.realised_r),
                    "tp1_hit": o.tp1_hit,
                    "source": o.source,
                }
            )
        b = brier(pairs)
        submitted = len(proposals)
        data: dict[str, Any] = {
            "avg_realised_r": outcomes.mean(taken_r),
            "taken_n": len(taken_r),
            "rejection_rate": outcomes.ratio(rejected, submitted),
            "risk_rejected_n": rejected,
            "submitted_n": submitted,
            "brier_score": b["brier_score"],
            "mean_p": b["mean_p"],
            "observed": b["observed"],
            "brier_n": len(pairs),
            "regret_r": outcomes.mean(regret),
            "regret_n": len(regret),
            "pending": outcomes.summary_ids(results, "pending"),
            "proposals": rows,
            "metrics": {
                "avg_realised_r": outcomes.mean(taken_r),
                "rejection_rate": outcomes.ratio(rejected, submitted),
                "brier_score": b["brier_score"],
                "regret_r": outcomes.mean(regret),
            },
        }
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=submitted,
            hit_rate=b["observed"],
            data=data,
            now=now,
            brier_score=b["brier_score"],
        )
        session.commit()
    return data


def score_days(ctx: ToolContext, today: date | None = None) -> dict[date, dict[str, Any] | None]:
    """(Re)score every Baghdad day in [today - lookback_days, today - 1]."""
    current = today or owner_day(ctx.now())
    out: dict[date, dict[str, Any] | None] = {}
    for day in outcomes.lookback_days(config(), current):
        try:
            out[day] = score_strategist_day(ctx, day)
        except Exception:  # one bad day must not stop the rest
            log.exception("scoring: strategist for %s failed", day)
            out[day] = None
    return out


__all__ = ["AGENT", "brier", "score_days", "score_strategist_day"]
