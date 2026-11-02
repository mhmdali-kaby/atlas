"""ATL-229 (decision #255 (2)): the agents follow the owner's ``schedule.*`` settings live.

* The supervisor's due-job check reads job times from the env's settings row (falling back to
  config/defaults.yaml) at every tick, so a changed time applies with no restart.
* The Orchestrator's slots follow ``analysis_interval_minutes`` / first / last slot.
* Quiet hours: no scheduled cycle starts inside the window; one system line per window.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from atlas_agents import schedule
from atlas_agents import testing as fake
from atlas_agents.agents import orchestrator as orch_hooks
from atlas_agents.orchestrator import cycle
from atlas_agents.registry import REGISTRY, AgentHost, HookRegistry, ScheduleFire
from atlas_agents.session import RunResult
from atlas_agents.supervisor import Supervisor
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, delete, text

from .conftest import Recorder

ENV = "demo"
BAGHDAD = ZoneInfo("Asia/Baghdad")
SCOUT = "market_scout"
ORCH = "orchestrator"
DAY = date(2099, 3, 4)  # a Wednesday


def baghdad(hh: int, mm: int, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=BAGHDAD).astimezone(UTC)


# -- the settings row -----------------------------------------------------------------------------------


class SettingsRow:
    """The demo env's settings row, written as Laravel's SettingsService would (version bumps)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.version = 1000
        self.data: dict[str, Any] = {}

    def save(self, **changes: Any) -> None:
        self.data.update(changes)
        self.version += 1
        with self._engine.begin() as conn:
            conn.execute(text("delete from settings where env = :e"), {"e": ENV})
            conn.execute(
                text(
                    "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
                    "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
                    "values (:id, :env, :v, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, now(), now())"
                ),
                {"id": uuid.uuid4(), "env": ENV, "v": self.version, "data": json.dumps(self.data)},
            )


@pytest.fixture
def settings_row(engine: Engine) -> Iterator[SettingsRow]:
    with engine.begin() as conn:
        saved = conn.execute(text("select * from settings where env = :e"), {"e": ENV}).mappings().first()
    row = SettingsRow(engine)
    yield row
    with engine.begin() as conn:
        conn.execute(text("delete from settings where env = :e"), {"e": ENV})
        if saved is not None:
            values = dict(saved)
            values["data"] = json.dumps(values["data"])
            cols = ", ".join(values)
            params = ", ".join(f"cast(:{c} as jsonb)" if c == "data" else f":{c}" for c in values)
            conn.execute(text(f"insert into settings ({cols}) values ({params})"), values)  # noqa: S608


def _wipe(engine: Engine, *agents: str) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent.in_(agents), model.env == ENV))


# -- schedule.effective / quiet_window (no DB) ----------------------------------------------------------


def test_effective_overlays_valid_values_and_keeps_defaults_for_bad_ones() -> None:
    got = schedule.effective(
        {
            "schedule.scout_watchlist": "09:15",
            "schedule.historian_weekly_review": "Sat 10:00",
            "schedule.analysis_interval_minutes": 60,
            "schedule.quiet_hours_enabled": True,
            "schedule.quiet_hours_start": "22:00",
            "schedule.pm_daily_report": "25:00",  # bad: default kept
            "schedule.analysis_first_slot": 900,  # bad
            "schedule.stock_perps_weekend": "never",  # bad
        }
    )
    sched, top = got["agents"]["schedule"], got["schedule"]
    assert sched["scout_watchlist"] == "09:15"
    assert sched["historian_weekly_review"] == "Sat 10:00"
    assert sched["analysis_interval_minutes"] == 60
    assert sched["pm_daily_report"] == DEFAULTS["agents"]["schedule"]["pm_daily_report"]
    assert sched["analysis_first_slot"] == DEFAULTS["agents"]["schedule"]["analysis_first_slot"]
    assert sched["tick_seconds"] == DEFAULTS["agents"]["schedule"]["tick_seconds"]  # config-only
    assert top["quiet_hours_enabled"] is True and top["quiet_hours_start"] == "22:00"
    assert top["stock_perps_weekend"] == "allow"
    assert DEFAULTS["agents"]["schedule"]["scout_watchlist"] == "08:00"  # DEFAULTS untouched
    assert schedule.effective({})["agents"]["schedule"] == DEFAULTS["agents"]["schedule"]


@pytest.mark.parametrize("value", [True, 5, 241, "60"])
def test_an_out_of_bounds_interval_is_ignored(value: Any) -> None:
    got = schedule.effective({"schedule.analysis_interval_minutes": value})
    assert got["agents"]["schedule"]["analysis_interval_minutes"] == 30


QUIET = {
    "schedule.quiet_hours_enabled": True,
    "schedule.quiet_hours_start": "23:00",
    "schedule.quiet_hours_end": "07:00",
}


