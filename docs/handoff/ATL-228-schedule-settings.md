# ATL-228 — Schedule settings keys (decision #255 (2))

For the Python task that makes the agents supervisor / Risk Engine read these, and for the
Settings → Schedule page.

## The keys

All live in the existing versioned settings row per env (`settings.data`, jsonb), go through the
existing `GET /api/settings?env=demo` and `PATCH /api/settings`, write `settings_history`,
`audit_log` and publish `system.settings_changed` on `atlas.system` (payload
`changed_fields` lists the dotted key, e.g. `["schedule.analysis_interval_minutes"]`).
The keys contain literal dots; they are flat keys, not nested objects.

| Key | Type | Default (source in config/defaults.yaml) | Rule |
| --- | --- | --- | --- |
| `schedule.news_morning_brief` | string `HH:MM` | `"07:30"` (agents.schedule.news_morning_brief) | 00:00–23:59, zero-padded |
| `schedule.historian_regime_table` | string `HH:MM` | `"07:30"` (agents.schedule.historian_regime_table) | same |
| `schedule.scout_watchlist` | string `HH:MM` | `"08:00"` (agents.schedule.scout_watchlist) | strictly after both 07:30 jobs |
| `schedule.pm_daily_report` | string `HH:MM` | `"23:30"` (agents.schedule.pm_daily_report) | HH:MM |
| `schedule.historian_weekly_review` | string `"<Day> HH:MM"` | `"Sun 18:00"` (agents.schedule.historian_weekly_review) | Day is exactly one of `Mon Tue Wed Thu Fri Sat Sun` |
| `schedule.analysis_interval_minutes` | JSON integer | `30` (agents.schedule.analysis_interval_minutes) | 15–240 (schedule.analysis_interval_minutes_min/max) |
| `schedule.analysis_first_slot` | string `HH:MM` | `"08:10"` (agents.schedule.analysis_first_slot) | strictly before last slot |
| `schedule.analysis_last_slot` | string `HH:MM` | `"23:00"` (agents.schedule.analysis_last_slot) | |
| `schedule.quiet_hours_enabled` | bool | `false` (schedule.quiet_hours_enabled) | strict JSON bool |
| `schedule.quiet_hours_start` | string `HH:MM` | `"23:00"` (schedule.quiet_hours_start) | may be later than end (crosses midnight); start ≠ end |
| `schedule.quiet_hours_end` | string `HH:MM` | `"07:00"` (schedule.quiet_hours_end) | |
| `schedule.stock_perps_weekend` | string | `"allow"` (schedule.stock_perps_weekend) | `allow` or `no_new_trades` (schedule.stock_perps_weekend_choices) |

All times are Asia/Baghdad wall-clock. `agents.schedule.scoring` (00:15), `reliability` (00:30) and
`tick_seconds` stay config-only — they are not settings keys.

Python: the fallback values are in `atlas_shared.defaults` under `agents.schedule.*` and the new
top-level `schedule.*` block. A settings row saved before ATL-228 has none of these keys;
Laravel's `SettingsService::values()` fills them from defaults on read, so a Python reader of the
raw `settings.data` must fall back to defaults.yaml for a missing key too.

## PATCH examples

```json
PATCH /api/settings
{"env": "demo", "version": 7, "changes": {"schedule.analysis_interval_minutes": 60}}
→ 200 {"data": {"version": 8, "settings": {"schedule.analysis_interval_minutes": 60, ...}, ...}}
```

```json
{"env": "demo", "version": 7, "changes": {"schedule.quiet_hours_enabled": true,
  "schedule.quiet_hours_start": "23:00", "schedule.quiet_hours_end": "07:00"}}
→ 200
```

## Validation errors (422)

Errors are keyed `changes.<dotted key>`, e.g. `errors["changes.schedule.scout_watchlist"]`.
English by default, Arabic with `Accept-Language: ar`. Field names come from
`lang/{en,ar}/settings.php` → `attr.schedule.*`.

| Case | Error key | English message |
| --- | --- | --- |
| `"25:00"` / `"7:30"` / non-string | that field | The News morning brief time must be a time between 00:00 and 23:59, written as HH:MM such as "07:30". |
| weekly `"18:00"`, `"Sunday 18:00"` | `changes.schedule.historian_weekly_review` | The Historian weekly review must be a weekday and a time, such as "Sun 18:00". |
| interval 5 / 241 / `"60"` | `changes.schedule.analysis_interval_minutes` | Laravel's `between` / `integer` message with "Analysis interval" |
| bad weekend value | `changes.schedule.stock_perps_weekend` | Laravel's `in` message with "Stock perpetuals at the weekend" |
| first slot ≥ last slot | the changed one of the two (last when both changed) | The First analysis cycle must be earlier than the Last analysis cycle. |
| scout ≤ news brief | `scout_watchlist`, or `news_morning_brief` if only that changed | The Market Scout watchlist time must be later than the News morning brief time, because Market Scout reads the morning brief. |
| scout ≤ regime table | `scout_watchlist`, or `historian_regime_table` if only that changed | ... later than the Historian regime table time, because Market Scout reads the regime table. |
| quiet start = end | the changed one (end when both) | Quiet hours must start and end at different times. |

Cross-field rules are checked against the stored values with the request's changes on top (so
moving only `analysis_last_slot` to 08:00 is refused against the stored first slot 08:10). They
run only when the request touches a `schedule.*` key and every single-field rule passed.

## Where the code is

- `SettingsSchema::scheduleFields()`, `SCHEDULE_TIME_FIELDS`, `SCHEDULE_WEEKLY_FIELDS`,
  `TIME_PATTERN`, `WEEKLY_PATTERN`, `scheduleIntervalBounds()`, `weekendChoices()`, `scheduleDefaults()`.
- `UpdateSettingsRequest::rules()` (single-field) and `::after()` (cross-field).
- Tests: `backend/tests/Feature/Settings/ScheduleSettingsApiTest.php`.

## Not built here

Nothing reads these yet: the supervisor still reads `agents.schedule.*` from config, the Risk
Engine has no weekend rule, and notifications ignore quiet hours. The Settings → Activity list in
the SPA only labels the fields its own page edits; the Schedule page task adds its labels there.
