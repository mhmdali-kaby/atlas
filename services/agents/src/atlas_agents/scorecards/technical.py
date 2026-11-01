"""The Technical Analyst's daily score (ATL-195, brief §7.4 Scoring, decision #253 (K)).

Scored from every ``research_report`` card of type ``technical_setup`` the Technical Analyst posted
on the Baghdad day, each from its ``created_at`` (t0). Settings: ``agents.scoring.technical``.

* **tol** = ``level_tolerance_atr`` x ATR(``atr_bar``, Wilder ``quant.atr_window``) of the bars
  closed by t0; **ref** = the close of the last ``walk_bar`` candle closed by t0.
* **Level hold** - each distinct priced ``levels_used`` entry of the card: a level below ref is a
  support and holds while no ``walk_bar`` close in [t0, t0 + outcome_hours) is below level - tol;
  a level above ref is a resistance and holds while no close is above level + tol. A level equal to
  ref is not scored. ``level_hold_rate`` = held / levels.
* **Target before stop** - a setup (direction long/short) triggers on the first ``walk_bar`` bar in
  [t0, t0 + trade_hours) whose range overlaps its entry zone; the fill is the bar's open clamped to
  the zone. On that bar only the stop is checked (the order of the touches is unknown). On later
  bars a bar touching the stop counts as a stop even if it also touched TP1 (ambiguous = stop
  first), else a bar touching TP1 is a target. ``target_before_stop_rate`` = targets / (targets +
  stops); a triggered setup that reached neither is ``open`` and left out of that rate and of R.
* **Realised vs expected R** - over the resolved setups: realised R = (TP1 - fill) / |fill - stop|
  (signed for the side) at a target, -1 at a stop; expected R = the card's ``expected_r`` (else
  TP1's ``r_multiple``). ``avg_r_gap`` = avg realised - avg expected.
* **"No setup" accuracy** - a ``none`` card's range is the nearest priced level below ref and the
  nearest above; it is right when every close in [t0, t0 + outcome_hours) stays within
  [support - tol, resistance + tol]. Cards without a level on both sides are not scored.

The day is skipped (nothing written) until ``now`` >= the day's end + max(outcome_hours,
trade_hours), when it has no card, or when no metric has a sample. The row's ``hit_rate`` is the
target-before-stop rate and ``n`` its sample.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

import pandas as pd
from atlas_quant import load_candles
from atlas_quant.grid import bar_delta
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.scorecards import DayCard, day_cards, mean, rate
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

AGENT: Final = "technical_analyst"
CARD_TYPE: Final = "technical_setup"

Outcome = Literal["target", "stop", "open", "not_triggered"]


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = scoring.scoring_config()["technical"]
    return cfg


# -- pure maths -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Walk:
    outcome: Outcome
    fill: float | None = None
    realised_r: float | None = None
    triggered_at: datetime | None = None
    resolved_at: datetime | None = None


def walk_setup(
    bars: pd.DataFrame, direction: str, zone: tuple[float, float], stop: float, target: float
) -> Walk:
    """Walk ``bars`` (o/h/l/c, ascending ts index) for one long/short setup. See the module doc."""
    low, high = zone
    long = direction == "long"
    fill: float | None = None
    triggered_at: datetime | None = None
    stamps = pd.DatetimeIndex(bars.index)
    columns = (bars[c].to_numpy(dtype="float64") for c in ("o", "h", "l"))
    for ts, o, h, lo in zip(stamps, *columns, strict=True):
        at = ts.to_pydatetime()
        hit_stop = lo <= stop if long else h >= stop
        if fill is None:
            if lo <= high and h >= low:
                fill = min(max(o, low), high)
                triggered_at = at
                if hit_stop:
                    return Walk("stop", fill, -1.0, triggered_at, at)
            continue
        if hit_stop:
            return Walk("stop", fill, -1.0, triggered_at, at)
        if h >= target if long else lo <= target:
            risk = abs(fill - stop)
            r = (target - fill) / risk if long else (fill - target) / risk
            return Walk("target", fill, r, triggered_at, at)
    if fill is None:
        return Walk("not_triggered")
    return Walk("open", fill, None, triggered_at, None)


def level_held(closes: Sequence[float], ref: float, level: float, tol: float) -> bool | None:
    """Whether ``level`` held over ``closes`` (support below ``ref``, resistance above); None when
    the level sits at ref or there is no close."""
    if not closes or level == ref:
        return None
    if level < ref:
        return min(closes) >= level - tol
    return max(closes) <= level + tol


def stated_range(ref: float, levels: Sequence[float]) -> tuple[float, float] | None:
    """The nearest level below ``ref`` and the nearest above, or None when a side has none."""
    below = [p for p in levels if p < ref]
    above = [p for p in levels if p > ref]
    if not below or not above:
        return None
    return max(below), min(above)


def stayed_inside(closes: Sequence[float], bounds: tuple[float, float], tol: float) -> bool | None:
    if not closes:
        return None
    lo, hi = bounds
    return min(closes) >= lo - tol and max(closes) <= hi + tol


# -- reads ------------------------------------------------------------------------------------------


def _bars(session: Session, env: str, inst_id: str, bar: str, start: datetime, hours: int) -> pd.DataFrame:
    end = start + timedelta(hours=hours)
    return load_candles(session, inst_id, bar, start, end, as_of=end, env=env).df


def _levels(card: Mapping[str, Any]) -> list[float]:
    out: list[float] = []
    for level in card.get("levels_used") or []:
        price = level.get("price") if isinstance(level, Mapping) else None
        if price is None:
            continue
        value = float(price)
        if value > 0 and value not in out:
            out.append(value)
    return out


def _expected_r(card: Mapping[str, Any]) -> float | None:
    if card.get("expected_r") is not None:
        return float(card["expected_r"])
    targets = card.get("targets") or []
    if targets and targets[0].get("r_multiple") is not None:
        return float(targets[0]["r_multiple"])
    return None


def _score_card(session: Session, env: str, item: DayCard) -> tuple[dict[str, Any] | None, str | None]:
    cfg = config()
    card = item.card
    inst_id = str(card.get("inst_id") or "")
    t0 = item.created_at
    walk_bar = str(cfg["walk_bar"])
    if not inst_id:
        return None, "card has no inst_id"
    ref, why = scoring.close_at(session, env, inst_id, t0, walk_bar, bar_delta(walk_bar) * 2)
    if ref is None:
        return None, why
    atr, why = scoring.atr_at(session, env, inst_id, t0, str(cfg["atr_bar"]), int(cfg["atr_bars"]))
    if atr is None:
        return None, why
    tol = float(cfg["level_tolerance_atr"]) * atr
    outcome_closes = [
        float(c) for c in _bars(session, env, inst_id, walk_bar, t0, int(cfg["outcome_hours"]))["c"]
    ]
    levels = _levels(card)
    holds = [(lv, level_held(outcome_closes, ref, lv, tol)) for lv in levels]
    row: dict[str, Any] = {
        "message_id": item.message_id,
        "inst_id": inst_id,
        "setup_type": card.get("setup_type"),
        "direction": card.get("direction"),
        "t0": t0.isoformat(),
        "ref_price": ref,
        "atr": atr,
        "tolerance": tol,
        "levels": [{"price": lv, "held": h} for lv, h in holds if h is not None],
    }
    direction = card.get("direction")
    if direction in ("long", "short"):
        zone = card.get("entry_zone") or []
        targets = card.get("targets") or []
        if len(zone) != 2 or card.get("stop") is None or not targets:
            row["outcome"] = None
            row["reason"] = "setup has no entry zone, stop or target"
        else:
            bars = _bars(session, env, inst_id, walk_bar, t0, int(cfg["trade_hours"]))
            walk = walk_setup(
                bars,
                str(direction),
                (float(zone[0]), float(zone[1])),
                float(card["stop"]),
                float(targets[0]["price"]),
            )
            row.update(
                {
                    "outcome": walk.outcome,
                    "fill": walk.fill,
                    "realised_r": walk.realised_r,
                    "expected_r": _expected_r(card),
                    "triggered_at": walk.triggered_at.isoformat() if walk.triggered_at else None,
                    "resolved_at": walk.resolved_at.isoformat() if walk.resolved_at else None,
                }
            )
    else:
        bounds = stated_range(ref, levels)
        row["range"] = list(bounds) if bounds else None
        row["inside"] = stayed_inside(outcome_closes, bounds, tol) if bounds else None
    return row, None


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The day's metrics from the per-card rows of :func:`_score_card`."""
    levels = [lv["held"] for r in rows for lv in r.get("levels", [])]
    resolved = [r for r in rows if r.get("outcome") in ("target", "stop")]
    targets = [r for r in resolved if r["outcome"] == "target"]
    realised = [float(r["realised_r"]) for r in resolved]
    with_expected = [r for r in resolved if r.get("expected_r") is not None]
    expected = [float(r["expected_r"]) for r in with_expected]
    no_setup = [
        r["inside"]
        for r in rows
        if r.get("direction") not in ("long", "short") and r.get("inside") is not None
    ]
    avg_realised = mean(realised)
    avg_expected = mean(expected)
    return {
        "target_before_stop_rate": rate(len(targets), len(resolved)),
        "target_before_stop_n": len(resolved),
        "target_hits": len(targets),
        "stop_hits": len(resolved) - len(targets),
        "triggered_n": sum(1 for r in rows if r.get("outcome") in ("target", "stop", "open")),
        "open_n": sum(1 for r in rows if r.get("outcome") == "open"),
        "not_triggered_n": sum(1 for r in rows if r.get("outcome") == "not_triggered"),
        "level_hold_rate": rate(sum(1 for h in levels if h), len(levels)),
        "level_hold_n": len(levels),
        "avg_realised_r": avg_realised,
        "realised_r_n": len(realised),
        "avg_expected_r": avg_expected,
        "expected_r_n": len(expected),
        "avg_r_gap": None if avg_realised is None or avg_expected is None else avg_realised - avg_expected,
        "no_setup_accuracy": rate(sum(1 for x in no_setup if x), len(no_setup)),
        "no_setup_n": len(no_setup),
    }