def test_quiet_window_crosses_midnight() -> None:
    defaults = schedule.effective(QUIET)
    late = schedule.quiet_window(baghdad(23, 30), defaults)
    early = schedule.quiet_window(baghdad(2, 0, date(2099, 3, 5)), defaults)
    assert late is not None and early is not None
    assert late == early  # the same window, so one announcement
    assert late.start == datetime(2099, 3, 4, 23, 0, tzinfo=BAGHDAD)
    assert late.end == datetime(2099, 3, 5, 7, 0, tzinfo=BAGHDAD)
    assert schedule.quiet_window(baghdad(22, 59), defaults) is None
    assert schedule.quiet_window(baghdad(7, 0), defaults) is None
    assert schedule.quiet_window(baghdad(12, 0), defaults) is None
    off = schedule.effective({**QUIET, "schedule.quiet_hours_enabled": False})
    assert schedule.quiet_window(baghdad(23, 30), off) is None


def test_a_same_day_quiet_window() -> None:
    defaults = schedule.effective(
        {**QUIET, "schedule.quiet_hours_start": "12:00", "schedule.quiet_hours_end": "14:00"}
    )
    window = schedule.quiet_window(baghdad(13, 0), defaults)
    assert window is not None and window.end == datetime(2099, 3, 4, 14, 0, tzinfo=BAGHDAD)
    assert schedule.quiet_window(baghdad(14, 0), defaults) is None


# -- AC2: the Orchestrator's slots ------------------------------------------------------------------------


def test_slots_follow_interval_first_and_last_from_settings() -> None:
    defaults = schedule.effective(
        {
            "schedule.analysis_interval_minutes": 60,
            "schedule.analysis_first_slot": "10:00",
            "schedule.analysis_last_slot": "12:00",
        }
    )
    slots = {cycle.due_slot(baghdad(hh, mm), defaults) for hh in range(24) for mm in range(0, 60, 5)}
    assert slots - {None} == {baghdad(10, 0), baghdad(11, 0), baghdad(12, 0)}
    assert cycle.due_slot(baghdad(9, 59), defaults) is None
    assert cycle.due_slot(baghdad(10, 59), defaults) == baghdad(10, 0)
    assert cycle.due_slot(baghdad(12, 30), defaults) == baghdad(12, 0)  # still the 12:00 slot
    assert cycle.due_slot(baghdad(13, 0), defaults) is None


# -- AC1: the supervisor's due-job check follows the settings row ----------------------------------------


@pytest.fixture
def scout_sup(engine: Engine, tmp_path: Path) -> Iterator[tuple[Supervisor, list[datetime]]]:
    _wipe(engine, SCOUT)
    fired: list[datetime] = []
    hooks = HookRegistry()

    @hooks.schedule(SCOUT, "scout_watchlist")
    async def job(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
        fired.append(fire.at)
        return None

    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={SCOUT: dataclasses.replace(REGISTRY[SCOUT], enabled=True)},
        hooks=hooks.hooks(),
        client_factory=fake.factory(fake.FakeClaudeClient()),
        publisher=Recorder(),
        prompts_dir=tmp_path,
        clock=lambda: baghdad(8, 30),
    )
    yield sup, fired
    _wipe(engine, SCOUT)


async def test_scout_time_from_settings_applies_at_the_next_tick_with_no_restart(
    settings_row: SettingsRow, scout_sup: tuple[Supervisor, list[datetime]]
) -> None:
    sup, fired = scout_sup
    settings_row.save(**{"schedule.news_morning_brief": "07:30"})  # no scout key: default 08:00
    await sup.tick(baghdad(8, 30))
    assert sup.due("scout_watchlist", baghdad(8, 30)) == DAY
    assert fired == [baghdad(8, 30)]

    settings_row.save(**{"schedule.scout_watchlist": "09:15"})  # the owner moves it; same process
    fired.clear()
    await sup.tick(baghdad(8, 30))
    assert sup.due("scout_watchlist", baghdad(8, 30)) is None
    assert fired == []
    await sup.tick(baghdad(9, 14))
    assert fired == []
    await sup.tick(baghdad(9, 15))
    assert sup.due("scout_watchlist", baghdad(9, 15)) == DAY
    assert fired == [baghdad(9, 15)]
    await sup.close()


async def test_a_weekly_job_day_comes_from_settings(
    settings_row: SettingsRow, scout_sup: tuple[Supervisor, list[datetime]]
) -> None:
    sup, _fired = scout_sup
    settings_row.save(**{"schedule.historian_weekly_review": "Wed 09:00"})
    await sup.refresh_settings()
    sched = sup.schedule_defaults["agents"]["schedule"]
    assert sched["historian_weekly_review"] == "Wed 09:00"
    assert sup.due("historian_weekly_review", baghdad(9, 0), weekday=2) == DAY
    await sup.close()


