"""The Derivatives & Sentiment Analyst's daily score (ATL-195, brief §7.5 Scoring, decision #253 (K)).

Scored from every ``research_report`` card of type ``positioning_report`` the analyst posted on the
Baghdad day, each from its ``created_at`` (t0). Settings: ``agents.scoring.derivatives``.

* **Supports-direction accuracy** - every non-neutral side of ``supports_direction`` is one call
  on the sign of the next move: long supports / short opposes = up, short supports / long opposes =
  down. The move is the ``price_bar`` close at t0 + supports_hours minus the close at t0 (each the
  last bar closed by that instant, refused past ``max_price_lag_minutes``); a zero move is a miss.
  ``supports_accuracy`` = correct calls / calls; it is also the row's ``hit_rate``, with ``n`` the
  number of calls.
* **Squeeze calls** - ``squeeze_risk`` >= ``squeeze_call_min``. A short squeeze points up, a long
  squeeze down. It hits when a ``price_bar`` bar in [t0, t0 + squeeze_hours) reaches p0 ±
  ``squeeze_move_atr`` x ATR(``atr_bar``) of the bars closed by t0 (high for up, low for down).
* **Brier score** - over every stated squeeze probability (calls or not): mean of (p - outcome)^2
  with outcome 1 when that squeeze's move happened. ``mean_p`` is the mean stated probability and
  ``observed`` the fraction that happened, on the same sample (``brier_n``).

The day is skipped (nothing written) until ``now`` >= the day's end + max(supports_hours,
squeeze_hours), when it has no card, or when no call and no squeeze probability could be scored.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Final

import pandas as pd
from atlas_quant import load_candles
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.scorecards import DayCard, day_cards, mean, rate
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "derivatives_sentiment"
CARD_TYPE: Final = "positioning_report"

_UP: Final = {("long", "supports"), ("short", "opposes")}
_DOWN: Final = {("short", "supports"), ("long", "opposes")}
SQUEEZE_SIGN: Final = {"short_squeeze": 1, "long_squeeze": -1}


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = scoring.scoring_config()["derivatives"]
    return cfg


# -- pure maths -------------------------------------------------------------------------------------


def direction_calls(supports: Mapping[str, str]) -> list[tuple[str, str, int]]:
    """(side, view, predicted sign) for every non-neutral side of ``supports_direction``."""
    out: list[tuple[str, str, int]] = []
    for side in ("long", "short"):
        view = supports.get(side)
        if (side, view) in _UP:
            out.append((side, str(view), 1))
        elif (side, view) in _DOWN:
            out.append((side, str(view), -1))
    return out


def squeeze_happened(bars: pd.DataFrame, p0: float, atr: float, kind: str, move_atr: float) -> bool:
    """Whether price reached p0 ± move_atr x atr in ``kind``'s direction within ``bars``."""
    if bars.empty:
        return False
    if SQUEEZE_SIGN[kind] > 0:
        return float(bars["h"].max()) >= p0 + move_atr * atr
    return float(bars["l"].min()) <= p0 - move_atr * atr


def brier(pairs: Sequence[tuple[float, bool]]) -> dict[str, Any]:
    """Brier score, mean stated probability and observed frequency of (p, happened) pairs."""
    if not pairs:
        return {"brier_score": None, "mean_p": None, "observed": None, "brier_n": 0}
    return {
        "brier_score": sum((p - (1.0 if hit else 0.0)) ** 2 for p, hit in pairs) / len(pairs),
        "mean_p": sum(p for p, _ in pairs) / len(pairs),
        "observed": sum(1 for _, hit in pairs if hit) / len(pairs),
        "brier_n": len(pairs),
    }


# -- reads ------------------------------------------------------------------------------------------


