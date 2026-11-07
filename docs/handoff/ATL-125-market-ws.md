# ATL-125 — live OKX WebSocket market data (trading_core/ws)

For: ATL-127 (Risk Engine check 1 "market data fresh < 30 s"), ATL-134 (derivatives features),
ATL-135 (Technical Analyst), and anything else that needs a live price, mark or book.

## What runs

`trading_core.ws.MarketFeed`, started in the FastAPI lifespan beside the scheduler (only in the
real app: `create_app()` without an injected scheduler, `TRADING_CORE_SCHEDULER` and
`TRADING_CORE_MARKET_WS` not `0`). Two sockets for the active env, URLs from
`okx.environments.<env>.ws_public_url` / `ws_business_url` in `config/defaults.yaml`:

| socket   | channels (`market_ws.*_channels`)  |
| -------- | ---------------------------------- |
| public   | `tickers`, `mark-price`, `books5`  |
| business | `candle1m`, `candle5m`, `candle1H` |

Instruments = `market_ws.always_subscribe` (`BTC-USDT-SWAP`) + today's (Asia/Baghdad) latest
watchlist version minus owner-removed picks + instruments with `pos != 0` at the newest account
sync. Re-read every `subscriptions_refresh_seconds` (15 s); only the diff is (un)subscribed.
`await feed.refresh()` forces a re-read now (nothing calls it yet — there is no watchlist/positions
change event to hook).

Text `ping` every 25 s per socket (protocol pings off). On a drop: wait
`min(60, 1 * 2^attempt) * (1 - 0.5 * random())` s, reconnect, resubscribe everything; attempt resets
after a successful connect.

## Redis snapshot — `atlas:md:<env>:<instId>` (a hash)

Read it with `atlas_shared.market_data.read_snapshot(client, env, inst_id) -> MarketSnapshot`
(`client` = `atlas_shared.bus.make_client()`, `decode_responses=True`). Do not parse the hash
yourself.

| field            | value                                                                         |
| ---------------- | ----------------------------------------------------------------------------- |
| `last_msg_ms`    | epoch ms UTC of the last message on any channel for the instrument            |
| `<channel>_ms`   | per channel: `tickers_ms`, `mark-price_ms`, `books5_ms`, `candle1m_ms`, …     |
| `ticker`         | JSON of OKX's last `tickers` data row, verbatim (camelCase, strings)          |
| `mark`           | JSON of the last `mark-price` row: `{"instType","instId","markPx","ts"}`      |
| `books5`         | JSON of the last `books5` row: `{"asks":[["px","sz","0","n"],…],"bids":…,"ts":"ms","seqId":…}` |
| `closed:<bar>`   | open time (ms) of the newest confirmed bar already published (dedupe across restarts) |

TTL `snapshot_ttl_seconds` (1 day), refreshed on every message.

```python
snap = read_snapshot(r, "demo", "BTC-USDT-SWAP")
snap.last_msg_at            # datetime | None
snap.is_stale(now)          # no message for > market_ws.market_data_max_age_seconds (30) or never
snap.channel_stale("books5", now)
snap.ticker["last"], snap.mark["markPx"], snap.books5["bids"]
```

`market_ws.market_data_max_age_seconds` is the single staleness limit. If ATL-124 adds a Risk
settings field of the same name, have it default to this one rather than a second number.

## Events on `atlas.market` (§3.2 envelope, `source = "service:trading_core.market_ws"`)

`market.tick` — at most once per second per instrument, from `tickers`. Decimals are strings.
```json
{"type":"market.tick","env":"demo","ts":"2026-10-30T09:00:00Z","source":"service:trading_core.market_ws",
 "correlation_id":null,
 "payload":{"inst_id":"BTC-USDT-SWAP","last":"100.5","bid_px":"100.4","bid_sz":"2","ask_px":"100.6","ask_sz":"3",
  "open24h":"99","high24h":"101","low24h":"98","vol24h":"10000","vol_ccy24h":"1000",
  "ts":"2026-10-30T09:00:00+00:00","received_at":"2026-10-30T09:00:00+00:00"}}
```

`candle.closed` — once per confirmed bar (`confirm=1`), after it is upserted into `candles`
through `trading_core.backfill.runner.upsert_rows` (the poller's ON CONFLICT path). `ts` is the
bar's open time.
```json
{"type":"candle.closed","payload":{"inst_id":"BTC-USDT-SWAP","bar":"1H","ts":"2026-10-30T08:00:00+00:00",
 "o":"100","h":"110","l":"95","c":"105","vol":"1000","vol_ccy":"10","vol_ccy_quote":"1050"}}
```

`market.stale` — when a subscribed instrument passes 30 s without a message (checked every
`stale_check_seconds`, 5 s; also counts from subscription time if nothing ever arrived), once per
transition: `{"inst_id","last_msg_at": ISO | null,"max_age_seconds":30.0}`.
`market.fresh` — the first message after that: `{"inst_id","last_msg_at"}`.

Nothing consumes these yet; the bridge does not forward `atlas.market` to Reverb.

## Agents

`okx_market.get_orderbook` now reads `books5` from the snapshot: `available=true` with
`bids`/`asks` as `[[price, size], …]` (≤ 5 levels, `depth` truncates), `as_of` = OKX book time,
`source = "redis:atlas:md (OKX books5 WebSocket)"`. `available=false` with empty sides when the
instrument is not subscribed, the book is older than 30 s, Redis is unreachable, or in replay.
`ToolContext.market_snapshot(env, inst_id)` is the reader; tests pass a fake.

`get_mark_price` still uses the newest 1m close as a proxy — switching it to `snap.mark` is left
for whoever needs it (ATL-135 probably).

## Not built

No private channels (ATL-G), no `trades`/`funding-rate`/`open-interest` WS channels (REST covers
them), no 4H/1D candle channels (the REST poller keeps those). The poller still runs for 1m/5m/1H;
both write the same rows, so whichever comes first wins and the other is a no-op update.

## Tests

`services/trading_core/tests/test_market_ws.py`: a fake `websockets` server per socket and a
simulated clock (`FakeClock.advance`) drive pings, staleness and backoff. Redis DB 15, the
checkout's test Postgres.
