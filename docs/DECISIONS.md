# Decisions

The authoritative log is kept by the team's tooling and mirrored into `CLAUDE.md`. This file records
decisions where the brief was silent, as the brief asks.

- **Phases ship as patches; calendar gates are owner-run.** Gates measured in days or weeks of demo
  running are listed in the handover for the owner; each patch is accepted on its tests and a check
  against the OKX demo API.
- **Laravel 13** is the latest stable release at build time (brief: "12.x+").
- **Monorepo layout:** `backend/`, `frontend/`, `services/` (one uv workspace). Laravel migrations own
  the whole schema, including Timescale hypertables (raw SQL inside migrations), so there is no
  separate `db/` migration tool.
- **Local ports:** Postgres (Timescale + pgvector) on `127.0.0.1:5433`, Redis on `127.0.0.1:6380`, so
  Atlas never collides with other Postgres/Redis instances on the Mac. API `:8000`, SPA `:3000`,
  Reverb `:8080`, trading core `:8100`.
- **Tests run on real Postgres**, never SQLite: jsonb, arrays and hypertables behave differently there.
- **Each checkout has its own test database** (ATL-29): `atlas_test_<first 8 hex of sha1(realpath of
  the repository root)>`, so parallel runs in different worktrees never reset each other's schema.
  `backend/tests/bootstrap.php` (rule in `Tests\Support\TestDatabase`) points Pest at it and creates
  it if missing; migrations then add the extensions. Python tests use `atlas_shared.testing.db_settings()`,
  which applies the same rule, so `php artisan test` migrates the database pytest reads. Exporting
  `DB_TEST_DATABASE` forces one name on both sides (e.g. `atlas_test`).
- **Redis client is predis** (pure PHP), so no PHP extension is needed on the Mac.
- **Redis Streams entry format:** every entry on an `atlas.*` stream has one field, `envelope`, holding the
  brief §3.2 envelope as JSON (the `atlas_shared.events.EventEnvelope` shape). PHP publishes with
  `App\Support\Events\EventBus` on the `events` Redis connection, which has no key prefix.
- **Settings values travel as strings for decimals and integers for counts/leverage/autonomy;** a settings
  change event carries the new version, not the values, and consumers reload from Postgres.
- **Withdraw-enabled OKX keys: refused in live, accepted with a warning in demo** (ATL-30, CEO
  instruction of 2026-10-28, relaxing decision #237 for `env=demo` only). Test connection on a demo key
  whose `perm` includes `withdraw` returns `ok: true`, sets `last_verified_at` and carries
  `warnings: ["withdraw_permission_enabled"]`; sync runs for it like any verified key. A live key with
  `withdraw` is still refused with `error: "withdraw_permission_enabled"` and is never verified.
  `atlas:doctor` reports the demo case as a warning (exit 0) and fails on a live connection whose last
  test stored `withdraw`. Brief rule 5 is unchanged: no withdrawal or transfer code exists anywhere,
  and `NoWithdrawalCodeTest` guards it.