def _score_card(session: Session, env: str, item: DayCard) -> tuple[dict[str, Any] | None, str | None]:
    cfg = config()
    card = item.card
    inst_id = str(card.get("inst_id") or "")
    if not inst_id:
        return None, "card has no inst_id"
    t0 = item.created_at
    bar = str(cfg["price_bar"])
    lag = timedelta(minutes=int(cfg["max_price_lag_minutes"]))
    p0, why = scoring.close_at(session, env, inst_id, t0, bar, lag)
    if p0 is None:
        return None, why
    row: dict[str, Any] = {"message_id": item.message_id, "inst_id": inst_id, "t0": t0.isoformat(), "p0": p0}

    calls = direction_calls(card.get("supports_direction") or {})
    row["calls"] = []
    if calls:
        p1, why = scoring.close_at(
            session, env, inst_id, t0 + timedelta(hours=int(cfg["supports_hours"])), bar, lag
        )
        if p1 is None:
            row["calls_skipped"] = why
        else:
            move = p1 - p0
            row["p1"] = p1
            row["calls"] = [
                {"side": side, "view": view, "predicted": sign, "correct": move * sign > 0}
                for side, view, sign in calls
            ]

    squeezes = {k: float(v) for k, v in (card.get("squeeze_risk") or {}).items() if k in SQUEEZE_SIGN}
    row["squeezes"] = []
    if squeezes:
        atr, why = scoring.atr_at(session, env, inst_id, t0, str(cfg["atr_bar"]), int(cfg["atr_bars"]))
        if atr is None:
            row["squeezes_skipped"] = why
        else:
            end = t0 + timedelta(hours=int(cfg["squeeze_hours"]))
            bars = load_candles(session, inst_id, bar, t0, end, as_of=end, env=env).df
            move_atr = float(cfg["squeeze_move_atr"])
            threshold = float(cfg["squeeze_call_min"])
            row["atr"] = atr
            row["squeezes"] = [
                {
                    "kind": kind,
                    "p": p,
                    "call": p >= threshold,
                    "happened": squeeze_happened(bars, p0, atr, kind, move_atr),
                }
                for kind, p in sorted(squeezes.items())
            ]
    return row, None


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    calls = [c for r in rows for c in r.get("calls", [])]
    squeezes = [s for r in rows for s in r.get("squeezes", [])]
    squeeze_calls = [s for s in squeezes if s["call"]]
    hits = sum(1 for s in squeeze_calls if s["happened"])
    correct = sum(1 for c in calls if c["correct"])
    return {
        "supports_accuracy": rate(correct, len(calls)),
        "supports_n": len(calls),
        "supports_correct": correct,
        "squeeze_call_hits": hits,
        "squeeze_calls_n": len(squeeze_calls),
        "squeeze_call_hit_rate": rate(hits, len(squeeze_calls)),
        **brier([(float(s["p"]), bool(s["happened"])) for s in squeezes]),
        "mean_squeeze_call_p": mean([float(s["p"]) for s in squeeze_calls]),
    }


# -- the job ----------------------------------------------------------------------------------------


def score_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score the Derivatives & Sentiment Analyst's reports of Baghdad ``day``; the row's data, or
    None (skipped)."""
    cfg = config()
    now = ctx.now()
    _, day_end = scoring.day_bounds(day)
    closes = day_end + timedelta(hours=max(int(cfg["supports_hours"]), int(cfg["squeeze_hours"])))
    if now < closes:
        log.info("scoring: %s %s windows close at %s; skipped", AGENT, day, closes.isoformat())
        return None
    with ctx.session_factory() as session:
        cards = day_cards(session, ctx.env, AGENT, CARD_TYPE, day)
        if not cards:
            log.info("scoring: no %s cards on %s (%s); skipped", AGENT, day, ctx.env)
            return None
        rows: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for item in cards:
            row, why = _score_card(session, ctx.env, item)
            if row is None:
                skipped.append({"message_id": item.message_id, "reason": str(why)})
            else:
                rows.append(row)
        stats = summarise(rows)
        if not (stats["supports_n"] or stats["brier_n"]):
            log.info("scoring: %s %s has nothing scorable (%s); skipped", AGENT, day, skipped)
            return None
        data = {
            **stats,
            "cards_n": len(cards),
            "reports": rows,
            "skipped": skipped,
            "window_closes": closes.isoformat(),
            "metrics": {
                "supports_accuracy": stats["supports_accuracy"],
                "squeeze_call_hit_rate": stats["squeeze_call_hit_rate"],
                "squeeze_brier": stats["brier_score"],
                "mean_p": stats["mean_p"],
                "observed": stats["observed"],
            },
        }
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=stats["supports_n"],
            hit_rate=stats["supports_accuracy"],
            brier_score=stats["brier_score"],
            data=data,
            now=now,
        )
        session.commit()
    return data


__all__ = ["AGENT", "CARD_TYPE", "brier", "direction_calls", "score_day", "squeeze_happened", "summarise"]
