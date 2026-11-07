"""The Historian's daily calibration score (ATL-197, brief §7.3, §9.3, decision #253 K).

Source: the Historian's War Room ``research_report`` cards with ``card.type = "history_verdict"``
posted on the Baghdad day. Each card's ``distribution[h].pct_positive`` (0-100) for every horizon
``h`` in ``agents.scoring.historian.horizons`` is read as a probability p = pct_positive / 100 that
the instrument's move over ``h`` from the card's ``created_at`` (t0) is positive. The outcome is the
close-to-close move: the close of the last ``price_bar`` candle closed by t0 + h minus the one closed
by t0 (each refused when it closed more than ``max_price_lag_minutes`` earlier); a zero move did not
happen.

Row: ``brier_score`` = mean (p - o)² over every statement, with ``data.mean_p`` (mean p) and
``data.observed`` (fraction that happened) on the same sample; ``hit_rate`` = the share of
statements with p != 0.5 whose side (p > 0.5 means "up") was right; ``n`` = statements scored.
``data.verdict_mix`` counts every card's verdict (supports, weakly_supports, does_not_support,
contradicts), scored or not.

A day is skipped until its longest horizon has closed (day end + max horizon), so this scorer runs
its own day range (``historian.lookback_days``) rather than the shared ``agents.scoring.lookback_days``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

from atlas_shared.db import owner_day
from atlas_shared.defaults import DEFAULTS
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.scorecards import DayCard, day_cards, rate
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "historian"
CARD_TYPE: Final = "history_verdict"
VERDICTS: Final = ("supports", "weakly_supports", "does_not_support", "contradicts")
_HORIZON: Final = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)
_UNIT: Final = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["agents"]["scoring"]["historian"]
    return cfg


def horizon_delta(label: str) -> timedelta | None:
    """``"4h"`` -> 4 hours; None for a label that is not <number><m|h|d>."""
    match = _HORIZON.match(label)
    if match is None:
        return None
    return int(match.group(1)) * _UNIT[match.group(2).lower()]


# -- pure maths -------------------------------------------------------------------------------------


def brier(pairs: Sequence[tuple[float, bool]]) -> dict[str, Any]:
    """Brier score with the mean stated probability and the observed frequency of the same sample."""
    if not pairs:
        return {"brier_score": None, "brier_n": 0, "mean_p": None, "observed": None}
    return {
        "brier_score": sum((p - (1.0 if o else 0.0)) ** 2 for p, o in pairs) / len(pairs),
        "brier_n": len(pairs),
        "mean_p": sum(p for p, _ in pairs) / len(pairs),
        "observed": sum(1 for _, o in pairs if o) / len(pairs),
    }


def summarise(statements: Sequence[Mapping[str, Any]], verdicts: Sequence[str]) -> dict[str, Any]:
    pairs = [(float(s["p"]), bool(s["happened"])) for s in statements]
    sided = [(p, o) for p, o in pairs if p != 0.5]
    right = sum(1 for p, o in sided if (p > 0.5) == o)
    mix = {v: 0 for v in VERDICTS}
    for v in verdicts:
        mix[v] = mix.get(v, 0) + 1
    by_horizon: dict[str, Any] = {}
    for label in sorted({str(s["horizon"]) for s in statements}):
        subset = [(float(s["p"]), bool(s["happened"])) for s in statements if s["horizon"] == label]
        by_horizon[label] = brier(subset)
    return {
        **brier(pairs),
        "direction_hits": right,
        "direction_n": len(sided),
        "direction_hit_rate": rate(right, len(sided)),
        "verdict_mix": mix,
        "verdicts_n": len(verdicts),
        "by_horizon": by_horizon,
    }


# -- reads ------------------------------------------------------------------------------------------


def _score_card(
    session: Session, env: str, card: DayCard, horizons: Sequence[str], bar: str, max_lag: timedelta
) -> tuple[list[dict[str, Any]], list[str]]:
    """The card's scorable statements and the reasons any were skipped."""
    inst_id = card.card.get("inst_id")
    distribution = card.card.get("distribution") or {}
    if not inst_id or not isinstance(distribution, Mapping):
        return [], ["no inst_id or distribution"]
    wanted = [h for h in horizons if isinstance(distribution.get(h), Mapping)]
    if not wanted:
        return [], ["no configured horizon in the distribution"]
    t0 = card.created_at
    p0, why0 = scoring.close_at(session, env, str(inst_id), t0, bar, max_lag)
    if p0 is None:
        return [], [str(why0)]
    out: list[dict[str, Any]] = []
    skipped: list[str] = []
    for label in wanted:
        delta = horizon_delta(label)
        pct = distribution[label].get("pct_positive")
        if delta is None or pct is None:
            skipped.append(f"{label}: no horizon length or pct_positive")
            continue
        p1, why1 = scoring.close_at(session, env, str(inst_id), t0 + delta, bar, max_lag)
        if p1 is None:
            skipped.append(f"{label}: {why1}")
            continue
        out.append(
            {
                "horizon": label,
                "p": float(pct) / 100.0,
                "p0": p0,
                "p1": p1,
                "happened": p1 > p0,
            }
        )
    return out, skipped


