# ATL-59 — atlas_ingest runtime: poll loop, collectors, pipeline, enrichment hook

Process: `uv run --package atlas-ingest python -m atlas_ingest` (started by `./atlas up` as `ingest`,
logs in `./atlas logs ingest`, stopped by `./atlas down`; SIGTERM/SIGINT stop it cleanly after
in-flight polls). It reads DB_* and APP_KEY from its environment, which `./atlas up` fills from
`backend/.env` exactly as for trading_core. It does not use Redis yet (news alerts arrive with
ATL-65).

## The two-minute version

- **Add a collector = add one file** in `services/ingest/src/atlas_ingest/collectors/`, decorated with
  `@register(keys=(...))` or `@register(kinds=(...))`. `discover()` imports every module there. Exact
  source key beats kind. Never edit a shared list.
- **Collectors only fetch and parse.** Return `RawItem`s; canonicalisation, dedupe, article
  extraction, language, enrichment and the insert are the pipeline's job.
- **Failures:** raise `CollectError("short safe text")` or let an `httpx` error propagate. The runner
  stores `HTTP 500` / `timeout` / `network error (ConnectError)` / your CollectError text in
  `last_error`, never `str(httpx_exc)` (it contains the URL). Returning `[]` is a success.
- **Keys:** `SourceConfig.api_key` is already decrypted. Put it in the request only. Do not put it in
  a log, `RawItem.raw`, or an exception message. httpx's own INFO request log (which prints the
  full URL) is pinned to WARNING by `configure_logging`.
- **The enrichment hook** is `atlas_ingest.enrichment.enrich_items`, a no-op today. Fill it in, or
  pass `Pipeline(enrich=your_hook)`.

## Collector protocol

```python
# atlas_ingest/collectors/__init__.py
@dataclass(frozen=True)
class SourceConfig:
    key: str; kind: str; category: str; name: str
    url: str | None
    poll_seconds: int
    api_key: str | None          # decrypted; repr=False

@dataclass(frozen=True)
class CollectContext:
    http: httpx.AsyncClient      # shared client, follow_redirects, UA "Atlas-Ingest/0.1 ..."
    now: datetime                # UTC poll time (the runner's clock; fake in tests)
    stock_symbols: Callable[[], Awaitable[list[str]]]   # live stock/ETF underlyings, any env: ["AAPL", "TSLA", ...]

class Collector(Protocol):
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]: ...
```

Example (a keyed API source):

```python
# atlas_ingest/collectors/cryptopanic.py
from atlas_ingest.collectors import CollectContext, CollectError, SourceConfig, register
from atlas_ingest.items import RawItem

@register(keys=("cryptopanic",))
class CryptoPanicCollector:
    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        r = await ctx.http.get(source.url or "", params={"auth_token": source.api_key, "public": "true"})
        r.raise_for_status()
        body = r.json()
        if "results" not in body:
            raise CollectError("unexpected response shape")
        return [RawItem(url=p["url"], title=p["title"], published_at=..., raw={"id": p["id"]}) for p in body["results"]]
```

The class is instantiated once per runner with no arguments and reused across polls.

## RawItem (`atlas_ingest.items`)

| field | type | meaning |
|---|---|---|
| url | str | the link as given; the pipeline canonicalises it |
| title | str | plain text |
| summary | str \| None | plain text (HTML stripped) |
| body | str \| None | full article text if the source has it; **None makes the pipeline fetch the page and run trafilatura** (up to `news.extract.max_per_poll` per poll) |
| published_at | datetime \| None | UTC; None → fetched_at |
| language | str \| None | a hint (`en-US`); detection from the text wins |
| raw | dict | JSON-safe payload stored in `news_items.raw`; no key, no keyed URL |

`PreparedItem` is the row about to be inserted: every `news_items` column (`instruments`,
`asset_classes`, `entities`, `event_type`, `sentiment` Decimal, `importance` 1–5, `novelty` Decimal,
`cluster_id`, `embedding` list of 384 floats, `title_original`) plus a pre-generated `id` (uuid4).

## Pipeline order (`atlas_ingest.pipeline.Pipeline.process`)

