# ATL-60 — CryptoPanic, Finnhub news and OKX announcements collectors

Three files in `services/ingest/src/atlas_ingest/collectors/`, each registered by exact source key:

| source key | file | request | key goes in |
|---|---|---|---|
| `cryptopanic` | `cryptopanic.py` | `GET <url>?public=true&kind=news&auth_token=…` | query (the only option CryptoPanic offers) |
| `finnhub.news` | `finnhub.py` | `GET <url>?category=general`, then `GET …/company-news?symbol=S&from=D-2&to=D` per symbol | `X-Finnhub-Token` header |
| `okx.announcements` | `okx_announcements.py` | `GET <url>` with `Accept-Language: en-US`, public | — |

## The two-minute version

- **Rate limits: raise `RateLimited(seconds)`** (new, in `atlas_ingest.collectors`, a `CollectError`
  subclass). The runner counts it as one failure (`last_error = "rate limited (HTTP 429)"`) and sets
  the source's next poll to at least `now + seconds`, even when `poll_seconds` is shorter.
  `retry_after_seconds(response, default)` reads `Retry-After` and never returns less than the
  default. The calendar/earnings collectors on the same Finnhub key should use both.
- **`ctx.stock_symbols()` is now active-env only.** `IngestRunner(env=...)` defaults to
  `aliases.active_env()` (ATLAS_ENV → ATLAS_DEFAULT_ENV → config default); `sources.stock_symbols(engine, env)`
  filters `instruments.env`. This also narrows `rss.yahoo` to the active env's symbols.
- **Finnhub budget:** `news.collectors.finnhub.max_calls_per_poll` (20) = 1 general + 19 company
  symbols per poll, rotating through the sorted symbol list across polls (a 429 mid-batch resumes at
  the refused symbol), `request_interval_seconds` 0.25 between calls. Free tier is 60/min and
  30/s per key; at 180 s polls this uses < 7/min.
- **OKX error** (`code != "0"` or HTTP error) fails the poll with `OKX error <code>` / `HTTP <n>` and
  yields nothing. There is no scraping fallback, on purpose.
- Items carry `raw.provider`; Finnhub company items carry `raw.symbol` (a linking hint, like
  `rss.yahoo`); CryptoPanic items carry `raw.currencies` (["BTC"]) and `raw.publisher`.
- CryptoPanic's free plan gives no `original_url`, so `url` is CryptoPanic's own news page; the
  pipeline's trafilatura extraction then runs on that page.

## Defaults added (`config/defaults.yaml` → `news.collectors`)

```yaml
collectors:
  finnhub: { max_calls_per_poll: 20, request_interval_seconds: "0.25", company_news_days: 2, rate_limit_window_seconds: 60 }
  cryptopanic: { rate_limit_window_seconds: 60 }
  okx_announcements: { language: en-US }
```

## Not verified

The build had no network access, so the fixtures in `services/ingest/tests/fixtures/api/` are written
from the providers' documented shapes, not recorded, and the provider terms in the module docstrings
are from their published pages as last known. Re-record and re-check on a networked run.