# -- the job ----------------------------------------------------------------------------------------


def score_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score the Technical Analyst's setups of Baghdad ``day``; the row's data, or None (skipped)."""
    cfg = config()
    now = ctx.now()
    _, day_end = scoring.day_bounds(day)
    closes = day_end + timedelta(hours=max(int(cfg["outcome_hours"]), int(cfg["trade_hours"])))
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
        if not (stats["target_before_stop_n"] or stats["level_hold_n"] or stats["no_setup_n"]):
            log.info("scoring: %s %s has nothing scorable (%s); skipped", AGENT, day, skipped)
            return None
        data = {
            **stats,
            "cards_n": len(cards),
            "setups": rows,
            "skipped": skipped,
            "window_closes": closes.isoformat(),
            "metrics": {
                "target_before_stop_rate": stats["target_before_stop_rate"],
                "level_hold_rate": stats["level_hold_rate"],
                "avg_realised_r": stats["avg_realised_r"],
                "avg_expected_r": stats["avg_expected_r"],
                "no_setup_accuracy": stats["no_setup_accuracy"],
            },
        }
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=stats["target_before_stop_n"],
            hit_rate=stats["target_before_stop_rate"],
            data=data,
            now=now,
        )
        session.commit()
    return data


__all__ = [
    "AGENT",
    "CARD_TYPE",
    "Walk",
    "level_held",
    "score_day",
    "stated_range",
    "stayed_inside",
    "summarise",
    "walk_setup",
]
