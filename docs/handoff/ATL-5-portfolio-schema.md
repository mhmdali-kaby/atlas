# ATL-5 — Portfolio and instrument schema

Migrations: `backend/database/migrations/2026_10_28_2000*`–`2002*`. Python mirror:
`services/shared/src/atlas_shared/db/models.py` (import from `atlas_shared.db`).

## Conventions on every table

- `id uuid` primary key (generate it yourself: Python models default to `uuid.uuid4`; with
  `DB::table()->insert` pass `Str::uuid()`).
- `env` varchar CHECK in (`demo`, `live`).
- `created_at`, `updated_at` timestamptz(6), nullable. **SQLAlchemy does not fill them**; set
  both on insert and `updated_at` on upsert (`func.now()`).
- Every price/size/amount is `numeric(38,18)` → `Decimal` in Python, string on the wire. Never float.
- Every timestamp is `timestamptz(6)` in UTC. Convert OKX ms epochs (`cTime`, `ts`, `listTime`)
  to aware UTC datetimes; OKX `""` → `NULL`.
- Units: columns ending `_usd` are USD. Every other amount is in the row's `ccy` (bills,
  balances, deposits) or as OKX reports it (positions: `pos` in contracts for SWAP/FUTURES,
  `upl`/`margin` in the settle ccy, `upl_ratio` a fraction, e.g. `0.05` = 5%).

## Tables and upsert keys

| Table | Columns (besides id/env/timestamps) | Unique / upsert key |
| --- | --- | --- |
| `instruments` | inst_id, inst_type, inst_family?, inst_category? (raw OKX string, e.g. "1"), base_ccy?, quote_ccy?, settle_ccy?, ct_val?, ct_val_ccy?, ct_mult?, lot_sz, min_sz, tick_sz, max_lever?, state, list_time?, exp_time?, asset_class (crypto\|stock\|etf\|index\|commodity\|other, default other), last_seen_at? | (env, inst_id) |
| `balances_snapshots` | ccy, account (trading\|funding), eq, avail_bal?, frozen_bal?, eq_usd?, upl?, taken_at | (env, account, ccy, taken_at) |
| `positions_snapshots` | inst_id, pos_side (net\|long\|short), pos, avg_px?, mark_px?, liq_px?, lever?, upl?, upl_ratio?, margin?, mgn_mode?, c_time?, taken_at | (env, inst_id, pos_side, taken_at) |
| `bills` | bill_id, inst_id?, ccy, type, sub_type?, bal_chg, pnl?, fee?, ts | (env, bill_id) |
| `deposits_withdrawals` | kind (deposit\|withdrawal), external_id (OKX depId / wdId), ccy, amount, state, tx_id?, ts | (env, kind, external_id) |
| `sync_status` | stream, last_success_at?, last_error? (text), reconciled_at? | (env, stream) |
| `equity_curve` | ts, total_eq_usd, upl_usd? | (env, ts); PK is (id, ts) |

`?` = nullable.

## Things worth knowing

- `equity_curve` is a TimescaleDB hypertable on `ts`. Timescale requires every unique index to
  include `ts`, hence PK (id, ts). Upsert on (env, ts) — round `ts` to the minute first.
- Snapshot tables are append-only per sync tick: write all rows of one tick with the same
  `taken_at`; "current" = rows at `max(taken_at)` for the env. A position that disappears from
  OKX simply isn't in the newest tick.
- `sync_status.stream` names are free strings; suggested: `instruments`, `balances`,
  `positions`, `bills`, `cashflows`. `reconciled_at` feeds the "Reconciled with OKX ✓" badge.
- `inst_category` is stored raw; derive `asset_class` in code, unknown → `other` (brief §2).
- Engine: `make_engine()` reads `DB_HOST`, `DB_PORT` (5433), `DB_DATABASE`, `DB_USERNAME`,
  `DB_PASSWORD` and sets the session timezone to UTC.
- Schema drift: `services/shared/tests/test_schema_reflection.py` reflects atlas_test and fails
  on any column/type/nullability/PK/unique mismatch. Change a migration → change the model in the
  same commit, and run `php artisan test` (migrates atlas_test) before `uv run pytest shared`.
- Not built: `orders`, `fills`, `algo_orders` (Phase 2), any FK between tables.
