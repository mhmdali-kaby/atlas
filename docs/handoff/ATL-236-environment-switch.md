# ATL-236 — stored active env, live pre-flight, guarded switch (backend)

For ATL-237 (trading_core/agents follow the env), ATL-238 (first-month limits), ATL-239 and the
Settings → Environment page.

## Storage

Table `system_state`, **one row, `id = 1`** (a CHECK enforces it):

| column | type | meaning |
| --- | --- | --- |
| `active_env` | `demo`\|`live` | the env Atlas trades in |
| `live_started_at` | timestamptz null | set by the **first** switch to live, never cleared (switching back to demo keeps it) |
| `switched_at` | timestamptz null | last switch |
| `preflight_attestations` | jsonb | `{"ip_allowlist": {"confirmed_at": "2026-11-02T07:10:00.000000Z"}, …}`; a cleared item is absent |

No row is created by the migration. It is written by the first attestation or switch.
`App\Support\System\ActiveEnv::get()` reads `active_env`, and falls back to
`config('atlas_defaults.env.default')` **only while the row is missing** (so existing tests that set
the config still work). Python: read `SELECT active_env FROM system_state WHERE id = 1`, with the same
fallback.

Defaults (config/defaults.yaml → `env.live_preflight`): `min_demo_days: 28`, `max_live_autonomy: 2`,
`first_month_days: 30`, `switch_attempts_per_minute: 5`, `confirmation_text: TRADE LIVE`.

## GET /api/environment (auth:sanctum)

```json
{"data": {
  "active_env": "demo",
  "live_started_at": null,
  "first_month_ends_at": null,
  "in_first_live_month": false,
  "switched_at": null,
  "confirmation_text": "TRADE LIVE",
  "preflight": {"ok": false, "items": [
    {"key": "live_connection",   "kind": "computed", "ok": false, "detail": "No live OKX connection. Add one in Settings → Connections."},
    {"key": "demo_history",      "kind": "computed", "ok": false, "detail": "12 of 28 days of demo history."},
    {"key": "live_risk_profile", "kind": "computed", "ok": true,  "detail": "Live risk profile: Balanced."},
    {"key": "live_autonomy",     "kind": "computed", "ok": true,  "detail": "Live autonomy is level 2."},
    {"key": "live_capital_cap",  "kind": "computed", "ok": false, "detail": "No live bot capital cap. Set one in Settings → Money limits with Live selected."},
    {"key": "ip_allowlist",      "kind": "attested", "ok": true,  "detail": "Confirmed by the owner at 2026-11-02T07:10:00.000000Z."},
    {"key": "sub_account",       "kind": "attested", "ok": false, "detail": "Confirm that the live key belongs to a sub-account used only by Atlas."},
    {"key": "demo_reviewed",     "kind": "attested", "ok": false, "detail": "Confirm that you have reviewed the demo results."}
  ]}
}}
```

Always 8 items in that order. `detail` is translated (`Accept-Language: ar`). Item labels for the UI
are in `lang/*/environment.php` → `items.<key>` (server side); the SPA will need its own copy.

Computed rules (`App\Support\System\Preflight`):
- `live_connection`: the okx/live connection has `last_verified_at` **and** `withdraw_off_verified_at` (ATL-235).
- `demo_history`: the earliest `equity_curve` row with env demo is ≥ 28 days old.
- `live_risk_profile`: a live `settings` row exists. GET never creates it; it is created the first
  time anything reads `/api/settings?env=live`.
- `live_autonomy`: the live row's `autonomy_level` ≤ 2 (false when there is no live row).
- `live_capital_cap`: the live row's `bot_capital_cap_usdt` is set and > 0.

## PUT /api/environment/attestations (throttle 60/min)

Body: any of `ip_allowlist`, `sub_account`, `demo_reviewed` as strict booleans. `true` stamps
`confirmed_at` (kept if already set), `false` clears. Returns the same body as GET. One audit row
`environment.attestations_updated` (before/after = confirmed_at per changed key) when anything changed.

## POST /api/environment/switch (throttle `environment-switch`, 5/min per user)

To live: `{"target": "live", "password": "…", "confirmation": "TRADE LIVE"}`.
To demo: `{"target": "demo", "confirm": true}` (no password, no pre-flight).

- 200: the GET body, after the switch.
- 409 `{"message": "Atlas is already trading in Demo.", "error": "already_active"}` when target is current.
- 422, wrong password — reported **alone** (no confirmation/pre-flight info):
  `{"message": "The password is incorrect.", "errors": {"password": ["The password is incorrect."]}, "failing_items": []}`
- 422, right password but other failures (both can appear together):
  `{"message": "…", "errors": {"confirmation": ["Type TRADE LIVE exactly, in capital letters."], "preflight": ["Pre-flight not passed: Demo results reviewed."]}, "failing_items": ["demo_reviewed"]}`
- 422 missing fields: normal Laravel validation (`password`, `confirmation`, `confirm`, `target`).
- 429 on the 6th attempt in a minute (successful ones count too).

On success, in one transaction: `active_env` set, `switched_at` = now, `live_started_at` set if null
(live only), `trading_state` for the **new** env set `paused = true` (row created if missing, reason
"Paused after switching environment. Resume when you are ready."), one audit row
`environment.switched` (subject `system_state`/`1`, before `{active_env}`, after
`{active_env, live_started_at, trading_paused_env}`). After commit, on `atlas.system`
(envelope env = the new env, source `api:environment`):

```json
{"type": "system.env_changed", "payload": {"from": "demo", "to": "live", "at": "2026-11-02T07:12:00.000000Z"}}
{"type": "system.trading_state", "payload": {"paused": true, "reason": "Paused after switching environment. …", "paused_at": "…", "by": "environment_switch", "active_breakers": [], "breakers_cleared": []}}
```

A Redis failure is reported, not raised: the switch stays committed. Consumers should also read
`system_state` on start.

## Not built here

- trading_core / agents reacting to `system.env_changed` (ATL-237).
- First-month enforcement (autonomy cap at check 9, the cap required at check 1, settings refusing
  autonomy > 2): ATL-238. `first_month_ends_at` / `in_first_live_month` are here for it and the UI.
- No UI.

## Note

`database/migrations/2026_11_02_600000_add_withdraw_off_verified_at_to_connections.php` is a byte-for-byte
copy of ATL-235's migration (still in review when this was built), so the pre-flight could be tested.
When both land, git sees the same file added twice with identical content.
