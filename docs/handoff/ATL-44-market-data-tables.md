# ATL-44 — Market-data hypertables and backfill tables

Migration: `backend/database/migrations/2026_10_29_200000_create_market_data_tables.php`.
Models: `atlas_shared.db` (`services/shared/src/atlas_shared/db/models.py`), all exported from `atlas_shared.db`.

## The two-minute version

- The five hypertables have **no `id`, no `created_at`/`updated_at`**. Their primary key *is* the natural
  key and the upsert conflict target. Write them with
  `insert(Model).values(rows).on_conflict_do_update(index_elements=list(inspect(Model).primary_key), set_={...excluded...})`
  — see `services/shared/tests/test_market_data_models.py::_upsert_candle`.
- Deduplicate a batch on the key before sending it: Postgres refuses a batch that hits the same row twice
  (`ON CONFLICT DO UPDATE command cannot affect row a second time`). Keep batches ≤ ~2000 rows (65535 bound-parameter limit).
- Every amount is `numeric(38,18)` → `Decimal`. Never pass floats; pass OKX's strings through `Decimal(str)`.
- `ts` is `timestamptz(6)`, UTC. For candles it is the bar's **open** time (OKX `ts`).
- Enum columns are `varchar(255)` + CHECK (Laravel `enum`). An invalid value raises `IntegrityError`/check violation.

## Hypertables (all partitioned on `ts`)

| Table | Model | Primary key (= upsert key) | Chunk | Columns (nullable marked ?) |
| --- | --- | --- | --- | --- |
| `candles` | `Candle` | env, inst_id, bar, ts | 1 day | env, inst_id varchar(64), bar (`1m`/`5m`/`15m`/`1H`/`4H`/`1D`), ts, o, h, l, c, vol, vol_ccy?, vol_ccy_quote?, confirmed bool default false |
| `funding_rates` | `FundingRate` | env, inst_id, ts | 30 days | env, inst_id, ts (funding time), funding_rate, realized_rate? |
| `open_interest` | `OpenInterest` | env, inst_id, ts | 7 days | env, inst_id, ts, oi?, oi_ccy?, oi_usd? |
| `long_short_ratio` | `LongShortRatio` | env, ccy, period, ts | 30 days | env, ccy varchar(32), period (`5m`/`1H`/`1D`), ts, ratio |
| `taker_volume` | `TakerVolume` | env, ccy, period, ts | 30 days | env, ccy, period (`5m`/`1H`/`1D`), ts, buy_vol, sell_vol |

`env` is `demo`|`live` everywhere. OKX candle field mapping: `vol` = contracts (SWAP) or base (SPOT),
`vol_ccy` = base ccy, `vol_ccy_quote` = quote ccy, `confirmed` = OKX `confirm == "1"`.

`open_interest.inst_id` holds an OKX instId (`public/open-interest`, e.g. `BTC-USDT-SWAP`) **or a bare ccy**
(`BTC`) for the currency-level rubik `open-interest-volume` series. They cannot collide: instIds always contain `-`.

## Plain tables (UUID `id`, `env`, `created_at`, `updated_at` like every other trading table)

**`backfill_jobs`** — model `BackfillJob`. Unique (env, inst_id, dataset); index (env, status).

| Column | Type |
| --- | --- |
| inst_id | varchar(64) |
| dataset | CHECK: `candles:1m`, `candles:5m`, `candles:15m`, `candles:1H`, `candles:4H`, `candles:1D`, `funding`, `open_interest`, `long_short_ratio`, `taker_volume` |
| target_from | timestamptz — oldest ts wanted; done when `cursor_ts <= target_from` |
| cursor_ts? | timestamptz — oldest ts written so far (backfill walks backwards from now) |
| rows_written | bigint default 0 |
| status | CHECK `pending` (default) / `running` / `done` / `failed` / `paused` |
| last_error? | text |

For `long_short_ratio`/`taker_volume` jobs, `inst_id` holds the ccy (same convention as `open_interest`).
For a rubik dataset the period is not part of the job key; if you need one job per period, ask TL — adding
`candles:`-style suffixes (`taker_volume:1H`) needs a migration to widen the CHECK.

**`data_quality_issues`** — model `DataQualityIssue`. Unique (env, inst_id, dataset, kind, from_ts), so a re-run
check can upsert instead of duplicating; index (env, inst_id, dataset, repaired_at) for "open issues".

| Column | Type |
| --- | --- |
| inst_id | varchar(64) |
| dataset | same CHECK list as backfill_jobs |
| kind | CHECK `gap` / `duplicate` / `zero_volume` / `outlier` |
| from_ts, to_ts | timestamptz |
| detail? | jsonb (free-form, e.g. `{"missing_bars": 5}` or `{"sigma": 9.2, "c": "…"}`) |
| detected_at | timestamptz (not null — set it yourself) |
| repaired_at? | timestamptz, null until repaired from REST |

## Not built here

No retention/compression policies, no continuous aggregates (4H/1D are stored bars from OKX, not rolled up),
no `instrument_stats_daily`. Tests: `backend/tests/Feature/Schema/MarketDataSchemaTest.php` (Pest) and
`services/shared/tests/test_market_data_models.py`; `test_schema_reflection.py` covers model ↔ DB drift.
