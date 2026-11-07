# ATL-12 — Portfolio API (summary, equity, holdings, positions, cash flows)

## The two-minute version

- Five `GET` endpoints under `/api/portfolio/*`, all `auth:sanctum` (401 without the session, see
  ATL-2). They read **Postgres only**; trading_core keeps it in sync with OKX (decision #235).
- Every one takes `?env=demo|live` (optional, defaults to `demo`; anything else is 422).
- **Every amount is a decimal string** (`"5136121.59"`), with trailing zeros stripped and never
  `"-0"`. There are no floats anywhere. Percentages are in percent units with up to 4 decimals
  (`"2.7019"` means 2.7019 %). Format them on the client; never `parseFloat` them for maths.
- Timestamps are ISO-8601 UTC with microseconds (`2026-10-28T17:36:00.000000Z`). Display them in
  Asia/Baghdad.
- **Total PnL follows decision #238**, not "equity − net deposits". See the formula below, and
  label the tile "since `pnl_since`". `total_pnl_pct` can be `null`, so show "—".
- "Nothing synced yet" is a 200 with `null` figures and `stale: true`, not an error. The screens
  need an empty state for it.
- Code: `app/Support/Portfolio/PortfolioService.php` (all the maths),
  `app/Http/Controllers/Portfolio/PortfolioController.php`, `routes/api/portfolio.php`.

## Formulas (summary)

| Field | Definition |
| --- | --- |
| `total_equity_usd` | `total_eq_usd` of the env's latest `equity_curve` point |
| `net_deposits_usd` | all-time Σ successful deposits − Σ successful withdrawals (OKX `state = "2"`), USDT/USDC/USD at 1:1. Other currencies are excluded in Phase 0 (no FX yet) |
| `total_pnl_usd` | `equity − (baseline + net deposits with ts strictly after the baseline ts)`; baseline = the env's **earliest** equity_curve point |
| `total_pnl_pct` | `total_pnl_usd / (baseline + those deposits) × 100`, 4 dp half-up; `null` when that denominator ≤ 0 |
| `pnl_since` | ts of the baseline point |
| `today_pnl_usd` | `equity − first equity point at or after 00:00 Asia/Baghdad today`; `null` if there is none yet today |
| `unrealised_pnl_usd` | Σ `upl` of the latest positions tick, only for USD-settled positions (USDT/USDC/USD); `null` if no positions tick exists, `"0"` if the tick is empty |
| `fees_paid_usd` | `−Σ bills.fee` over all USD-stable bills. Positive means paid; a net rebate is negative |
| `funding_net_usd` | `Σ bills.bal_chg` over bills with `type = "8"` (OKX "Funding fee"). Positive means received |
| `reconciled_at` | max `sync_status.reconciled_at` over the env's streams |
| `stale` | `true` if `reconciled_at` is null or older than 180 s |

Bill classification is the PHP enum `App\Support\Portfolio\BillType` (`1` Transfer, `2` Trade,
`8` FundingFee, from the OKX v5 docs for `GET /api/v5/account/bills`). Fees are not a bill type:
every bill has its own `fee` field. ATL-11 had not landed when this was written, and there are no
Python bill-type constants on develop yet. **ATL-11: use `"8"` for funding and `fee` for fees, and
store the OKX `state` of deposits/withdrawals raw (`"2"` = success)**, otherwise net deposits
count nothing.

## Endpoints

### GET /api/portfolio/summary

```json
{
  "data": {
    "env": "demo",
    "total_equity_usd": "5136121.59",
    "net_deposits_usd": "1000",
    "total_pnl_usd": "135121.59",
    "total_pnl_pct": "2.7019",
    "pnl_since": "2026-09-28T17:36:00.000000Z",
    "today_pnl_usd": "7219.64",
    "unrealised_pnl_usd": "271.05",
    "fees_paid_usd": "13.88991",
    "funding_net_usd": "-0.036183",
    "equity_as_of": "2026-10-28T17:36:00.000000Z",
    "reconciled_at": "2026-10-28T17:36:00.000000Z",
    "stale": false
  }
}
```

Before any sync: `total_equity_usd`, `total_pnl_usd`, `total_pnl_pct`, `pnl_since`,
`today_pnl_usd`, `unrealised_pnl_usd`, `equity_as_of` and `reconciled_at` are `null`;
`net_deposits_usd`, `fees_paid_usd` and `funding_net_usd` are `"0"`; `stale` is `true`.

### GET /api/portfolio/equity?range=1D|1W|1M|3M|YTD|ALL

`range` defaults to `1M`; anything else is 422 (`errors.range`). Windows end at now: 1D = 24 h,
1W = 7 days, 1M = 1 calendar month, 3M = 3 months, YTD = from 1 Jan 00:00 **Asia/Baghdad**,
ALL = no lower bound (`from: null`).

`points` are in ascending ts order, **at most 500**. When the window holds more, it is cut into
equal time buckets (width = ceil(span / 499) s) and the **last** point of each bucket is kept, so
the final point is always the current equity. `markers` are the successful deposits and
withdrawals in the window, in ascending order, with the same shape as a cash-flow row.

```json
{
  "data": {
    "range": "1D",
    "from": "2026-10-27T17:36:25.317211Z",
    "to": "2026-10-28T17:36:25.317211Z",
    "points": [
      { "ts": "2026-10-28T17:06:00.000000Z", "equity_usd": "5134973.76" },
      { "ts": "2026-10-28T17:21:00.000000Z", "equity_usd": "5135874.43" },
      { "ts": "2026-10-28T17:36:00.000000Z", "equity_usd": "5136121.59" }
    ],
    "markers": [
      { "id": "f3c9…", "kind": "deposit", "ccy": "USDT", "amount": "1000", "amount_usd": "1000",
        "state": "2", "tx_id": null, "ts": "2026-10-28T09:00:00.000000Z" }
    ]
  }
}
```

### GET /api/portfolio/holdings

One row per (account, ccy) from **each account's** newest balances tick (`trading` and
`funding` may be synced at different times; `taken_at` is the newer one). Sorted by `value_usd`,
largest first. `value_usd` = `eq_usd`, or `eq` for USD stablecoins when OKX gave no `eq_usd`,
otherwise `null`. `pct_of_portfolio` is relative to `total_value_usd` (4 dp; the rows sum to 100
within 0.01) and is `null` when the value is unknown. **`avg_cost_usd` and `change_24h_pct` are
always `null` in Phase 0** (decision #236; they come with the Executor patch). Show "—".

```json
{
  "data": {
    "taken_at": "2026-10-28T17:36:00.000000Z",
    "total_value_usd": "5136121.59",
    "items": [
      { "ccy": "USDT", "account": "trading", "quantity": "5099121.59", "available": "5092103.331667",
        "frozen": "7018.258333", "value_usd": "5099121.59", "pct_of_portfolio": "99.2796",
        "avg_cost_usd": null, "change_24h_pct": null },
      { "ccy": "USDC", "account": "trading", "quantity": "25000", "available": "25000", "frozen": "0",
        "value_usd": "25000", "pct_of_portfolio": "0.4867", "avg_cost_usd": null, "change_24h_pct": null },
      { "ccy": "USDT", "account": "funding", "quantity": "12000", "available": "12000", "frozen": "0",
        "value_usd": "12000", "pct_of_portfolio": "0.2336", "avg_cost_usd": null, "change_24h_pct": null }
    ]
  }
}
```

No balances yet: `{"data": {"taken_at": null, "total_value_usd": "0", "items": []}}`.

### GET /api/portfolio/positions

Rows of the newest positions tick with `pos ≠ 0`, ordered by `inst_id`. `side` is `long`/`short`:
from `pos_side` in long/short mode, from the sign of `pos` in net mode. `size` = |pos| **in
contracts** (OKX units; multiply by the instrument's `ctVal` for coins or ounces). `upl_usd` is set
only when the settle currency is USDT/USDC/USD (from `instruments.settle_ccy`, falling back to the
2nd segment of `inst_id`); otherwise it is `null`. `upl_pct` = OKX `uplRatio × 100` (return on
margin). **`stop`, `take_profits`, `agent` and `proposal_id` are always present and `null` until
Phase 2.**

```json
{
  "data": {
    "taken_at": "2026-10-28T17:36:00.000000Z",
    "items": [
      { "id": "809d7f30-…", "inst_id": "BTC-USDT-SWAP", "side": "long", "pos_side": "net", "size": "25",
        "leverage": "5", "entry": "67250.5", "mark": "68120.3", "liq_px": "54112.8", "margin": "3362.525",
        "settle_ccy": "USDT", "upl_usd": "217.45", "upl_pct": "6.466866", "margin_mode": "isolated",
        "opened_at": "2026-10-28T08:36:00.000000Z",
        "stop": null, "take_profits": null, "agent": null, "proposal_id": null },
      { "id": "85242c4a-…", "inst_id": "XAU-USDT-SWAP", "side": "short", "pos_side": "net", "size": "400",
        "leverage": "3", "entry": "2741.8", "mark": "2728.4", "liq_px": "3618.9", "margin": "3655.733333",
        "settle_ccy": "USDT", "upl_usd": "53.6", "upl_pct": "1.46619", "margin_mode": "isolated",
        "opened_at": "2026-10-27T14:36:00.000000Z",
        "stop": null, "take_profits": null, "agent": null, "proposal_id": null }
    ]
  }
}
```

No positions tick yet: `{"data": {"taken_at": null, "items": []}}`.

### GET /api/portfolio/cashflows?per_page=20&page=1&kind=deposit|withdrawal

Newest first. `per_page` 1–100 (default 20). `kind` is optional. Includes every state (pending,
failed, success); `state` is the raw OKX code (`"2"` = success). `amount_usd` is `null` for
non-stable currencies. This is the standard Laravel paginator shape:

```json
{
  "data": [
    { "id": "f3c94440-…", "kind": "deposit", "ccy": "USDT", "amount": "1000", "amount_usd": "1000",
      "state": "2", "tx_id": null, "ts": "2026-10-25T17:36:25.282876Z" }
  ],
  "links": { "first": "…?per_page=20&page=1", "last": "…?per_page=20&page=1", "prev": null, "next": null },
  "meta": { "current_page": 1, "from": 1, "last_page": 1, "links": ["…"], "path": "…/api/portfolio/cashflows",
            "per_page": 20, "to": 1, "total": 1 }
}
```

## PortfolioDemoSeeder (tests, local development, Storybook fixtures)

`php artisan db:seed --class=PortfolioDemoSeeder`. It **replaces the `demo` rows** of
equity_curve, balances_snapshots, positions_snapshots, bills, deposits_withdrawals and
sync_status. It throws `RuntimeException` when `APP_ENV=production`. Everything is anchored to
"now" (rounded to the minute):

- equity: 2,881 points, one every 15 min for 30 days, a deterministic random walk starting at
  `5000000` (so `total_pnl_pct` is non-null). Holdings add up exactly to the last point.
- balances: trading USDT (equity − 37,000), trading USDC 25,000, funding USDT 12,000.
- positions (net mode): BTC-USDT-SWAP +25 contracts at 5x, XAU-USDT-SWAP −400 contracts at 3x.
  Combined upl is 271.05.
- bills: 2 trade fees (Σ fee −13.88991) and 4 funding bills (Σ −0.036183).
- no deposits or withdrawals (a demo account has none); sync_status is reconciled now.

The 5M figure is illustrative only. Real demo checks compare with what OKX reports (decision #237).

## Not built here

Orders, History, Performance, the benchmark toggle, and average cost / 24h change (decision #236);
FX conversion of non-stable currencies; any live push (Reverb arrives in Phase 1, so poll for now).
