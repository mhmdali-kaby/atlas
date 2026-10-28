# ATL-3 — Versioned settings API, history, audit log, settings_changed event

## The two-minute version

- One `settings` row per env (`demo`, `live`), created at **version 1** from the ATL-1 defaults
  (Balanced profile, autonomy 2) the first time anyone reads it. Never insert it yourself.
- Write settings **only** through `App\Support\Settings\SettingsService::update()` (the PATCH endpoint
  calls it). It locks the row, checks the version, bumps it, writes `settings_history` + `audit_log`,
  then publishes `system.settings_changed` to Redis stream `atlas.system`.
- Decimals (percentages, R, bps, USD) travel and are stored as **strings** (`"1.0"`). Integers
  (leverage, counts, autonomy) are JSON **integers**. A PATCH that sends `1.5` (a float) for a decimal, or
  `"5"` (a string) for an integer, gets 422.
- Percentages are in percent units: `"1.0"` = 1 % of equity.
- `data` jsonb is authoritative. The typed columns (`risk_profile`, `autonomy_level`,
  `max_loss_per_trade_pct`, `max_position_pct`, `max_leverage`) mirror it for cheap SQL reads.
- Any other feature that writes something calls `App\Support\Auditable::record()` (see below).

## Fields

JSON key = the key inside `settings` in responses and inside `changes` in PATCH.

| JSON key | Type | Allowed range | Default (balanced) |
| --- | --- | --- | --- |
| `risk_profile` | string | `conservative` \| `balanced` \| `aggressive` \| `custom` | `balanced` |
| `max_loss_per_trade_pct` | decimal string | 0 < x ≤ 100 | `"1.0"` |
| `max_position_pct` | decimal string | 0 < x ≤ 100 | `"5"` |
| `max_daily_loss_pct` | decimal string | 0 < x ≤ 100 | `"4"` |
| `max_weekly_loss_pct` | decimal string | 0 < x ≤ 100 | `"10"` |
| `max_leverage` | int | 1–125 | `5` |
| `max_open_positions` | int | 1–100 | `6` |
| `max_share_per_instrument_pct` | decimal string | 0 < x ≤ 100 | `"25"` |
| `max_correlated_exposure_pct` | decimal string | 0 < x ≤ 100 | `"35"` |
| `min_expected_r` | decimal string | 0 < x ≤ 20 | `"1.8"` |
| `max_consecutive_losses` | int | 1–50 | `3` |
| `autonomy_level` | int | 1–4 | `2` |
| `max_instruments_per_day` | int | 1–10 | `5` |
| `max_spread_bps` | decimal string | 0 < x ≤ 10000 | `"8"` |
| `min_24h_volume_usd_crypto_perp` | decimal string | 0 ≤ x ≤ 1e12 | `"20000000"` |
| `min_24h_volume_usd_stock_perp` | decimal string | 0 ≤ x ≤ 1e12 | `"5000000"` |
| `min_24h_volume_usd_commodity_perp` | decimal string | 0 ≤ x ≤ 1e12 | `"5000000"` |
| `min_24h_volume_usd_spot` | decimal string | 0 ≤ x ≤ 1e12 | `"10000000"` |

Decimal strings must match `^\d{1,15}(\.\d{1,8})?$` (no sign, no exponent). The source of these
ranges is `App\Support\Settings\SettingsSchema::NUMERIC_FIELDS`; the brief gives percentages, leverage,
autonomy and instruments-per-day ranges, the rest are our own upper bounds.

The ten **risk fields** a preset sets (brief §14.3): `max_loss_per_trade_pct`, `max_position_pct`,
`max_daily_loss_pct`, `max_weekly_loss_pct`, `max_leverage`, `max_open_positions`,
`max_share_per_instrument_pct`, `max_correlated_exposure_pct`, `min_expected_r`, `max_consecutive_losses`.

### Profile rules

- `changes.risk_profile` = a preset → the ten risk fields are replaced by that preset.
- Any risk field edited so it no longer equals the profile's preset → profile becomes `custom`.
  Equality is numeric (`"5"` equals `"5.0"`).
- Preset + risk fields in one PATCH: the preset is applied first, then the fields; if they differ from
  the preset the result is `custom`.
- `risk_profile: "custom"` keeps the current values and just labels them custom.
- A PATCH that changes nothing returns 200 with the **same** version: no history, no audit, no event.

Not here yet (later tasks add them to `config/defaults.yaml` and `SettingsSchema`): money limits in USDT,
blocklist/whitelist, asset-class toggles, schedule, agents. When a new field is added, rows saved before
it get the default on read (`SettingsService::values()`).

## Endpoints

All need the Sanctum session (`auth:sanctum`, see ATL-2). Unauthenticated → 401.

### GET /api/settings?env=demo

`env` optional (defaults to `demo`); anything other than `demo`/`live` → 422. Always 200.

