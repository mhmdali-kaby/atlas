# ATL-66 — Data sources API (Settings → Data sources)

Laravel, `backend/routes/api/data_sources.php` → `App\Http\Controllers\DataSources\DataSourcesController`.
All routes need a Sanctum SPA session (`auth:sanctum`; 401 otherwise). Writes are throttled per user by
`throttle:data-sources-write` (`data_sources_api.writes_per_minute`, default 20 → 429). Unknown
source key → 404. Status logic lives in `App\Support\DataSources\SourceStatus`.

Bounds come from `config/defaults.yaml` → `data_sources_api`:
`poll_seconds_min: 60`, `poll_seconds_max: 86400`, `api_key_max_length: 255`, `writes_per_minute: 20`.

## Status (derived, not stored)

Precedence, first match wins:

| status | when |
|---|---|
| `disabled` | `enabled = false` |
| `needs_key` | `requires_key` and no key stored |
| `degraded` | `disabled_until` is in the future (ingest's 3-strike, 30-min disable) |
| `never_run` | no `last_success_at`, no `last_error`, `consecutive_failures = 0` |
| `ok` | anything else (including a source with 1–2 failures not yet disabled — see `consecutive_failures`) |

A source is **down** when its status is `disabled`, `needs_key` or `degraded`. `summary.<category>.all_down`
is true when no source in that category is up (also true for a category with 0 sources). Patch E's
"all sources for a class are down" rule reads `summary.crypto_news`, `summary.markets_news`,
`summary.calendar` (all six categories are always present).

## GET /api/data-sources → 200

Ordered by category, then name.

```json
{
  "data": [
    {
      "key": "cryptopanic",
      "name": "CryptoPanic",
      "kind": "api",
      "category": "crypto_news",
      "url": "https://cryptopanic.com/api/v1/posts/",
      "enabled": true,
      "requires_key": true,
      "has_key": true,
      "key_last4": "WXYZ",
      "poll_seconds": 180,
      "status": "degraded",
      "consecutive_failures": 3,
      "disabled_until": "2026-10-29T10:30:00.000000Z",
      "last_success_at": "2026-10-29T09:41:12.123456Z",
      "last_item_at": "2026-10-29T09:40:00.000000Z",
      "last_error": "HTTP 401",
      "updated_at": "2026-10-29T10:00:00.000000Z"
    },
    {
      "key": "rss.coindesk",
      "name": "CoinDesk",
      "kind": "rss",
      "category": "crypto_news",
      "url": "https://www.coindesk.com/arc/outboundfeeds/rss",
      "enabled": true,
      "requires_key": false,
      "has_key": false,
      "key_last4": null,
      "poll_seconds": 180,
      "status": "never_run",
      "consecutive_failures": 0,
      "disabled_until": null,
      "last_success_at": null,
      "last_item_at": null,
      "last_error": null,
      "updated_at": "2026-10-29T08:00:00.000000Z"
    }
  ],
  "summary": {
    "exchange":     { "total": 1, "available": 1, "all_down": false },
    "crypto_news":  { "total": 5, "available": 4, "all_down": false },
    "markets_news": { "total": 4, "available": 3, "all_down": false },
    "calendar":     { "total": 2, "available": 1, "all_down": false },
    "earnings":     { "total": 1, "available": 0, "all_down": true },
    "sentiment":    { "total": 1, "available": 1, "all_down": false }
  }
}
```

`last_error` is shown as stored; atlas_ingest writes it already redacted (ATL-59). Timestamps are UTC ISO 8601 with
microseconds, or null.

## PATCH /api/data-sources/{key} → 200

Body: `{"enabled"?: bool, "poll_seconds"?: int}` — at least one. `poll_seconds` must be within
`[poll_seconds_min, poll_seconds_max]` (422 otherwise). `api_key`, `key`, `requires_key` in the body → 422
(the key has its own endpoint). Response: `{"data": <one source object as above>}`.

```http
PATCH /api/data-sources/rss.coindesk
{"enabled": false, "poll_seconds": 600}
```
```json
{ "data": { "key": "rss.coindesk", "enabled": false, "poll_seconds": 600, "status": "disabled", "...": "..." } }
```

atlas_ingest re-reads rows every `news.reload_seconds` (60), so a change takes effect within a minute.
A patch that changes nothing returns 200 and writes no audit row.

## PUT /api/data-sources/{key}/key → 200

Body: `{"api_key": "…"}`. Trimmed (global TrimStrings), 4..`api_key_max_length` chars, no whitespace inside.
Only for `requires_key = true` sources; otherwise 422 with `errors.api_key` ("This source does not use an
API key."). Replaces any existing key and resets `consecutive_failures = 0`, `disabled_until = null`,
`last_error = null`. Response `{"data": <source>}` with `has_key: true`, `key_last4`, never the key.

## DELETE /api/data-sources/{key}/key → 204

Removes the key (status becomes `needs_key`). 422 for a source that does not use a key; 204 with no audit
row if there was no key. Failure columns are left as they were.

## Audit (`audit_log`, subject_type `news_sources`, subject_id = row UUID)

| action | before | after |
|---|---|---|
| `news_source.updated` | `{"source":"rss.coindesk","enabled":true,"poll_seconds":180}` | `{"source":"rss.coindesk","enabled":false,"poll_seconds":600}` |
| `news_source.key_set` | `{"source":"cryptopanic","api_key":null}` (or `"key_set"` when replacing) | `{"source":"cryptopanic","api_key":"key_set"}` |
| `news_source.key_removed` | `{"source":"cryptopanic","api_key":"key_set"}` | `{"source":"cryptopanic","api_key":"key_removed"}` |

No key value or last 4 ever goes into audit.

## Two-minute version

The frontend never needs to compute status — use `status` and `summary`. To check "is this key working",
wait for the next poll: after PUT the failure columns are cleared, so watch `last_error` and `last_success_at` after ingest's next run (a source that never succeeded shows `never_run` until then)
There is no "test key" endpoint; that was not in scope.
