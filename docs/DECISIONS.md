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
- **Money-limit defaults start small** (ATL-124). The brief (§14.4) names the money limits but gives
  no numbers, so a fresh env starts with `bot_capital_cap_usdt` 10,000, `max_usdt_per_trade` 1,000,
  `max_new_exposure_per_day_usdt` 5,000 and `max_trades_per_day` 10. The Risk Engine uses the smaller
  of these and the risk-profile percentages, so the first orders are small until the owner raises
  them in Settings. `bot_capital_cap_usdt` is the one nullable settings field: `null` means no cap
  (decision #250: sizing equity is then the account equity). Asset-class toggles are stored as `enabled_markets`, the list of
  `markets.kinds` that are on (dated futures and options start off); `whitelist` and `blocklist` are
  upper-case instId lists, the blocklist always wins.
- **Executor tables enforce rules 1 and 8 at the database** (ATL-141). `orders` refuses an
  `origin='atlas'` row with no `cl_ord_id`, and an atlas `entry` with no `proposal_id` and
  `risk_check_id`; protect/close/kill orders and reconciled `external` orders may leave them null.
  Trade-call retries resend the identical body, so a lost answer comes back as sCode 51016
  (duplicated clOrdId) and the Executor looks the order up rather than placing a second one. GET and
  POST `trade/order` share one rate-limit bucket. Executor defaults (`executor` in
  config/defaults.yaml): entry timeout 30 min, perps tdMode isolated, stop check 3 s, 3 tries,
  emergency stop 2.0 × ATR(1H).
- **Risk Engine check rules the brief leaves open** (ATL-127, `trading_core/risk/engine.py`, numbers in
  `risk_rules` of config/defaults.yaml). Check 1 reads freshness from the `tickers` channel stamp in
  Redis, not any-channel, so candle pushes cannot make a quiet ticker look fresh. Check 2's US hours
  are 09:30–16:00 America/New_York Mon–Fri, DST from the tz database; exchange holidays are not
  modelled (a holiday counts as open). Check 5 counts same-direction exposure in instruments with
  30-day corr > 0.8 (the instrument itself included; a pair with < 20 overlapping daily returns is
  "unknown" and not counted), and caps each asset class's **net** exposure at the same
  `max_correlated_exposure_pct`, since Settings has no separate per-class cap; it also enforces
  `max_trades_per_day`. Portfolio, loss, liquidation and blackout failures are hard rejects; only
  check 4's caps resize. Check 6's loss is start equity (first equity_curve point of the Baghdad day
  or ISO week) + net deposits since − current equity, so realised and unrealised both count;
  consecutive losses are trade bills (type 2) with a negative pnl, newest first. Check 7 estimates an
  isolated liquidation price as entry × (1 ∓ 1/leverage ± 0.005 maintenance). Check 9: autonomy 1 →
  `advise_only` (a new `risk_checks.decision` value), 2 → `awaiting_owner`, 3–4 → `awaiting_owner`
  until `autonomy.max_enabled_level` allows them (Phase 3), so nothing is sent without the owner.
  Fee rates are not stored yet, so sizing uses `risk_rules.fallback_taker_fee_rate` and the check
  records `fee_source: config_fallback`. A limit proposal with only an entry zone is sized at the
  zone bound farthest from the stop.
