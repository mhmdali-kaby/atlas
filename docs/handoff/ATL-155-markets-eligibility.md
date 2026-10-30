# ATL-155 — Markets eligibility API

For ATL-131 (Settings → Markets page) and anyone who needs "why is this instrument not tradeable".

## Two-minute version

- `GET /api/settings/markets/eligibility` tells you, for up to 50 instIds, which Markets filter each
  one fails right now. Use it for the label next to every whitelisted instrument (decision #250 (1)).
- It is **read-only**: it never writes a settings version and never creates the settings row.
- Send the form's **unsaved** values as `draft[...]` to preview them; leave `draft` out to check the
  saved settings.
- `fails` is a list of codes in a fixed order. `eligible` is `fails == []`. The whitelist itself is
  not a filter: `on_whitelist` just says whether the id is on it. An instrument not on a non-empty
  whitelist is still not tradeable — the page already knows that from the list it draws.
- An unknown spread is **not** a failure (most instruments have no live ticker; see below). Show the
  `spread_unknown` note if you want, but don't paint it as a failure.

## Request

`GET /api/settings/markets/eligibility` — Sanctum session (`auth:sanctum`), like every settings route.

| param | required | notes |
| --- | --- | --- |
| `env` | no | `demo` (default) or `live` |
| `inst_ids[]` | yes | 1..50 instIds; trimmed, upper-cased, repeats dropped, answered in request order. 51+ → 422 |
| `draft[enabled_markets][]` | no | subset of `spot, crypto_perp, stock_perp, commodity_perp, futures, options` |
| `draft[whitelist][]`, `draft[blocklist][]` | no | instIds |
| `draft[max_spread_bps]` | no | decimal string, same range as PATCH (> 0, ≤ 10000) |
| `draft[min_24h_volume_usd_<market>]` | no | `<market>` = spot, crypto_perp, stock_perp, commodity_perp; decimal string ≥ 0 |

An **empty list** can't be written as `[]` in a query string: send `draft[blocklist]=` (empty value)
and it means `[]`. Any other `draft` key → 422 on `draft`. Validation errors use the same messages
as `PATCH /api/settings` (`settings.decimal_format`, `settings.inst_id_format`, …).

Example (axios): `api.get('/api/settings/markets/eligibility', { params: { env: 'demo',
inst_ids: ['XAU-USDT-SWAP', 'THIN-USDT-SWAP'], draft: { min_24h_volume_usd_crypto_perp: '1000000' } } })`
— axios' default serializer writes `inst_ids[]=…&draft[min_24h_volume_usd_crypto_perp]=…`.

## Response 200

```json
{
  "data": {
    "env": "demo",
    "as_of": "2026-10-30T08:00:12.345Z",
    "settings_version": 4,
    "draft": ["min_24h_volume_usd_crypto_perp"],
    "volume_basis": "24h quote volume summed from closed 1H candles (the Market Scout basis); USDT, USDC and USD quotes counted 1:1 as USD",
    "spread_basis": "live OKX ticker best bid/ask from trading_core, only when pushed within market_data_max_age_seconds",
    "instruments": [
      {
        "inst_id": "XAU-USDT-SWAP", "eligible": false, "fails": ["blocked"],
        "market": "commodity_perp", "on_whitelist": true,
        "volume_24h_usd": "24000000", "min_24h_volume_usd": "5000000",
        "spread_bps": null, "max_spread_bps": "8", "notes": ["spread_unknown"]
      },
      {
        "inst_id": "THIN-USDT-SWAP", "eligible": true, "fails": [],
        "market": "crypto_perp", "on_whitelist": true,
        "volume_24h_usd": "2400000", "min_24h_volume_usd": "1000000",
        "spread_bps": "0.0123", "max_spread_bps": "8", "notes": []
      },
      {
        "inst_id": "NOPE-USDT-SWAP", "eligible": false, "fails": ["unknown_instrument"],
        "market": null, "on_whitelist": false,
        "volume_24h_usd": null, "min_24h_volume_usd": null,
        "spread_bps": null, "max_spread_bps": null, "notes": []
      }
    ]
  }
}
```

`settings_version` is null when the env has no settings row yet (defaults are used). `draft` lists
the keys that were overridden. Money/bps are decimal strings. `min_24h_volume_usd` is null for
markets with no minimum (futures, options, other) — the Scout has none for them either.

## Fail codes (always in this order)

| code | meaning |
| --- | --- |
| `blocked` | on the blocklist (case-insensitive). Comes first; the blocklist wins over the whitelist |
| `unknown_instrument` | no instrument with that id in this env (or listed in the future). Nothing else is checked after it |
| `asset_class_disabled` | its market toggle is off. `enabled_markets: []` means **every** market is off |
| `not_live` | OKX `state` is not `live` |
| `listed_under_7_days` | `listTime` is not more than `agents.scout.universe.min_listed_days` (7) days ago |
| `min_24h_volume` | 24h USD volume below its market's minimum |
| `max_spread` | live spread above `max_spread_bps` |
| `no_data` | 24h volume can't be measured: no closed 1H bar in the last 48 h, or the quote currency isn't USDT/USDC/USD |

`notes` (not failures): `spread_unknown` (no fresh ticker), `age_unknown` (OKX gave no listTime),
`volume_unknown` / `volume_not_usd` (the reason behind `no_data`).

## Where the numbers come from (freshest available)

- **Volume:** the `candles` table, closed confirmed `1H` bars, exactly as
  `atlas_agents.tools.market.compute_tickers` does it (window of 24 h ending at the newest bar, bars
  within the last 2 × 24 h, bar volume = `vol_ccy_quote`, else `vol_ccy × c`). This is what Market
  Scout's universe filter uses, so the label and the Scout agree. There is no `instrument_stats_daily`
  table, and the Scout `feature_table` report only holds instruments that already *passed* the
  filter, so neither can explain a failure.
- **Spread:** trading_core's live snapshot in Redis (`atlas:md:<env>:<instId>`, fields `ticker` and
  `tickers_ms`, ATL-125), only if the ticker was pushed within `market_data_max_age_seconds`. It is
  the only stored spread. The WS feed subscribes only some instruments, so most rows will say
  `spread_unknown`; that matches the Scout, which keeps instruments with an unknown spread.

## Where it differs from the Scout, on purpose

- No candles → Scout counts volume 0 and rejects on `volume`; this API says `no_data`.
- Non-USD quote → Scout rejects `volume_unknown`; this API says `no_data` (+ note `volume_not_usd`).
- `enabled_markets: []` → this API treats it as all off. Today's Scout `config_from` falls back to the
  defaults on an empty list; ATL-156 fixes that on the Scout side.

## Files

`backend/routes/api/settings_markets.php`, `app/Http/Controllers/Settings/MarketsEligibilityController.php`,
`app/Http/Requests/Settings/MarketsEligibilityRequest.php`, `app/Support/Settings/MarketsEligibility.php`
(filters), `app/Support/Settings/TickerSpreads.php` (Redis spread reader; swap it in tests with
`app()->instance(TickerSpreads::class, …)`), tests in `tests/Feature/Settings/MarketsEligibilityApiTest.php`.
`UpdateSettingsRequest::instId()` and `::decimalRange()` became public so both requests share the rules.

## Not built

No frontend, no translations (the page owns the human copy for each code), no whitelist code.
