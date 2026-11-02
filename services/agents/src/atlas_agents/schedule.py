"""The owner's schedule from the settings row, over config/defaults.yaml (ATL-229, decision #255).

Laravel stores the schedule as flat ``schedule.*`` keys in ``settings.data`` (ATL-228). Python code
reads times through a *defaults mapping* (``defaults["agents"]["schedule"]`` and
``defaults["schedule"]``); :func:`effective` returns DEFAULTS with those two blocks overlaid by the
row's valid values, so every existing reader (``ScheduleHook.time_of_day``, ``cycle.due_slot``,
``status.next_step``) follows the owner's settings by being handed the result.

A value of the wrong shape is ignored with a warning and the default is kept (Laravel validates on
write; this only guards a row edited by hand). A row saved before ATL-228 has none of the keys.

Quiet hours (:func:`quiet_window`): when ``schedule.quiet_hours_enabled`` and now (Asia/Baghdad) is
inside ``[start, end)``, a window that may cross midnight.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Integer, String, column, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_TIME: Final = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_WEEKLY: Final = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01]\d|2[0-3]):[0-5]\d$")

AGENT_TIME_KEYS: Final = (
    "news_morning_brief",
    "historian_regime_table",
    "scout_watchlist",
    "pm_daily_report",
    "analysis_first_slot",
    "analysis_last_slot",
)
"""``schedule.<key>`` → ``agents.schedule.<key>``, "HH:MM"."""
AGENT_WEEKLY_KEYS: Final = ("historian_weekly_review",)
QUIET_TIME_KEYS: Final = ("quiet_hours_start", "quiet_hours_end")
"""``schedule.<key>`` → ``schedule.<key>`` (top-level block)."""

_settings = table("settings", column("env", String), column("version", Integer), column("data", JSONB))


def _warn(key: str, value: Any) -> None:
    log.warning("agents: ignoring settings value %s=%r (wrong type or format)", key, value)


def effective(data: Mapping[str, Any], defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, Any]:
    """``defaults`` with ``agents.schedule`` and ``schedule`` overlaid by the row's valid values.
    Only those two blocks are copied; every other block is shared with ``defaults``."""
    agents_sched = dict(defaults["agents"]["schedule"])
    top = dict(defaults["schedule"])
    for key in AGENT_TIME_KEYS + AGENT_WEEKLY_KEYS + QUIET_TIME_KEYS:
        dotted = f"schedule.{key}"
        if dotted not in data:
            continue
        value = data[dotted]
        pattern = _WEEKLY if key in AGENT_WEEKLY_KEYS else _TIME
        if not isinstance(value, str) or not pattern.match(value):
            _warn(dotted, value)
        elif key in QUIET_TIME_KEYS:
            top[key] = value
        else:
            agents_sched[key] = value
    if "schedule.analysis_interval_minutes" in data:
        value = data["schedule.analysis_interval_minutes"]
        low, high = int(top["analysis_interval_minutes_min"]), int(top["analysis_interval_minutes_max"])
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            agents_sched["analysis_interval_minutes"] = value
        else:
            _warn("schedule.analysis_interval_minutes", value)
    if "schedule.quiet_hours_enabled" in data:
        value = data["schedule.quiet_hours_enabled"]
        if isinstance(value, bool):
            top["quiet_hours_enabled"] = value
        else:
            _warn("schedule.quiet_hours_enabled", value)
    if "schedule.stock_perps_weekend" in data:
        value = data["schedule.stock_perps_weekend"]
        if value in [str(c) for c in top["stock_perps_weekend_choices"]]:
            top["stock_perps_weekend"] = value
        else:
            _warn("schedule.stock_perps_weekend", value)
    out = dict(defaults)
    out["agents"] = {**defaults["agents"], "schedule": agents_sched}
    out["schedule"] = top
    return out


def load(session: Session, env: str, defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, Any]:
    """:func:`effective` for ``env``'s settings row (the defaults alone when there is no row)."""
    data = session.scalar(select(_settings.c.data).where(_settings.c.env == env))
    return effective(data if isinstance(data, dict) else {}, defaults)


def _hhmm(text: str) -> time:
    hours, minutes = str(text).split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class QuietWindow:
    """One quiet-hours window, as aware datetimes in the owner zone."""

    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        return self.start.isoformat()


def quiet_window(now: datetime, defaults: Mapping[str, Any] = DEFAULTS) -> QuietWindow | None:
    """The quiet-hours window containing ``now``, or None (off, or outside the window)."""
    conf = defaults["schedule"]
    if conf.get("quiet_hours_enabled") is not True:
        return None
    zone = ZoneInfo(str(defaults["timezone"]["display"]))
    local = now.astimezone(zone)
    start_at, end_at = _hhmm(conf["quiet_hours_start"]), _hhmm(conf["quiet_hours_end"])
    if start_at == end_at:
        return None
    today = local.date()
    for start_day in (today, today - timedelta(days=1)):
        start = datetime.combine(start_day, start_at, tzinfo=zone)
        end_day = start_day if end_at > start_at else start_day + timedelta(days=1)
        end = datetime.combine(end_day, end_at, tzinfo=zone)
        if start <= local < end:
            return QuietWindow(start=start, end=end)
    return None


__all__ = ["QuietWindow", "effective", "load", "quiet_window"]
