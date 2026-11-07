# ATL-172 — Portfolio history: closed positions + fills sync, list API and CSV export

For ATL-173 (History page), ATL-174 (performance API), ATL-176 (holdings/benchmark API), and anyone
who writes fills or links positions (ATL-142, ATL-146).

## Two-minute version

- New table **`positions_closed`** (one row per OKX `account/positions-history` record) and a new
  nullable column **`fills.fill_pnl`** (OKX `fillPnl`). Migration
  `2026_10_30_500000_create_positions_closed_table.php`; Python model `atlas_shared.db.PositionClosed`.
- trading_core job **`history`** (`trading_core.sync.history`) runs every `sync.history_interval_seconds`
  (300 s) with the same verified-connection guard as bills. It pages positions-history and
  fills-history (instTypes `sync.history_fill_inst_types` = SPOT, SWAP, FUTURES). First run reads all
  3 months; later runs stop paging at last success − `sync.history_overlap_seconds` (3600). Upserts
  are idempotent. `sync_status(env, 'history').reconciled_at` is stamped on success.
- Linking: a closed position gets `proposal_id` + `agent` (= `proposals.author`) + `origin='atlas'`
  when an Atlas **entry** order (origin atlas, purpose entry, same inst and side) has its first fill
  within [cTime − 5 s, uTime]; the closest one to cTime wins. Fills are linked by `ordId` →
  `orders.ord_id`. Links are only ever added, never removed (COALESCE on re-sync).
- **R is not stored.** The API derives it at read time: `R = (exit − entry) × dir / |entry − proposal.stop|`,
  2 decimals HALF_UP (`portfolio_history.r_scale`), with `entry` = the position's open average price
  (proposal entry if OKX gave none). No proposal stop / no exit / zero distance → `null`. For the
  Performance API (ATL-174) reuse `App\Support\Portfolio\HistoryQuery::rMultiple()`.
- `setup_tag` exists but nothing fills it yet: proposals have no setup-type column. When a proposal
  carries one, set it in `trading_core.sync.history.position_row` (one line) — the filter already works.

## positions_closed

`id uuid`, `env`, `pos_id`, `inst_id`, `inst_type`, `mgn_mode`, `pos_side` (net|long|short),
`side` (**long|short**, the closed position's direction), `close_type` (OKX type: 1 partial close,
2 close all, 3 liquidation, 4 partial liquidation, 5 ADL), `lever`, `ccy`, `size` (contracts closed,
closeTotalPos), `entry_px`, `exit_px`, `realised_pnl` (OKX realizedPnl = pnl + fee + funding +
liq penalty), `pnl`, `fee`, `funding_fee`, `liq_penalty` (OKX sign: negative = paid), `pnl_ratio`,
`opened_at`, `closed_at`, `proposal_id` → proposals (SET NULL), `agent`, `setup_tag`,
`origin` (atlas|external, default external; CHECK atlas ⇒ proposal_id), `raw` jsonb, timestamps.
UNIQUE (env, pos_id, closed_at). Money numeric(38,18).

## GET /api/portfolio/history (auth:sanctum)

Query (all optional):

| param | values |
| --- | --- |
| `type` | `fills` (default) \| `closed` |
| `env` | demo \| live (default env) |
| `inst_id` | exact instId |
| `asset_class` | crypto, stock, etf, index, commodity, other (joined from `instruments`) |
| `agent` | closed: `positions_closed.agent`; fills: the linked order's proposal author |
| `strategy_tag` | **closed only** (422 on fills) |
| `from`, `to` | `Y-m-d` **Asia/Baghdad** days, inclusive; `to ≥ from` |
| `side` | fills: buy\|sell (long/short → 422). closed: long\|short, or buy\|sell = opening side (buy≡long, sell≡short) |
| `win_loss` | win (pnl > 0) \| loss (pnl < 0); closed uses `realised_pnl`, fills use `fill_pnl`. Zero is neither. |
| `limit` | 1..200, default 50 |
| `cursor` | `meta.next_cursor` / `meta.prev_cursor` from the previous answer; malformed → 422 |

Order: newest first by (`closed_at`|`ts`, `id`). The cursor keeps microseconds, so rows sharing a
timestamp are neither skipped nor repeated, and rows added later do not shift later pages.

Closed example (`?type=closed&side=sell&win_loss=loss&from=2026-10-20&to=2026-10-21&limit=2`):

```json
{
  "data": [
    { "id": "9f3c…", "pos_id": "1777777777777777777", "inst_id": "ETH-USDT-SWAP", "asset_class": "crypto",
      "side": "short", "pos_side": "net", "margin_mode": "isolated", "close_type": "2", "leverage": "5",
      "ccy": "USDT", "size": "2", "entry": "3301.5", "exit": "3350.12", "realised_pnl": "-4.5",
      "pnl": "-4.5", "fees": "-0.67", "funding": "-0.012", "liq_penalty": "0", "r": "-1.50",
      "outcome": "loss", "opened_at": "2026-10-21T18:30:00.000000Z", "closed_at": "2026-10-21T20:30:00.000000Z",
      "proposal_id": "0b1e…", "agent": "strategist", "setup_tag": null, "origin": "atlas" }
  ],
  "meta": { "type": "closed", "per_page": 2, "next_cursor": "eyJjbG9zZWRfYXQiOi…", "prev_cursor": null }
}
```

`outcome` = win | loss | flat | null (from realised_pnl). Decimals are plain strings with trailing
zeros stripped (`"-0.00000001"`, never `1e-8`), except `r`, which is always 2 decimals.

Fills row:

```json
{ "id": "…", "ts": "2026-10-20T08:00:00.000000Z", "inst_id": "ETH-USDT-SWAP", "asset_class": "crypto",
  "side": "sell", "pos_side": "net", "price": "3350.12", "size": "2", "fee": "-0.67", "fee_ccy": "USDT",
  "fill_pnl": "-3.2", "exec_type": "T", "trade_id": "1900000002", "ord_id": "2900000000000000002",
  "order_id": null, "proposal_id": null, "agent": null, "origin": "external" }
```

## GET /api/exports/{fills|closed}.csv (auth:sanctum)

Same filters as above (no `type`, `cursor`, `limit`). Streams `text/csv` as an attachment named
`atlas-<env>-<type>-<UTC stamp>.csv`. Header row = the JSON keys in the order above
(`HistoryQuery::FILL_COLUMNS` / `CLOSED_COLUMNS`), one row per filtered record, nulls as empty cells,
timestamps ISO 8601 UTC with microseconds. Text cells starting with `= + - @` get a leading `'`
(numeric columns are left alone). Every export writes one `audit_log` row:
action `portfolio.history_exported`, subject_type `exports`, `after` = the filters (with
`from_utc`/`to_utc` already converted from Baghdad days).

## What is not here

No UI (ATL-173). No performance aggregates (ATL-174). No internal "sync history now" endpoint —
the job only runs on the scheduler. Lang file `lang/en/portfolio_history.php` holds the three
validation messages.
