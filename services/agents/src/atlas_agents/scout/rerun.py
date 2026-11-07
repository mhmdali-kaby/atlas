"""Market Scout's intraday re-run triggers (brief §7.1 step 6, ATL-109). Code only, no model.

Two triggers re-run today's watchlist (``atlas_agents.agents.market_scout`` wires them in):

* a ``news.alert`` whose ``importance`` is at least ``agents.scout.rerun_alert_importance`` (5);
* a **volatility spike**: an instrument in the universe that is not on today's watchlist, whose
  latest closed 1H bar moved more than ``agents.scout.spike_sigma`` (3) standard deviations of its
  previous ``spike_lookback_days`` (30) of 1H returns. "Latest closed" means the bar that opened one
  hour before the current hour: a feed that is behind triggers nothing.

A re-run starts only inside ``agents.scout.rerun_window`` (08:10-23:00 Asia/Baghdad, start
inclusive, end exclusive) and at most once per ``rerun_min_minutes`` (60). The debounce is kept in
memory per supervisor and also read from today's saved re-run watchlists, so a restart does not
re-run twice.

The re-run card carries ``trigger`` (:func:`news_trigger` / :func:`spike_trigger`) and ``changes``
(:func:`changes`) against the previous version that day.
"""

from __future__ import annotations

import math
import weakref
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd
from atlas_quant import load_candles
from atlas_shared.db import Watchlist
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.tools import ToolContext
from atlas_agents.watchlists import current_watchlist, listed_ids

NEWS_ALERT: Final = "news_alert"
VOL_SPIKE: Final = "vol_spike"
KINDS: Final = (NEWS_ALERT, VOL_SPIKE)
BAR: Final = "1H"
_HOUR: Final = timedelta(hours=1)


# -- config -----------------------------------------------------------------------------------------


