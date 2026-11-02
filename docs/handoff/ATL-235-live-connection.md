# ATL-235 — live OKX connection (backend)

For whoever builds Settings → Connections' live section and the Settings → Environment pre-flight.

## What changed

- `POST /api/connections` now accepts `env: "live"`. One connection per `(exchange, env)`, so the owner can
  have one demo and one live OKX connection. A second live one is a 422 on `env`
  (`connections.already_exists`), exactly like demo. The `connections.live_disabled` message is gone.
- Same encrypted casts, same audit (`connection.created` / `updated` / `deleted` / `tested`), same masking.
- New column `connections.withdraw_off_verified_at` (timestamptz, nullable) and the same field on
  `ConnectionResource`.

## The resource (GET /api/connections, POST, PATCH, and `connection` inside the test response)

```json
{
  "id": "9f1c…", "exchange": "okx", "env": "live", "label": "OKX Live",
  "api_key_last4": "ab12", "has_secret": true, "has_passphrase": true,
  "permissions": ["read_only", "trade"], "account_mode": "futures", "position_mode": "net_mode",
  "uid_last4": "9876",
  "last_verified_at": "2026-11-02T07:10:00.000000Z",
  "withdraw_off_verified_at": "2026-11-02T07:10:00.000000Z",
  "last_test_error": null,
  "created_at": "…", "updated_at": "…"
}
```

Secret and passphrase never come back, not even their last 4 characters: `has_secret` / `has_passphrase`
only, as for demo since ATL-4.

## Test connection — `POST /api/connections/{id}/test` (unchanged shape)

trading_core calls OKX `account/config`. For a live row the request has **no** `x-simulated-trading`
header (OkxClient adds it in demo only).

| Key | `ok` | `error` | `warnings` | `last_verified_at` | `withdraw_off_verified_at` |
| --- | --- | --- | --- | --- | --- |
| live, Read + Trade | true | null | [] | now | now (same instant) |
| live, can withdraw | false | `withdraw_permission_enabled` | [] | null | null |
| demo, can withdraw | true | null | [`withdraw_permission_enabled`] | now | null |
| demo, Read + Trade | true | null | [] | now | now |
| any other failure | false | slug | [] | null | null |

`message` for `withdraw_permission_enabled` is "This key can withdraw funds. Create a key with Read + Trade
only." (lang `connections_test.php`). Changing any secret clears every verification field, including
`withdraw_off_verified_at`, so the pre-flight item resets until the owner tests again.

**Pre-flight "live connection verified, withdraw permission OFF verified"**: true when the live row has
`last_verified_at !== null && withdraw_off_verified_at !== null`. That pair is only ever set by a test where
OKX listed no `withdraw`.

## Sync

trading_core still syncs only `Settings.env` (the process env). A verified live key is **not** synced while
demo is active (test: `test_a_verified_live_key_is_not_synced_while_demo_is_active`). ATL-237 switches that
to `system_state.active_env`.

## Doctor

New check `live_connection` between `demo_connection` and `withdraw_permission`:

- no live row → ✓ "No live OKX connection (not needed while trading in demo)"
- live re-tested through trading_core (audited, `source: atlas:doctor`):
  verified → ✓ "Verified, no withdraw permission (reported by OKX just now): OKX Live · futures · net_mode · UID …9876";
  withdraw → ✗ (doctor exits 1); any other failure → ⚠ "Not verified …" (live is not active yet, so it
  does not block).
- trading_core down → judged from the stored row (withdraw ✗, verified ✓, else ⚠).

Demo withdraw stays a ⚠ on `withdraw_permission`; a stored live withdraw still fails that check too.

## Not built here

No UI. The frontend still shows `settings.connections.liveNote` ("Live trading is enabled in Phase 4") —
replace it in the Connections live-section task.
