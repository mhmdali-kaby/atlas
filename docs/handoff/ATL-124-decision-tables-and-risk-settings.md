# ATL-124 — proposals, votes, risk_checks, cycles + Risk Engine settings

For the Risk Engine (trading_core/risk), the settings pages (Money limits, Markets, Risk Engine),
the Orchestrator/Strategist/DA patch and the Portfolio Manager.

## Two-minute version

- Four Laravel-owned tables in `backend/database/migrations/2026_10_30_300000_create_trading_decision_tables.php`;
  Python models `Cycle`, `Proposal`, `Vote`, `RiskCheck` in `atlas_shared.db`. Every list of states is
  a Postgres CHECK, so a bad value fails at insert, not in your code.
- Money/prices/sizes are `numeric(38,18)` → `Decimal`. Probability/confidence `numeric(6,4)`, 0..1.
- 20 new settings keys, all in `GET /api/settings` → `data.settings`, all PATCH-able, defaults in
  `config/defaults.yaml` (`money_limits`, `markets`, `risk_engine`). Decimals are strings, counts are
  JSON integers, toggles are booleans, lists are JSON lists.
- `bot_capital_cap_usdt` can be JSON `null` (no cap, decision #250). In `settings.data` a stored
  null is a value: check key presence, don't treat null as "missing → default".
- Python should read a field from `settings.data` and fall back to `DEFAULTS` (rows saved before
  ATL-124 do not have the keys in `data`; the Laravel API fills them from defaults on read, the DB
  row does not). Record `settings.version` in `risk_checks.settings_version`.
- The proposal `status` list is exactly brief §8 — there is no `withdrawn` or `superseded`. A
  withdrawn proposal is `cancelled`; a revised one is `cancelled` too, and its revision is the row
  whose `parent_id` points at it. If you need a separate state, add it with a migration that
  widens the CHECK.

## Tables

All have `id uuid pk`, `env` (demo|live), `created_at`, `updated_at` (timestamptz(6)).

### cycles

| column | type | notes |
| --- | --- | --- |
| kind | varchar(32) NOT NULL | free text, e.g. `scheduled`, `manual`, `alert`; the Orchestrator task owns the list |
| state | enum, default `cycle_start` | `cycle_start, research_requests_sent, research_collected, proposals_open, reviews_done, voting, vote_closed, risk_check, awaiting_owner, executing, cycle_closed` |
| started_at | timestamptz NOT NULL | |
| deadlines | jsonb NOT NULL default `{}` | step → ISO-8601 UTC, e.g. `{"research": "2026-10-30T05:15:00Z", "vote": "…"}` |
| closed_at | timestamptz NULL | |

### proposals

| column | type | notes |
| --- | --- | --- |
| cycle_id | uuid NULL → cycles (SET NULL) | null for owner-requested proposals outside a cycle |
| version | int NOT NULL default 1 | CHECK ≥ 1 |
| parent_id | uuid NULL → proposals (RESTRICT), UNIQUE | CHECK `(version = 1) = (parent_id IS NULL)`; one revision per row |
| kind | enum default `entry` | `entry, amend_stop, amend_targets, partial_close, close` |
| author | varchar(64) | agent slug or `owner` |
| inst_id | varchar(64) | |
| side | enum NULL | `buy, sell` |
| order_type | enum NULL | `market, limit, post_only` |
| entry | numeric NULL | |
| entry_zone | jsonb NULL | `["4010.00", "4015.00"]` decimal strings |
| stop | numeric NULL | CHECK: `entry` needs side + order_type + stop; `amend_stop` needs stop |
| take_profits | jsonb default `[]` | `[{"price": "4060.00", "pct": "50"}]` |
| time_stop_minutes | int NULL | CHECK > 0 (brief §5 calls it `time_stop`; named after TradeProposal) |
| size_suggestion_pct | numeric NULL | % of equity, CHECK 0 < x ≤ 100 |
| leverage_suggestion | int NULL | CHECK ≥ 1 |
| expected_r | numeric NULL | |
| probability | numeric(6,4) NULL | CHECK 0..1 |
| rationale_md, invalidation | text NOT NULL default `''` | |
| evidence | jsonb default `[]` | `[{"type": "research_report", "id": "…"}]` |
| status | enum default `draft` | `draft, under_review, voting, passed, rejected, risk_checked, awaiting_owner, sent, partially_filled, filled, closed, cancelled, expired` |

Indexes: (env, inst_id, status), (env, created_at).

### votes

`proposal_id` uuid → proposals (CASCADE), `agent` varchar(64), `vote` enum `take|skip|change`,
`confidence` numeric(6,4) CHECK 0..1, `reason` text default `''`. UNIQUE (proposal_id, agent).

### risk_checks

| column | type | notes |
| --- | --- | --- |
| proposal_id | uuid → proposals (CASCADE) | many per proposal (re-check after Edit) |
| settings_version | int NOT NULL | CHECK ≥ 1 |
| result | enum | `approved, resized, rejected` |
| checks | jsonb default `[]` | suggested shape: `[{"check": "position_size", "passed": true, "detail": "…"}]` in §10 order |
| final_size | numeric NULL | contracts after lotSz rounding |
| final_notional_usd | numeric NULL | |
| leverage | int NULL | CHECK ≥ 1 |
| reason | text NULL | |
| decision | enum NULL | `send_now, awaiting_owner`; CHECK `(result = 'rejected') = (decision IS NULL)` |

## Python

```python
from atlas_shared.db import Cycle, Proposal, Vote, RiskCheck
p = Proposal(env="demo", author="strategist", inst_id="XAU-USDT-SWAP", side="buy",
             order_type="limit", entry=Decimal("4012.35"), stop=Decimal("3988.10"))
```

Server defaults (status, kind, version, jsonb lists, blank text) are filled by Postgres on insert;
`session.refresh()` or re-select to see them. Round-trip test: `services/shared/tests/test_trading_decision_models.py`.

## Settings keys (GET /api/settings → data.settings; PATCH /api/settings `{env, version, changes}`)

| key | type | default | allowed |
| --- | --- | --- | --- |
| bot_capital_cap_usdt | decimal string or **null** | "10000" | > 0, ≤ 1e12, or `null` = no cap (decision #250: sizing equity = min(equity, cap), or equity when null). The only nullable key. |
| max_usdt_per_trade | decimal string | "1000" | > 0, ≤ 1e12 |
| max_new_exposure_per_day_usdt | decimal string | "5000" | > 0, ≤ 1e12 |
| max_trades_per_day | int | 10 | 1..1000 |
| enabled_markets | list of strings | ["spot","crypto_perp","stock_perp","commodity_perp"] | subset of `markets.kinds` = spot, crypto_perp, stock_perp, commodity_perp, futures, options; no duplicates; `[]` allowed |
| whitelist | list of instIds | [] | ≤ 500, each `^[A-Z0-9]+(-[A-Z0-9]+)*$` after trim + upper-case |
| blocklist | list of instIds | [] | same |
| avoid_stock_perps_outside_us_hours | bool | true | strict boolean |
| min_stop_atr_mult | decimal string | "0.8" | > 0, ≤ 20 |
| max_stop_pct | decimal string | "8" | > 0, ≤ 100 |
| slippage_bps | decimal string | "2" | 0..1000 |
| liq_buffer_mult | decimal string | "2.0" | 1..20 |
| block_new_trades_minutes_before_high_impact_event | int | 15 | 0..1440 (0 = off) |
| volatility_halt_sigma | decimal string | "6" | > 0, ≤ 100 |
| market_data_max_age_seconds | int | 30 | 1..3600 |
| margin_buffer | decimal string | "0.9" | > 0, ≤ 1 |

Stored forms: decimals keep what the owner typed (`"2500.50"`); `enabled_markets` is stored in
`markets.kinds` order; instId lists are upper-cased and de-duplicated. Any invalid value or unknown
key → 422 with the error on `changes.<key>` (or `changes.<key>.<i>` for list entries, `changes`
for an unknown key) and no new version.

Example PATCH:

```json
{ "env": "demo", "version": 3,
  "changes": { "max_usdt_per_trade": "2500.50", "enabled_markets": ["crypto_perp", "options"],
               "blocklist": ["xau-usdt-swap"], "avoid_stock_perps_outside_us_hours": false } }
```

→ 200, `data.version` 4, `data.settings.blocklist` `["XAU-USDT-SWAP"]`, one settings_history row
with `diff` `{"max_usdt_per_trade": {"from": "1000", "to": "2500.50"}, …}`.

Existing readers already use `enabled_markets` and `blocklist` from settings data (Market Scout's
universe filter, `App\Support\Watchlists\Blocklist`), so the new keys feed them without changes.

## Not built here

The settings page UI, the "next trade can risk $Y" preview, the Risk Engine itself and
`POST /risk/check`. No Laravel Eloquent models for the four tables (add them when an API needs them).