```json
{
  "data": {
    "id": "9f6c0a7e-3a1b-4d0e-9a51-2b7f1c2d3e4f",
    "env": "demo",
    "version": 1,
    "settings": {
      "risk_profile": "balanced",
      "max_loss_per_trade_pct": "1.0",
      "max_position_pct": "5",
      "max_daily_loss_pct": "4",
      "max_weekly_loss_pct": "10",
      "max_leverage": 5,
      "max_open_positions": 6,
      "max_share_per_instrument_pct": "25",
      "max_correlated_exposure_pct": "35",
      "min_expected_r": "1.8",
      "max_consecutive_losses": 3,
      "autonomy_level": 2,
      "max_instruments_per_day": 5,
      "max_spread_bps": "8",
      "min_24h_volume_usd_crypto_perp": "20000000",
      "min_24h_volume_usd_stock_perp": "5000000",
      "min_24h_volume_usd_commodity_perp": "5000000",
      "min_24h_volume_usd_spot": "10000000"
    },
    "presets": {
      "conservative": { "max_loss_per_trade_pct": "0.5", "max_position_pct": "2", "max_daily_loss_pct": "2", "max_weekly_loss_pct": "5", "max_leverage": 2, "max_open_positions": 3, "max_share_per_instrument_pct": "15", "max_correlated_exposure_pct": "20", "min_expected_r": "2.0", "max_consecutive_losses": 2 },
      "balanced": { "...": "same ten keys" },
      "aggressive": { "...": "same ten keys" }
    },
    "updated_at": "2026-10-28T21:04:11.123456Z"
  }
}
```

`presets` is there so the Settings → Risk screen can preview a preset before saving it.

### PATCH /api/settings

```json
{ "env": "demo", "version": 1, "changes": { "risk_profile": "conservative" } }
```

- `version` (int, required) is the version the client last read.
- `changes` (object, required, at least one key, only the JSON keys above).

| Status | When | Body |
| --- | --- | --- |
| 200 | saved (or nothing changed) | same shape as GET, with the new `version` |
| 409 | `version` is not current | `{"message": "Settings were changed elsewhere. Reload them and apply your change again.", "current_version": 3}` |
| 422 | bad field | `{"message": "...", "errors": {"changes.max_loss_per_trade_pct": ["The max_loss_per_trade_pct must be greater than 0 and at most 100."]}}` |

Error keys are `changes.<field>`, plus `env`, `version` and `changes` (unknown key / empty).

### GET /api/settings/history?env=demo&per_page=20&page=1

Newest first; `per_page` 1–100, default 20. Version 1 (the creation) is in the history too.

```json
{
  "data": [
    {
      "id": "1c7e…",
      "version": 2,
      "settings": { "risk_profile": "custom", "max_loss_per_trade_pct": "1.0", "max_leverage": 3, "...": "all fields" },
      "diff": {
        "risk_profile": { "from": "balanced", "to": "custom" },
        "max_leverage": { "from": 5, "to": 3 }
      },
      "changed_by": { "id": "0b9d…", "name": "Owner" },
      "created_at": "2026-10-28T21:05:00.000000Z"
    },
    { "id": "…", "version": 1, "settings": { "...": "defaults" }, "diff": { "...": "from null to every default" }, "changed_by": { "...": "" }, "created_at": "…" }
  ],
  "links": { "first": "…", "last": "…", "prev": null, "next": null },
  "meta": { "current_page": 1, "last_page": 1, "per_page": 20, "total": 2, "...": "Laravel paginator meta" }
}
```

`changed_by` is `null` for writes without a user.

## The event — system.settings_changed

XADD to Redis stream **`atlas.system`** (Redis connection `events`, no key prefix, `REDIS_DB` database).
Each entry has **one field, `envelope`**, whose value is the JSON envelope in the
`atlas_shared.events.EventEnvelope` shape:

```json
{
  "id": "5d1f7c8e-0c55-4f0e-8a2b-6e0b1d9a7c11",
  "type": "system.settings_changed",
  "ts": "2026-10-28T21:05:00.123Z",
  "env": "demo",
  "source": "backend:settings",
  "correlation_id": null,
  "payload": {
    "settings_id": "9f6c0a7e-3a1b-4d0e-9a51-2b7f1c2d3e4f",
    "version": 2,
    "changed_fields": ["risk_profile", "max_leverage"],
    "changed_by": "0b9d…"
  }
}
```

Python: `EventEnvelope.model_validate_json(fields[b"envelope"])`, then reload settings for
`envelope.env` from Postgres. The event carries no values on purpose: Postgres is the source of truth,
and every risk decision should still compare `settings.version`.

Published after the DB commit; if Redis is down the change stays saved and the error is reported
(`report()`), not returned. Creating version 1 on first read publishes nothing.

To publish other events from PHP: `app(App\Support\Events\EventBus::class)->publish($stream, $type, $env, $source, $payload, $correlationId)`.

## Audit log — App\Support\Auditable

```php
app(\App\Support\Auditable::class)->record(
    action: 'connection.created',     // dotted verb
    subjectType: 'connections',       // table-style name
    subjectId: $connection->id,
    before: null,
    after: ['label' => 'Demo', 'key' => '…a1b2'],  // never secrets: mask them
);
```

User defaults to the request's authenticated user; IP to the request IP. Call it inside the same DB
transaction as your write. Settings write `settings.created` (first read) and `settings.updated`, with
`before`/`after` = `{env, version, data}`.

## Tables

- `settings`: `id` uuid, `env` unique enum, `version` int, `data` jsonb, hot columns (decimals are
  `numeric(12,6)`, so `max_loss_per_trade_pct` reads back from SQL as `"1.000000"` — use `data` for the
  canonical string), `created_at`/`updated_at`.
- `settings_history`: `id`, `settings_id` → settings, `version` (unique per settings_id), `data`, `diff`
  (`{field: {from, to}}`), `changed_by` → users nullable, `created_at`.
- `audit_log`: `id`, `user_id` → users nullable, `action`, `subject_type`, `subject_id`, `before`, `after`
  (jsonb), `ip`, `created_at`.

## Tests

`backend/tests/Feature/Settings/SettingsApiTest.php` needs Redis on `127.0.0.1:6380`; phpunit.xml points
tests at Redis database 15 so they never write into the dev streams in database 0.