def _scout(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    scout: Mapping[str, Any] = defaults["agents"]["scout"]
    return scout


def spike_sigma(defaults: Mapping[str, Any] = DEFAULTS) -> float:
    return float(str(_scout(defaults)["spike_sigma"]))


def min_minutes(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(_scout(defaults)["rerun_min_minutes"])


def alert_importance(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(_scout(defaults)["rerun_alert_importance"])


def check_seconds(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(_scout(defaults)["spike_check_seconds"])


def lookback_days(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(_scout(defaults)["spike_lookback_days"])


def min_returns(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(_scout(defaults)["spike_min_returns"])


def _hhmm(text: str) -> time:
    hours, minutes = str(text).split(":")
    return time(int(hours), int(minutes))


def owner_zone(defaults: Mapping[str, Any] = DEFAULTS) -> ZoneInfo:
    return ZoneInfo(str(defaults["timezone"]["display"]))


def in_window(now: datetime, defaults: Mapping[str, Any] = DEFAULTS) -> bool:
    """True when ``now`` (Asia/Baghdad wall clock) is inside ``rerun_window``."""
    window = _scout(defaults)["rerun_window"]
    local = now.astimezone(owner_zone(defaults)).time()
    return _hhmm(window["start"]) <= local < _hhmm(window["end"])


# -- triggers ---------------------------------------------------------------------------------------


def alert_qualifies(payload: Mapping[str, Any], defaults: Mapping[str, Any] = DEFAULTS) -> bool:
    """An alert re-runs the watchlist only at importance >= ``rerun_alert_importance``."""
    importance = payload.get("importance")
    if isinstance(importance, bool):
        return False
    try:
        value = int(importance)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return value >= alert_importance(defaults)


def news_trigger(
    *, message_id: str | None, event_id: str, headline: str | None, importance: int, at: datetime
) -> dict[str, Any]:
    """The card's ``trigger`` for a news alert. ``message_id`` is the alert's warroom_messages row
    (null when the bridge had not stored it); ``event_id`` is the alert envelope's id."""
    return {
        "kind": NEWS_ALERT,
        "message_id": message_id,
        "event_id": event_id,
        "headline": headline,
        "importance": importance,
        "at": at.astimezone(UTC).isoformat(),
    }


@dataclass(frozen=True)
class Spike:
    inst_id: str
    sigma: float
    """|latest 1H return| / stdev of the previous 1H returns."""
    bar_ts: datetime
    """Open time of the spiking bar (UTC)."""
    ret: float
    stdev: float
    n: int


def spike_trigger(spike: Spike) -> dict[str, Any]:
    """The card's ``trigger`` for a volatility spike. ``at`` is when the bar closed."""
    return {
        "kind": VOL_SPIKE,
        "inst_id": spike.inst_id,
        "sigma": round(spike.sigma, 2),
        "bar": BAR,
        "bar_ts": spike.bar_ts.astimezone(UTC).isoformat(),
        "at": (spike.bar_ts + _HOUR).astimezone(UTC).isoformat(),
    }


def latest_closed_bar(now: datetime) -> datetime:
    """Open time of the 1H bar that closed most recently at ``now`` (UTC hour grid)."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return hour - _HOUR


def measure_spike(
    closes: pd.Series[float], expected_ts: datetime, *, lookback: int, minimum: int
) -> tuple[float, float, float, int] | None:
    """(sigma, latest return, stdev, n) for a close series indexed by bar open time, or None when the
    last bar is not ``expected_ts``, the history is shorter than ``minimum`` returns, or the stdev
    is zero. The latest return is not part of the stdev (no look-ahead into its own baseline)."""
    if closes.empty or pd.Timestamp(closes.index[-1]) != pd.Timestamp(expected_ts):
        return None
    returns = closes.astype(float).pct_change().dropna()
    if len(returns) < minimum + 1:
        return None
    latest = float(returns.iloc[-1])
    history = returns.iloc[:-1].iloc[-lookback:]
    if len(history) < minimum:
        return None
    stdev = float(history.std(ddof=1))
    if not math.isfinite(stdev) or stdev <= 0 or not math.isfinite(latest):
        return None
    return abs(latest) / stdev, latest, stdev, len(history)


def find_spikes(
    session: Session,
    ctx: ToolContext,
    inst_ids: Iterable[str],
    *,
    exclude: Iterable[str] = (),
    defaults: Mapping[str, Any] = DEFAULTS,
) -> list[Spike]:
    """Instruments of ``inst_ids`` (minus ``exclude``) whose latest closed 1H bar spiked above
    ``spike_sigma``, strongest first."""
    now = ctx.now()
    expected = latest_closed_bar(now)
    days = lookback_days(defaults)
    lookback = days * 24
    minimum = min_returns(defaults)
    threshold = spike_sigma(defaults)
    skip = {i.upper() for i in exclude}
    start = expected - timedelta(days=days) - _HOUR
    out: list[Spike] = []
    for inst_id in inst_ids:
        if inst_id.upper() in skip:
            continue
        candles = load_candles(session, inst_id, BAR, start, now, as_of=now, env=ctx.env)
        if candles.df.empty:
            continue
        measured = measure_spike(candles.df["c"], expected, lookback=lookback, minimum=minimum)
        if measured is None:
            continue
        sigma, ret, stdev, n = measured
        if sigma > threshold:
            out.append(Spike(inst_id=inst_id, sigma=sigma, bar_ts=expected, ret=ret, stdev=stdev, n=n))
    out.sort(key=lambda s: (-s.sigma, s.inst_id))
    return out


# -- today's watchlist, changes, debounce -------------------------------------------------------------


def changes(
    previous: Watchlist | None, previous_ids: Sequence[str], new_ids: Sequence[str]
) -> dict[str, Any]:
    """``{since, version, added, dropped}`` of the new pick list against the previous version of the
    day (``since``/``version`` null and everything ``added`` when there was none)."""
    before = set(previous_ids)
    after = set(new_ids)
    return {
        "since": None
        if previous is None or previous.created_at is None
        else previous.created_at.astimezone(UTC).isoformat(),
        "version": None if previous is None else previous.version,
        "added": [i for i in new_ids if i not in before],
        "dropped": [i for i in previous_ids if i not in after],
    }


def last_saved_rerun(session: Session, ctx: ToolContext, day: date, author: str) -> datetime | None:
    """created_at of the newest re-run watchlist saved today (data.trigger.kind is a re-run kind)."""
    found = session.scalars(
        select(Watchlist.created_at)
        .where(
            Watchlist.env == ctx.env,
            Watchlist.date == day,
            Watchlist.created_by == author,
            Watchlist.data["trigger"]["kind"].astext.in_(KINDS),
            ctx.bound(Watchlist.created_at),
        )
        .order_by(Watchlist.created_at.desc())
        .limit(1)
    ).first()
    return found


class Debounce:
    """When each supervisor (host) last started a re-run. Weak keys: a closed supervisor is dropped."""

    def __init__(self) -> None:
        self._last: weakref.WeakKeyDictionary[Any, datetime] = weakref.WeakKeyDictionary()

    def blocked(self, host: Any, now: datetime, saved: datetime | None, minutes: int) -> bool:
        window = timedelta(minutes=minutes)
        for last in (self._last.get(host), saved):
            if last is not None and abs(now - last) < window:
                return True
        return False

    def mark(self, host: Any, now: datetime) -> None:
        self._last[host] = now

    def clear(self) -> None:
        self._last.clear()


DEBOUNCE: Final = Debounce()


# -- card text ------------------------------------------------------------------------------------


def trigger_words(trigger: Mapping[str, Any]) -> str:
    """The trigger in words, for the Scout prompt (the untrusted headline is left out)."""
    if trigger.get("kind") == VOL_SPIKE:
        return (
            f"a volatility spike: {trigger.get('inst_id')} moved {trigger.get('sigma')} sigma on the "
            f"1H bar that closed at {trigger.get('at')}"
        )
    return f"an importance-{trigger.get('importance')} news alert (event {trigger.get('event_id')})"


def rerun_line(trigger: Mapping[str, Any], changes: Mapping[str, Any] | None) -> str:
    """The card text's first line on a re-run: what triggered it and what changed (code-written)."""
    if trigger.get("kind") == VOL_SPIKE:
        why = (
            f"{trigger.get('inst_id')} moved {trigger.get('sigma')} sigma on the 1H bar closed at "
            f"{trigger.get('at')}"
        )
    else:
        headline = str(trigger.get("headline") or "").strip() or "(no headline)"
        why = f"importance-{trigger.get('importance')} news alert: {headline}"
    line = f"**Intraday re-run** ({why})."
    if changes is not None:
        added = ", ".join(changes.get("added") or []) or "none"
        dropped = ", ".join(changes.get("dropped") or []) or "none"
        line += f" Added: {added}. Dropped: {dropped}."
    return line


__all__ = [
    "DEBOUNCE",
    "KINDS",
    "NEWS_ALERT",
    "VOL_SPIKE",
    "Debounce",
    "Spike",
    "alert_qualifies",
    "changes",
    "current_watchlist",
    "find_spikes",
    "in_window",
    "last_saved_rerun",
    "latest_closed_bar",
    "listed_ids",
    "measure_spike",
    "news_trigger",
    "rerun_line",
    "spike_trigger",
    "trigger_words",
]
