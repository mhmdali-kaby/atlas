# ATL-45 — OKX public market-data methods on `OkxClient`

Extends the ATL-7 client (`services/trading_core/src/trading_core/okx/`). Every method below is **public and
unsigned** (no credentials needed: `OkxClient("demo")`), goes through the same retry + per-endpoint token
bucket, and sends `x-simulated-trading: 1` in demo only (from `okx.environments.<env>.headers`). No DB writes.

```python
from trading_core.okx import OkxClient
async with OkxClient("demo") as okx:
    async for page in okx.iter_candles("BTC-USDT-SWAP", bar="1H", since=start_utc, limit=300):
        ...  # page: list[Candle], newest first
```

## Methods

`Cursor` = aware UTC `datetime` | epoch-ms `int` | epoch-ms `str` | `None` (a naive datetime raises `ValueError`).
`limit` above the documented page size raises `ValueError` before any request.

| Method | Endpoint | Args | Returns | Rate limit (per IP) |
| --- | --- | --- | --- | --- |
| `get_candles(inst_id, *, bar="1m", after, before, limit)` | market/candles | `limit` 1..300 | `list[Candle]` newest first | 40 / 2 s |
| `get_history_candles(inst_id, *, bar="1m", after, before, limit)` | market/history-candles | `limit` 1..100 | `list[Candle]` | 20 / 2 s |
| `iter_candles(inst_id, *, bar, after, since, limit, history=False)` | either of the above | walks back with `after` = oldest ts of the previous page | `AsyncIterator[list[Candle]]` | as above |
| `get_tickers(inst_type, *, inst_family)` (ATL-7) | market/tickers | | `list[Ticker]` | 20 / 2 s |
| `get_funding_rate(inst_id)` | public/funding-rate | swap instId | `FundingRate` | 10 / 2 s |
| `get_funding_rate_history(inst_id, *, after, before, limit)` | public/funding-rate-history | cursors are `funding_time`; `limit` 1..100 | `list[FundingRateHistory]` newest first | 10 / 2 s |
| `get_open_interest(inst_type, *, inst_family, inst_id)` | public/open-interest | SWAP/FUTURES/OPTION | `list[OpenInterest]` | 20 / 2 s |
| `get_open_interest_volume(ccy, *, period="5m", begin, end)` | rubik/stat/contracts/open-interest-volume | `ccy` like `BTC`; period `5m`/`1H`/`1D` | `list[OpenInterestVolume]` | 5 / 2 s |
| `get_long_short_ratio(ccy, *, period="5m", begin, end)` | rubik/stat/contracts/long-short-account-ratio | | `list[LongShortRatio]` | 5 / 2 s |
| `get_taker_volume(ccy, *, inst_type="CONTRACTS", period="5m", begin, end)` | rubik/stat/taker-volume | `inst_type` SPOT or CONTRACTS | `list[TakerVolume]` | 5 / 2 s |
| `get_mark_price(inst_type, *, inst_family, inst_id)` | public/mark-price | | `list[MarkPrice]` | 10 / 2 s |

`bar` and `period` are passed through as OKX strings (`1m 3m 5m 15m 30m 1H 2H 4H 6H 12H 1D 1W 1M`, plus OKX's
`…utc` variants); OKX rejects unknown ones with an `OkxError`.

## Models (`trading_core.okx.models`, all frozen, numbers `Decimal | None`, times aware UTC)

- `Candle(ts, o, h, l, c, vol, vol_ccy, vol_ccy_quote, confirm: bool)` — parsed from OKX's positional array
  `[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]`. `ts` is the bar **open** time. `confirm=False` = bar still
  forming: ATL-46/47 must store only `confirm=True` bars (or overwrite the forming one).
- `FundingRate(inst_type, inst_id, funding_rate, next_funding_rate, funding_time, next_funding_time,
  min_funding_rate, max_funding_rate, sett_funding_rate, sett_state, premium, interest_rate, impact_value,
  method, formula_type, ts)`
- `FundingRateHistory(inst_type, inst_id, funding_rate, realized_rate, funding_time, method, formula_type)`
- `OpenInterest(inst_type, inst_id, oi, oi_ccy, oi_usd, ts)`
- `OpenInterestVolume(ts, oi, vol)` — array `[ts, oi, vol]`, both USD.
- `LongShortRatio(ts, ratio)` — array `[ts, ratio]`.
- `TakerVolume(ts, sell_vol, buy_vol)` — array `[ts, sellVol, buyVol]`.
- `MarkPrice(inst_type, inst_id, mark_px, ts)`

Array models (`_ArrayRow`) refuse a row with too few columns and ignore extra trailing ones.

## Config (`config/defaults.yaml`, regenerate with `php artisan atlas:defaults`)

- `okx_rest.rate_limits.<path>` — the limits above. **Note:** the task said "new `okx.rate_limits` block";
  ATL-7 already keeps every endpoint's limit in `okx_rest.rate_limits`, and `RateLimiter` reads only that,
  so the new entries went there instead of starting a second source of limits.
- `okx_rest.page_limits` — max `limit` per request: candles 300, history-candles 100, funding-rate-history 100.
- `okx_rest.rate_limit_codes: ["50011"]` — together with HTTP 429, a "too many requests" answer is retried
  after `max(jittered backoff, that endpoint's per_seconds window)`; other retryable errors keep plain
  jitter. Still `max_attempts: 3`, then the `OkxError(code="50011")`/`OkxHttpError(429)` is raised.

## Verification against the OKX docs — read this

The build sandbox had **no network** (DNS for www.okx.com failed; web fetch and search were not permitted), so
the paths, parameters, response shapes, page sizes and limits are from the OKX v5 API docs as I know them
(docs as of mid-2026), not re-read on 2026-10-29. The fixtures in `tests/fixtures/okx/` for these endpoints are
hand-written in the documented shape (see that README). Before ATL-46 trusts them at scale, someone with
network should run each method once against demo and re-record the fixtures. The page sizes are the
conservative ones: funding-rate-history may now allow 400, but 100 always works.

## Two-minute version

- `iter_candles` is the backfill primitive: pass `since=` to stop at a start date; it stops on an empty page or
  a cursor that does not move, and drops bars older than `since` from the last page.
- Share one `RateLimiter()` across every client in a process — public limits are per IP, so two clients with
  separate limiters could together exceed OKX's limit.
- A token bucket lets a full burst of `requests` through at once and then refills at `requests/per_seconds`, so
  in the first window after an idle spell up to about 2× the limit can go out (steady state is exactly the
  limit). OKX may answer 50011 to the tail of such a burst; the client then waits a full window and retries.
  If backfill logs show many 50011s, lower `requests` in the YAML, or ask for a sliding-window limiter.
- `TokenBucket` now treats 0.999999… tokens as one (float tolerance); without it a fake or fine-grained clock
  could spin forever on ever-smaller sleeps.
