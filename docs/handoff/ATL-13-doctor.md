# ATL-13 — `php artisan atlas:doctor`

## The two-minute version

- `./atlas doctor` (DevOps' wrapper) runs its own Docker/port checks, then calls
  `cd backend && php artisan atlas:doctor` for the app-level checks. Exit code 0 = every required
  check passed; 1 = at least one failed. Warnings (⚠) never fail.
- `--json` prints a JSON array, one object per check, in a fixed order:
  `{"name": "...", "ok": true|false, "status": "pass"|"warn"|"fail", "detail": "..."}`.
- Laravel needs `INTERNAL_SHARED_SECRET` and `TRADING_CORE_URL` (same as Test connection);
  trading_core needs the same `APP_KEY` as Laravel.
- Running it **re-runs the demo connection test** (trading_core writes the verification columns, an
  `audit_log` row `connection.tested` with `after.source = "atlas:doctor"` and no user is recorded).
- When Laravel runs inside an AI agent shell (`AI_AGENT` / `CLAUDECODE` set), Laravel's console compacts
  whitespace and drops the ⚠ glyph. Unset those to see the real output; the owner's terminal is unaffected.

## Checks (name → passes when)

| name | passes when |
| --- | --- |
| `postgres` | `select version()` answers |
| `postgres_extensions` | `timescaledb` and `vector` are in `pg_extension` |
| `migrations` | no migration file is unrun |
| `redis` | `PING` answers |
| `trading_core` | unsigned `GET /health` answers `{"status":"ok"}` (fails with "unreachable at <url>") |
| `okx_clock` | signed `GET /internal/time` answers and `drift_ms < doctor.max_clock_drift_ms` (2000) |
| `demo_connection` | an `okx`/`demo` connection exists and the re-run test (`POST /internal/connections/{id}/test`) returns `ok: true` |
| `withdraw_permission` | OKX just reported the key's permissions and `withdraw` is not among them. Fails if withdraw is on (from the fresh test, or from the stored row when trading_core is down), and fails when it could not be confirmed |
| `disk_space` | free space on the backend volume ≥ `doctor.min_free_disk_gb` (5) |
| `backup` | always `warn` "not configured" until the Backup feature exists — replace `Doctor::backup()` then |

Thresholds live in `config/defaults.yaml` → `doctor.*`.

## trading_core: `GET /internal/time` (signed)

200:
```json
{"ok": true, "env": "demo", "server_time": "2026-10-28T08:00:00.243Z",
 "local_time": "2026-10-28T08:00:00.000Z", "offset_ms": 243, "drift_ms": 243}
```
`offset_ms` = OKX server time minus this Mac's time (round-trip midpoint); `drift_ms` = its absolute value.
502: `{"ok": false, "error": "okx_unreachable"}` or `{"ok": false, "error": "okx_error", "okx_code": "..."}`.
Tests can replace the client with `app.state.okx_public_client_factory = lambda env: OkxClient(...)`.

## Never in the output

Key, secret, passphrase, full UID (only `UID …1234`), DB credentials (exceptions are reported by class name
only), the internal shared secret.
