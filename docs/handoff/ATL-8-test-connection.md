# ATL-8 — Test connection (OKX account/config, withdraw keys refused)

## The two-minute version

- `POST /api/connections/{id}/test` (Sanctum session) → Laravel signs a call to trading_core
  `POST /internal/connections/{id}/test` → trading_core decrypts the row with `APP_KEY`, calls OKX
  `account/config`, **writes the verification columns itself**, and returns the result. Laravel adds
  `message` (from `lang/en/connections_test.php`), audits `connection.tested`, and returns the fresh
  connection resource too.
- `ok` is true only when account/config answered **and** permissions do not include `withdraw`
  (decision #237, demo too). A withdraw key returns `ok:false, error:"withdraw_permission_enabled"`
  but still fills account_mode/position_mode/permissions/uid_last4 so the UI can show what the key is.
- `last_verified_at` is set on success and **cleared to null on every failure** (a key that used to
  pass and now fails is not verified any more). `last_test_error` holds the error slug (not the
  message) or null.
- Spot mode (`acct_lv: "1"`) is `ok:true`. The raw `acct_lv` string is returned so the UI can show the
  Spot-mode warning. It is **not stored** on the row (no column); `account_mode = "spot"` on the row
  says the same thing.
- The full UID never leaves trading_core: only `uid_last4`.
- trading_core needs `APP_KEY` (same value as Laravel's) in its environment. Without it the internal
  route answers 503 `app_key_missing`, which Laravel reports as 502 `trading_core_error`.
- The real vault demo key currently comes back `withdraw_permission_enabled`, acctLv 2, net_mode
  (checked 2026-10-28). QA's ok:true check waits for the CEO's Read + Trade key.

## Laravel endpoint

`POST /api/connections/{id}/test`, no body. 401 unauthenticated, 404 unknown/non-UUID id.

200 (the test ran; look at `ok`):

```json
{
  "ok": true,
  "acct_lv": "2",
  "account_mode": "futures",
  "position_mode": "net_mode",
  "permissions": ["read_only", "trade"],
  "uid_last4": "6123",
  "error": null,
  "okx_code": null,
  "verified_at": "2026-10-28T08:00:00.123456Z",
  "message": null,
  "connection": { "id": "…", "exchange": "okx", "env": "demo", "label": "OKX Demo", "api_key_last4": "ab12",
                  "has_secret": true, "has_passphrase": true, "permissions": ["read_only", "trade"],
                  "account_mode": "futures", "position_mode": "net_mode", "uid_last4": "6123",
                  "last_verified_at": "2026-10-28T08:00:00.123456Z", "last_test_error": null,
                  "created_at": "…", "updated_at": "…" }
}
```

Failure example (still 200):

```json
{ "ok": false, "acct_lv": null, "account_mode": null, "position_mode": null, "permissions": [],
  "uid_last4": null, "error": "invalid_credentials", "okx_code": "50113", "verified_at": null,
  "message": "Key or passphrase is wrong", "connection": { … "last_verified_at": null,
  "last_test_error": "invalid_credentials" … } }
```

Non-200:

| Status | Body |
| --- | --- |
| 503 | `{"ok":false,"error":"trading_core_unavailable","message":"The trading service is not running. Start Atlas and try again."}` — connection refused/timeout (Laravel timeout 10 s) or `INTERNAL_SHARED_SECRET` empty |
| 502 | `{"ok":false,"error":"trading_core_error","message":"…"}` — trading_core answered non-2xx (bad signature, missing APP_KEY, crash) |
| 404 | `{"ok":false,"error":"connection_not_found",…}` when trading_core can't find the row; plain Laravel 404 for unknown ids |

## Error slugs (`error`) and messages

| slug | OKX codes | message (lang/en/connections_test.php) |
| --- | --- | --- |
| `withdraw_permission_enabled` | — | This key can withdraw funds. Create a key with Read + Trade only. |
| `invalid_credentials` | 50111, 50113, 50105 | Key or passphrase is wrong |
| `wrong_environment` | 50101 | This key belongs to the other environment (demo/live) |
| `ip_not_allowed` | 50110 | This key only works from the IP addresses on its allow-list |
| `clock_out_of_sync` | 50102 | This Mac's clock is out of sync with OKX |
| `key_frozen` | 50100 | This key is frozen on OKX |
| `key_not_found` | 50119 | OKX does not know this key |
| `decrypt_failed` | — | APP_KEY in trading_core differs from Laravel's |
| `okx_unreachable` | — | network failure after retries |
| `okx_error` | anything else (`okx_code` set) | OKX refused the request |

Frontend: translate the slug from `connection.last_test_error` with your own i18n when showing a
stored error; the `message` field is English only.

## account_mode values (acctLv)

`"1"` spot, `"2"` futures, `"3"` multi_currency_margin, `"4"` portfolio_margin, anything else `unknown`.
`permissions` is the OKX `perm` string split on commas, sorted.

## Audit

`action = connection.tested`, `subject_type = connections`, `after` = `{exchange, env, label,
api_key_last4, ok, acct_lv, account_mode, position_mode, permissions, uid_last4, error, okx_code}`.
No audit row when trading_core was unreachable/errored.

## Python (trading_core)

- `trading_core.connection_test.run_connection_test(connection_id, engine=, app_key=, client_factory=)`
  → `ConnectionTestResult` (`.as_dict()` is the internal route's body, without `message`). Raises
  `ConnectionNotFound`. Reuse it from `atlas:doctor` (ATL-13) via the signed route rather than
  duplicating the withdraw check.
- New `atlas_shared.db.Connection` SQLAlchemy model (mirrors the ATL-4 table; schema reflection test
  covers it). ATL-10: load connections with it; skip rows where `last_verified_at is None` (#237).
- `create_app(..., engine=)` now takes an optional engine (stored as `app.state.engine`) and
  `Settings.app_key` reads `APP_KEY`. `app.state.okx_client_factory` can override the OKX client in tests.
