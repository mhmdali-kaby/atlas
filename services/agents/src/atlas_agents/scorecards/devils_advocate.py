"""The Devil's Advocate's daily score (ATL-196, brief §7.7 Scoring).

The sample is the Strategist's entry proposals of the Baghdad day (latest version of each chain) that
the Devil's Advocate rated: the ``risk_rating`` of the newest ``da_review`` card on any version of
the chain (a round-2 re-rate wins). The outcome is the realised R of the taken trade, else of the
paper trade (:mod:`atlas_agents.scorecards.outcomes`); a not-filled paper trade or a trade still open
has none and is left out. Win = R >= ``scratch_r``, loss = R <= -``scratch_r``, else scratch.

* rated >= ``high_rating`` (4), "do not trade" (``high_n``): ``caught_rate`` = share lost,
  ``false_alarm_rate`` = share won;
* rated <= ``low_rating`` (2), "clean" (``low_n``): ``low_win_rate`` = share won, ``miss_rate`` =
  share lost.

The row's ``hit_rate`` is (high lost + low won) / (high_n + low_n); ``n`` is every rated proposal
with an outcome (ratings of 3 included). No Brier score: a rating is not a probability.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Final

from atlas_shared.db import owner_day
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import text
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.scorecards import outcomes, paper
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "devils_advocate"

_NEWEST_RATING = text(
    "SELECT (card->>'risk_rating')::int FROM warroom_messages "
    "WHERE env = :env AND author = :agent AND card->>'type' = 'da_review' "
    "  AND card->>'proposal_id' = ANY(:ids) AND card->>'risk_rating' ~ '^[0-9]+$' "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)


def config() -> Any:
    return DEFAULTS["agents"]["scoring"]["devils_advocate"]


def classify(r: Decimal, scratch: Decimal) -> str:
    if r >= scratch:
        return "win"
    if r <= -scratch:
        return "loss"
    return "scratch"


def rating_stats(rated: list[tuple[int, str]], high: int, low: int) -> dict[str, Any]:
    """(rating, win|loss|scratch) pairs -> the DA's rates, each with its n."""
    hi = [o for r, o in rated if r >= high]
    lo = [o for r, o in rated if r <= low]
    hi_lost, hi_won = hi.count("loss"), hi.count("win")
    lo_won, lo_lost = lo.count("win"), lo.count("loss")
    return {
        "high_n": len(hi),
        "high_lost": hi_lost,
        "high_won": hi_won,
        "caught_rate": outcomes.ratio(hi_lost, len(hi)),
        "false_alarm_rate": outcomes.ratio(hi_won, len(hi)),
        "low_n": len(lo),
        "low_won": lo_won,
        "low_lost": lo_lost,
        "low_win_rate": outcomes.ratio(lo_won, len(lo)),
        "miss_rate": outcomes.ratio(lo_lost, len(lo)),
        "accuracy": outcomes.ratio(hi_lost + lo_won, len(hi) + len(lo)),
    }


def newest_rating(session: Session, env: str, ids: list[uuid.UUID]) -> int | None:
    value = session.execute(
        _NEWEST_RATING, {"env": env, "agent": AGENT, "ids": [str(i) for i in ids]}
    ).scalar()
    return None if value is None else int(value)


def score_da_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    now = ctx.now()
    _start, end = outcomes.day_bounds(day)
    if now < end:
        log.info("scoring: devils_advocate %s has not ended; skipped", day)
        return None
    cfg = config()
    high, low = int(cfg["high_rating"]), int(cfg["low_rating"])
    scratch = Decimal(str(cfg["scratch_r"]))
    with ctx.session_factory() as session:
        proposals, results = outcomes.day_outcomes(session, ctx.env, day, now)
        session.commit()
        rated: list[tuple[int, str]] = []
        rows: list[dict[str, Any]] = []
        for p in proposals:
            rating = newest_rating(session, ctx.env, paper.chain_ids(session, p))
            if rating is None:
                continue
            o = results[p.id]
            result = classify(o.realised_r, scratch) if o.realised_r is not None else None
            if result is not None:
                rated.append((rating, result))
            rows.append(
                {
                    "proposal_id": str(p.id),
                    "inst_id": p.inst_id,
                    "risk_rating": rating,
                    "outcome": o.kind,
                    "realised_r": outcomes.as_float(o.realised_r),
                    "result": result,
                }
            )
        if not rated:
            log.info("scoring: devils_advocate has no rated proposal with an outcome on %s; skipped", day)
            return None
        stats = rating_stats(rated, high, low)
        data: dict[str, Any] = {
            **stats,
            "rated_n": len(rated),
            "high_rating": high,
            "low_rating": low,
            "scratch_r": str(scratch),
            "pending": [r["proposal_id"] for r in rows if r["result"] is None],
            "proposals": rows,
            "metrics": {
                "false_alarm_rate": stats["false_alarm_rate"],
                "miss_rate": stats["miss_rate"],
                "caught_rate": stats["caught_rate"],
                "low_win_rate": stats["low_win_rate"],
            },
        }
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=len(rated),
            hit_rate=stats["accuracy"],
            data=data,
            now=now,
        )
        session.commit()
    return data


def score_days(ctx: ToolContext, today: date | None = None) -> dict[date, dict[str, Any] | None]:
    current = today or owner_day(ctx.now())
    out: dict[date, dict[str, Any] | None] = {}
    for day in outcomes.lookback_days(config(), current):
        try:
            out[day] = score_da_day(ctx, day)
        except Exception:  # one bad day must not stop the rest
            log.exception("scoring: devils_advocate for %s failed", day)
            out[day] = None
    return out


__all__ = ["AGENT", "classify", "rating_stats", "score_da_day", "score_days"]