1. `canonicalize_url` (lowercase scheme/host, drop default port, fragment, `utm_*`, `fbclid`,
   `gclid`, Yahoo's `.tsrc`, trailing slash; other params keep their order) and dedupe inside the batch.
2. One indexed lookup on `canonical_url_hash` (sha256 computed in Python, same bytes as the DB's
   generated column) drops seen URLs **before** any page is fetched.
3. Article extraction for new items with no body: browser user agent (CoinDesk returns 429 to bot
   agents), `news.extract.timeout_seconds` per request (`Extractor.host_timeouts` overrides per
   host), and a host that errors is skipped for the rest of the poll. Failure = `body` stays None.
4. Language: `langdetect` (seeded, deterministic) on title + summary + first 1500 body chars, else
   the feed's hint (`en-US` → `en`).
5. `await enrich(items, EnrichContext(engine, now))`.
6. `INSERT ... ON CONFLICT (canonical_url_hash) DO NOTHING RETURNING id`; the count is `inserted`.

## The enrichment hook

```python
# atlas_ingest/enrichment.py
@dataclass(frozen=True)
class EnrichContext:
    engine: Engine
    now: datetime            # UTC poll time

EnrichHook = Callable[[Sequence[PreparedItem], EnrichContext], Awaitable[None]]

async def enrich_items(items: Sequence[PreparedItem], ctx: EnrichContext) -> None: ...  # no-op
```

It gets every new item of one poll at once, so you can embed in one batch. It mutates items in place.
To cluster, create or update the `news_clusters` row yourself through `ctx.engine`, then set
`item.cluster_id`. The items are not in the DB yet. An exception in the hook fails the whole poll
(counts as a source failure), so nothing half-enriched is stored. DB work belongs in
`asyncio.to_thread`, as the pipeline does.

## Runner behaviour (`atlas_ingest.runner.IngestRunner`)

- Ticks every `news.tick_seconds` (1 s). Enabled rows are re-read every `news.reload_seconds` (60 s),
  **and each source's row is re-read just before it runs**, so disabling it, removing its key or a
  degraded window takes effect on the next due poll.
- First poll at startup, then every `poll_seconds`. A source never overlaps itself.
- Skipped with **no request and no column change**: `requires_key` with `api_key` NULL (needs_key),
  `disabled_until > now` (degraded), and kinds with no registered collector (one warning per source).
  Today only `kind: rss` has a collector, so the api/calendar/earnings/sentiment rows just wait.
- Success: `consecutive_failures=0`, `disabled_until=NULL`, `last_error=NULL`,
  `last_success_at=now`, and `last_item_at=now` when something was inserted.
- Failure: `consecutive_failures += 1`, `last_error` set; at `news.failure_threshold` (3) or more,
  `disabled_until = now + news.disable_minutes` (30). After the window the source is retried; the
  counter is not reset by the window, so one more failure disables it again at once.
- An undecryptable `api_key` (wrong APP_KEY) is a failure with `last_error` "api_key cannot be decrypted (check APP_KEY)".

## Sources

- `kind: rss` is served by `collectors/rss.py` (feedparser). A URL containing `{symbol}` is a per-symbol
  template, expanded for every `ctx.stock_symbols()` value. It fails only if every symbol's feed fails.
- New seeded source **`rss.yahoo`** (markets_news, 300 s):
  `https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US`. Its items carry
  `raw.symbol`, which is a useful linking hint.
- The CoinDesk URL in defaults lost its trailing slash (the feed 308-redirects to it). Existing seeded
  rows keep the old URL, which still works through the redirect.
- Defaults added under `news`: `reload_seconds`, `tick_seconds`, `extract.{timeout_seconds, max_bytes, max_per_poll}`.

## Tests

`services/ingest/tests/test_ingest_runtime.py` holds respx-recorded CoinDesk and Yahoo TSLA feeds
(`tests/fixtures/rss/`, recorded 2026-10-29, trimmed to 5 and 6 items). It uses `FakeClock`
(`runner(clock=...)`) and `await runner.tick()`, which starts the due sources and waits for them. The
test fixture wipes `news_items` and `news_sources` in this checkout's test DB.

Not built here: the API, calendar, earnings and sentiment collectors, enrichment, and `news.alert`
publishing.
