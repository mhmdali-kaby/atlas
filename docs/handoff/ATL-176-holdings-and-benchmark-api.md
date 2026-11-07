# ATL-176 — Holdings average cost / 24 h change, and the benchmark series API

For whoever builds the Overview benchmark toggle and the Holdings columns in the SPA.

## Two-minute version

- `GET /api/portfolio/holdings` items now carry `avg_cost_usd`, `change_24h_pct`,
  `change_24h_reason` and `change_24h_inst_id` (code: `App\Support\Portfolio\HoldingsMarketData`).
- New `GET /api/portfolio/benchmark?symbol=BTC|SPY|XAU&range=1D|1W|1M|3M|YTD|ALL[&env=]`
  (route file `routes/api/portfolio_benchmark.php`, `BenchmarkController`, `BenchmarkService`).
  It returns a series you can draw straight on the equity chart: its first point **is** the
  equity curve's first point in the same range, and every later point is
  `base_equity × close / base_close`.
- When it can't be drawn, `available: false` with `reason`: `not_in_universe` (no candidate instId
  in this env's `instruments`), `no_equity` (no equity point in the range), `no_candles`.
- All numbers are decimal strings. All knobs are in `config/defaults.yaml` →
  `portfolio_holdings` and `portfolio_benchmark`.

## Holdings item (new fields)

```json
{ "ccy": "BTC", "account": "trading", "quantity": "1.5", "available": "1.5", "frozen": "0",
  "value_usd": "99000", "pct_of_portfolio": "95.1234",
  "avg_cost_usd": "60000.50", "change_24h_pct": "10", "change_24h_reason": null,
  "change_24h_inst_id": "BTC-USDT-SWAP" }
```

- `avg_cost_usd`: moving average cost of the asset's **spot** fills against `USDT`/`USDC`
  (`BTC-USDT`, `BTC-USDC`; swap fills don't change a holding). Buy adds `px × sz`; sell removes
  size at the current average; sold to zero → starts again. Fees not included. Fixed decimals,
  **not** stripped: `max(2, decimals of the spot tickSz)`, or 8 when there is no spot instrument
  and the average is below 1 USD. `null` when there are no such fills or nothing is left from them
  (so stablecoins and deposited-only coins are null).
- `change_24h_pct`: newest close vs the newest close ≥ 24 h before it, on the first instrument
  found in order `CCY-USDT`, `CCY-USDT-SWAP`, `CCY-USDC`, `CCY-USDC-SWAP`, trying bars `1H` then
  `1D`. Percent at scale 4, trailing zeros stripped (`"10"`, `"-25"`, `"1.2345"`).
  `null` + `change_24h_reason`: `no_instrument` (none of those instIds exists in this env —
  includes USDT itself) or `no_candles`.

## GET /api/portfolio/benchmark (auth:sanctum)

`symbol` required (keys of `portfolio_benchmark.symbols`), `range` default `1M`, `env` default env.
422 on an unknown symbol or range.

```json
{ "data": {
  "symbol": "BTC", "env": "demo", "range": "1M",
  "from": "2026-09-30T10:00:00.000000Z", "to": "2026-10-30T10:00:00.000000Z",
  "available": true, "reason": null, "inst_id": "BTC-USDT-SWAP", "bar": "4H",
  "base": { "ts": "2026-10-20T10:30:00.000000Z", "equity_usd": "5000000", "close": "60000",
            "candle_ts": "2026-10-20T10:00:00.000000Z" },
  "points": [
    { "ts": "2026-10-20T10:30:00.000000Z", "close": "60000", "value_usd": "5000000" },
    { "ts": "2026-10-21T10:00:00.000000Z", "close": "66000", "value_usd": "5500000" }
  ] } }
```

Unavailable: same keys, `available: false`, `reason` set, `bar`/`base` null, `points: []`
(`inst_id` is set for `no_equity`/`no_candles`).

- Instruments: `BTC → BTC-USDT-SWAP, BTC-USDC-SWAP`; `SPY → SPY-USDT-SWAP, SPY-USDC-SWAP`;
  `XAU → XAU-USDT-SWAP, XAU-USDC-SWAP`; first one present in the env wins.
- Bars per range (first with a usable base candle wins): 1D 15m→1H, 1W 1H→4H, 1M 4H→1D,
  3M/YTD/ALL 1D. Candle `ts` is the bar's open time.
- Base close = newest candle at or before the first equity point; if none, the first candle after
  it. `value_usd` is at scale 8 HALF_UP, then stripped.
- `from`/`to` use the same range maths as `/api/portfolio/equity`, so the two series line up.

## Not here

No UI. No FX: non-USD-quoted instruments are not searched. No staleness flag on the 24 h change
(an old newest candle still produces a figure).
