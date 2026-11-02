# ATL-229 — Who reads the schedule.* settings, and what each one does now

Builds on `docs/handoff/ATL-228-schedule-settings.md` (the keys). This file says what changes at
runtime when the owner saves them, for the Schedule page (ATL-230) copy and for QA.

## Agents (services/agents)

- `atlas_agents.schedule.effective(data)` returns DEFAULTS with `agents.schedule.*` and the
  top-level `schedule.*` block overlaid by the row's valid `schedule.*` keys. A bad value (wrong
  type/format, interval out of 15–240) is ignored with a warning; the default is kept.
- Daily/weekly jobs (`news_morning_brief`, `historian_regime_table`, `scout_watchlist`,
  `pm_daily_report`, `historian_weekly_review`): the supervisor reads them from
  `AgentSettings.schedule_defaults`, refreshed at every tick when the settings version changes.
  A new time applies at the next tick (30 s), no restart. A job already run today is not re-run
  when its time is moved later (the `agent_runs` "done today" rule is unchanged).
- Analysis slots (`analysis_interval_minutes`, `analysis_first_slot`, `analysis_last_slot`): the
  Orchestrator reads the row on every check. Slots are `first + k × interval` up to and including
  `last`, same Baghdad day. The "Where we are" card's "next:" phrase uses the same values.
- Quiet hours: when enabled and now (Baghdad) is in `[start, end)` (crosses midnight when start >
  end), no **scheduled** cycle starts. The first check inside a window posts one system line in Main:
  `Quiet hours until 07:00.` (card `{"type": "quiet_hours", "cycle_id": null, "state": "quiet_hours"}`).
  Once per window per process (in memory), so a supervisor restart mid-window posts it once more.
  Daily jobs still run. `/cycle now` is the owner's explicit request and still starts a cycle.

## Risk Engine (trading_core)

- `schedule.stock_perps_weekend = no_new_trades`: check 2 rejects stock/ETF/index perps from
  Fri 20:00 to Sun 20:00 America/New_York (DST from the tz database), code `stock_perps_weekend`,
  detail e.g. `No new stock perp trades at the weekend (Fri 20:00 to Sun 20:00 America/New_York);
  it is Sat 12:00 there.` The window is `risk_rules.stock_perps_weekend` in config/defaults.yaml.
  An unknown stored value is read as `no_new_trades` (never fails open). Crypto/commodity pass.

## Notifications (backend)

- `App\Support\Notifications\QuietHours`: during quiet hours the Notifier stores
  `channels.browser = false` and `channels.telegram = false` for every event not in
  `notifications.quiet_hours_always_deliver` (circuit_breaker, kill_switch, stop_hit, target_hit,
  proposal_awaiting_approval). The row is still written and still broadcast to the in-app list
  when `in_app` is on; the SPA raises a browser banner only when `channels.browser` is true, and the
  Telegram listener queues only when `channels.telegram` is true. Quiet hours are judged at
  delivery time (`now()`), not the envelope's ts.