# -- AC2: quiet hours stop new cycles; one system line per window -----------------------------------------


@pytest.fixture
def orch_sup(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Supervisor, Recorder, list[Any]]]:
    _wipe(engine, ORCH)
    orch_hooks._quiet_announced.clear()
    started: list[Any] = []
    monkeypatch.setattr(cycle, "start", lambda host, kind, **kw: started.append(kw.get("slot_at")))
    monkeypatch.setattr(cycle.CycleRunner, "slot_taken", lambda self, slot: False)
    rec = Recorder()
    sup = Supervisor(
        env=ENV,
        engine=engine,
        registry={ORCH: dataclasses.replace(REGISTRY[ORCH], enabled=True)},
        hooks={},
        client_factory=fake.factory(fake.FakeClaudeClient()),
        publisher=rec,
        prompts_dir=tmp_path,
        clock=lambda: baghdad(12, 0),
    )
    yield sup, rec, started
    orch_hooks._quiet_announced.clear()
    _wipe(engine, ORCH)


def _quiet_lines(rec: Recorder) -> list[str]:
    return [
        e.payload["content_md"]
        for e in rec.of("warroom.message")
        if (e.payload.get("card") or {}).get("type") == orch_hooks.QUIET_CARD
    ]


async def _check(sup: Supervisor, at: datetime) -> None:
    fire = ScheduleFire(
        name=orch_hooks.SCHEDULE_JOB, at=at, day=at.astimezone(BAGHDAD).date(), correlation_id="x"
    )
    await orch_hooks.on_schedule(sup, fire)


async def test_quiet_hours_start_no_cycle_and_post_one_line_per_window(
    settings_row: SettingsRow, orch_sup: tuple[Supervisor, Recorder, list[Any]]
) -> None:
    sup, rec, started = orch_sup
    # Slots every 15 min all day, so only quiet hours can stop a cycle at 23:30 and 02:00.
    settings_row.save(
        **QUIET,
        **{
            "schedule.analysis_interval_minutes": 15,
            "schedule.analysis_first_slot": "00:00",
            "schedule.analysis_last_slot": "23:45",
        },
    )
    next_day = date(2099, 3, 5)
    for at in (baghdad(23, 0), baghdad(23, 30), baghdad(2, 0, next_day), baghdad(6, 59, next_day)):
        await _check(sup, at)
    assert started == []
    assert _quiet_lines(rec) == ["Quiet hours until 07:00."]
    line = next(e for e in rec.of("warroom.message") if e.payload["card"]["type"] == orch_hooks.QUIET_CARD)
    assert line.payload["author_type"] == "system" and line.payload["thread"]["kind"] == "main"

    await _check(sup, baghdad(7, 0, next_day))  # the window is over: cycles start again
    assert started == [baghdad(7, 0, next_day)]

    await _check(sup, baghdad(23, 15, next_day))  # the next night's window gets its own line
    assert _quiet_lines(rec) == ["Quiet hours until 07:00.", "Quiet hours until 07:00."]
    assert started == [baghdad(7, 0, next_day)]
    await sup.close()


async def test_with_quiet_hours_off_cycles_start_at_night(
    settings_row: SettingsRow, orch_sup: tuple[Supervisor, Recorder, list[Any]]
) -> None:
    sup, rec, started = orch_sup
    settings_row.save(
        **{**QUIET, "schedule.quiet_hours_enabled": False},
        **{
            "schedule.analysis_interval_minutes": 15,
            "schedule.analysis_first_slot": "00:00",
            "schedule.analysis_last_slot": "23:45",
        },
    )
    await _check(sup, baghdad(23, 30))
    assert started == [baghdad(23, 30)]
    assert _quiet_lines(rec) == []
    await sup.close()


async def test_the_orchestrator_slots_follow_a_settings_change_with_no_restart(
    settings_row: SettingsRow, orch_sup: tuple[Supervisor, Recorder, list[Any]]
) -> None:
    sup, _rec, started = orch_sup
    settings_row.save(**{"schedule.analysis_first_slot": "08:10"})
    await _check(sup, baghdad(10, 0))
    assert started == [baghdad(9, 40)]  # default 30-min grid from 08:10
    settings_row.save(
        **{
            "schedule.analysis_interval_minutes": 60,
            "schedule.analysis_first_slot": "10:00",
            "schedule.analysis_last_slot": "12:00",
        }
    )
    started.clear()
    for hh in (9, 10, 11, 12, 13):
        await _check(sup, baghdad(hh, 0))
    assert started == [baghdad(10, 0), baghdad(11, 0), baghdad(12, 0)]
    await sup.close()
