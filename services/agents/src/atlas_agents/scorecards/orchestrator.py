"""The Orchestrator's daily score (ATL-197, brief §7.9 Scoring, decision #253 K).

Per env and Baghdad day:

* **Cycles on time**: every ``cycles`` row started that day. Its deadline is
  max(started_at + the cycle budget, the latest step deadline stored in ``cycles.deadlines``) +
  ``grace_seconds``, the budget being the research, Strategist, review, vote and risk timeouts in
  ``agents.orchestrator``. A cycle is on time when ``closed_at`` is set and no later than that; it is
  late when it closed after it or is still open past it; a cycle still open before its deadline is
  ``pending`` and left out. ``on_time_rate`` is the row's ``hit_rate``; ``n`` is the counted cycles.
* **Owner acknowledgement latency**: for each owner (``author_type = 'human'``) War Room message of
  the day, the seconds to the first Orchestrator message replying to it (``reply_to``); median and
  p90 by linear interpolation, plus the unanswered count.
* **Loops stopped**: the Orchestrator's ``loop_summary`` cards of the day (ATL-161).

The live counters :func:`atlas_agents.orchestrator.owner.record_score` writes during the day are
replaced by these recomputed figures; any other keys in the row's data are kept.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import AgentScore
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.scorecards import rate
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "orchestrator"
LOOP_CARD: Final = "loop_summary"
_BUDGET_KEYS: Final = (
    "research_timeout_seconds",
    "strategist_timeout_seconds",
    "review_timeout_seconds",
    "vote_seconds",
    "risk_timeout_seconds",
)


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["agents"]["scoring"]["orchestrator"]
    return cfg


def cycle_budget(defaults: Mapping[str, Any] = DEFAULTS) -> timedelta:
    orch = defaults["agents"]["orchestrator"]
    return timedelta(seconds=sum(int(orch[k]) for k in _BUDGET_KEYS))


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# -- pure maths -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleRow:
    cycle_id: str
    kind: str
    started_at: datetime
    closed_at: datetime | None
    deadlines: Mapping[str, Any]


def cycle_deadline(cycle: CycleRow, budget: timedelta, grace: timedelta) -> datetime:
    stored = [d for d in (_parse(v) for v in cycle.deadlines.values()) if d is not None]
    latest = max([cycle.started_at + budget, *stored])
    return latest + grace


def cycle_status(cycle: CycleRow, now: datetime, budget: timedelta, grace: timedelta) -> str:
    """on_time | late | pending."""
    deadline = cycle_deadline(cycle, budget, grace)
    if cycle.closed_at is not None:
        return "on_time" if cycle.closed_at <= deadline else "late"
    return "late" if now > deadline else "pending"


def cycle_stats(
    cycles: Sequence[CycleRow], now: datetime, budget: timedelta, grace: timedelta
) -> dict[str, Any]:
    rows = []
    for c in cycles:
        status = cycle_status(c, now, budget, grace)
        rows.append(
            {
                "cycle_id": c.cycle_id,
                "kind": c.kind,
                "started_at": c.started_at.isoformat(),
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "deadline": cycle_deadline(c, budget, grace).isoformat(),
                "status": status,
            }
        )
    on_time = sum(1 for r in rows if r["status"] == "on_time")
    late = sum(1 for r in rows if r["status"] == "late")
    return {
        "cycles_n": on_time + late,
        "cycles_on_time": on_time,
        "cycles_late": late,
        "cycles_pending": sum(1 for r in rows if r["status"] == "pending"),
        "on_time_rate": rate(on_time, on_time + late),
        "cycles": rows,
    }


def ack_stats(latencies: Sequence[float], unanswered: int, target_seconds: float) -> dict[str, Any]:
    base = scoring.latency_stats(latencies, target_seconds)
    return {
        "ack_n": len(latencies),
        "ack_unanswered": unanswered,
        "ack_latency_seconds": [round(v, 3) for v in latencies],
        "ack_median_seconds": base["latency_median_s"],
        "ack_p90_seconds": base["latency_p90_s"],
        "ack_max_seconds": max(latencies) if latencies else None,
        "ack_within_target_share": base["within_target_share"],
        "ack_target_seconds": target_seconds,
    }


# -- reads ------------------------------------------------------------------------------------------

_CYCLES = text(
    "SELECT id, kind, started_at, closed_at, deadlines FROM cycles "
    "WHERE env = :env AND started_at >= :start AND started_at < :end ORDER BY started_at, id"
)

_ACKS = text(
    "SELECT o.id, o.created_at, "
    "  (SELECT min(r.created_at) FROM warroom_messages r "
    "    WHERE r.env = o.env AND r.reply_to = o.id AND r.author = :agent AND r.author_type = 'agent') "
    "FROM warroom_messages o "
    "WHERE o.env = :env AND o.author_type = 'human' AND o.created_at >= :start AND o.created_at < :end "
    "ORDER BY o.created_at, o.id"
)

_LOOPS = text(
    "SELECT count(*) FROM warroom_messages "
    "WHERE env = :env AND author = :agent AND author_type = 'agent' AND card->>'type' = :card "
    "  AND created_at >= :start AND created_at < :end"
)


def load_cycles(session: Session, env: str, day: date) -> list[CycleRow]:
    start, end = scoring.day_bounds(day)
    return [
        CycleRow(str(cid), kind, started, closed, deadlines or {})
        for cid, kind, started, closed, deadlines in session.execute(
            _CYCLES, {"env": env, "start": start, "end": end}
        ).all()
    ]


def ack_latencies(session: Session, env: str, day: date) -> tuple[list[float], int]:
    start, end = scoring.day_bounds(day)
    latencies: list[float] = []
    unanswered = 0
    for _, asked, answered in session.execute(
        _ACKS, {"env": env, "agent": AGENT, "start": start, "end": end}
    ).all():
        if answered is None:
            unanswered += 1
        else:
            latencies.append(max(0.0, (answered - asked).total_seconds()))
    return latencies, unanswered


def loops_stopped(session: Session, env: str, day: date) -> int:
    start, end = scoring.day_bounds(day)
    return int(
        session.execute(
            _LOOPS, {"env": env, "agent": AGENT, "card": LOOP_CARD, "start": start, "end": end}
        ).scalar()
        or 0
    )


# -- the job ----------------------------------------------------------------------------------------


def score_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score the Orchestrator on Baghdad ``day``; the row's data, or None (skipped)."""
    now = ctx.now()
    grace = timedelta(seconds=int(config()["grace_seconds"]))
    target = float(DEFAULTS["agents"]["orchestrator"]["ack_target_seconds"])
    with ctx.session_factory() as session:
        cycles = load_cycles(session, ctx.env, day)
        latencies, unanswered = ack_latencies(session, ctx.env, day)
        loops = loops_stopped(session, ctx.env, day)
        if not cycles and not latencies and not unanswered and not loops:
            log.info("scoring: no %s activity on %s (%s); skipped", AGENT, day, ctx.env)
            return None
        c_stats = cycle_stats(cycles, now, cycle_budget(), grace)
        a_stats = ack_stats(latencies, unanswered, target)
        existing = session.execute(
            select(AgentScore.data).where(
                AgentScore.agent == AGENT, AgentScore.env == ctx.env, AgentScore.day == day
            )
        ).scalar()
        data: dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
        data.update(
            {
                **c_stats,
                **a_stats,
                "loops_stopped": loops,
                "cycle_budget_seconds": int(cycle_budget().total_seconds()),
                "grace_seconds": int(grace.total_seconds()),
                "metrics": {
                    "on_time_rate": c_stats["on_time_rate"],
                    "ack_median_seconds": a_stats["ack_median_seconds"],
                    "ack_p90_seconds": a_stats["ack_p90_seconds"],
                    "loops_stopped": loops,
                },
            }
        )
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=c_stats["cycles_n"],
            hit_rate=c_stats["on_time_rate"],
            data=data,
            now=now,
        )
        session.commit()
    return data


__all__ = ["AGENT", "CycleRow", "ack_stats", "cycle_budget", "cycle_deadline", "cycle_stats", "score_day"]