# -- the job ----------------------------------------------------------------------------------------


def window_closes(day: date) -> datetime:
    _, day_end = scoring.day_bounds(day)
    deltas = [d for d in (horizon_delta(str(h)) for h in config()["horizons"]) if d is not None]
    return day_end + max(deltas, default=timedelta(0))


def score_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score the Historian's verdict cards of Baghdad ``day``; the row's data, or None (skipped)."""
    cfg = config()
    now = ctx.now()
    closes = window_closes(day)
    if now < closes:
        log.info("scoring: %s %s windows close at %s; skipped", AGENT, day, closes.isoformat())
        return None
    horizons = [str(h) for h in cfg["horizons"]]
    bar = str(cfg["price_bar"])
    max_lag = timedelta(minutes=int(cfg["max_price_lag_minutes"]))
    with ctx.session_factory() as session:
        cards = day_cards(session, ctx.env, AGENT, CARD_TYPE, day)
        if not cards:
            log.info("scoring: no %s cards on %s (%s); skipped", AGENT, day, ctx.env)
            return None
        statements: list[dict[str, Any]] = []
        verdicts: list[str] = []
        per_card: list[dict[str, Any]] = []
        for item in cards:
            verdict = item.card.get("verdict")
            if isinstance(verdict, str):
                verdicts.append(verdict)
            rows, skipped = _score_card(session, ctx.env, item, horizons, bar, max_lag)
            statements.extend(rows)
            per_card.append(
                {
                    "message_id": item.message_id,
                    "inst_id": item.card.get("inst_id"),
                    "t0": item.created_at.isoformat(),
                    "verdict": verdict,
                    "statements": rows,
                    "skipped": skipped,
                }
            )
        stats = summarise(statements, verdicts)
        data = {
            **stats,
            "cards_n": len(cards),
            "cards": per_card,
            "window_closes": closes.isoformat(),
            "metrics": {
                "brier_score": stats["brier_score"],
                "mean_p": stats["mean_p"],
                "observed": stats["observed"],
                "direction_hit_rate": stats["direction_hit_rate"],
            },
        }
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=stats["brier_n"],
            hit_rate=stats["direction_hit_rate"],
            brier_score=stats["brier_score"],
            data=data,
            now=now,
        )
        session.commit()
    return data


def score_days(ctx: ToolContext, today: date | None = None) -> dict[date, dict[str, Any] | None]:
    """(Re)score every Baghdad day in [today - historian.lookback_days, today - 1]."""
    current = today or owner_day(ctx.now())
    lookback = int(config()["lookback_days"])
    out: dict[date, dict[str, Any] | None] = {}
    for back in range(lookback, 0, -1):
        day = current - timedelta(days=back)
        try:
            out[day] = score_day(ctx, day)
        except Exception:  # one bad day must not stop the rest
            log.exception("scoring: %s for %s failed", AGENT, day)
            out[day] = None
    return out


__all__ = ["AGENT", "CARD_TYPE", "VERDICTS", "brier", "horizon_delta", "score_day", "score_days", "summarise"]
