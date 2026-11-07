# ATL-57 — news, calendar and source-registry schema

Migration: `backend/database/migrations/2026_10_29_300000_create_news_tables.php`.
SQLAlchemy: `atlas_shared.db` — `NewsSource`, `NewsItem`, `NewsCluster`, `EconomicEvent`,
`EarningsEvent`, `MarketSentiment`, `InstrumentAlias`, plus the `Vector(dim)` column type.
Eloquent: only `App\Models\NewsSource` (encrypted `api_key`, hidden from `toArray()`, `apiKeyLast4()`).
`shared/tests/test_schema_reflection.py` fails if a model drifts from the migration.

None of these tables has an `env` column: news is the same for demo and live.
Every table: `id uuid` PK, `created_at`/`updated_at` timestamptz(6) (nullable, set them yourself from Python).

## The two-minute version

- **Dedupe news on `canonical_url_hash`**, a generated `bytea` = sha256 of `canonical_url` (via the
  IMMUTABLE SQL function `atlas_sha256_text(text)`). Never set it; write with
  `insert(NewsItem).values(...).on_conflict_do_nothing(index_elements=["canonical_url_hash"])`
  (or `do_update`). Canonicalise the URL before insert — the DB only compares bytes.
- **Embeddings**: `NewsItem.embedding` is `Vector(384)`; pass a `list[float]` of exactly 384 values
  (the type raises `ValueError` otherwise) and you get a `list[float]` back. Cosine search:
  `ORDER BY embedding <=> CAST(:q AS vector)` uses the HNSW index `news_items_embedding_hnsw`
  (`vector_cosine_ops`). Cosine *similarity* = `1 - (a <=> b)`.
- **text[]**: `instruments`, `asset_classes` (news_items) and `instruments` (news_clusters) map to
  `list[str]` in Python; NOT NULL default `{}`. Query with `instruments @> ARRAY[:id]::text[]` or
  `&&` — those use the GIN index; `= ANY(instruments)` does not. From PHP use
  `App\Casts\PostgresTextArray` (there is no Eloquent model for news_items yet).
- **CHECKs**: `news_items.importance` 1–5 (NOT NULL, default 1), `news_items.sentiment` −1..1,
  `news_clusters.max_importance` 1–5, `economic_events.importance` 1–5.
- **Source keys** come from `config/defaults.yaml` → `news.sources`; the seeder is insert-only
  (`firstOrCreate` by key), so owner edits survive re-seeding. Add a source = add a yaml entry,
  `php artisan atlas:defaults`, re-seed.
- **Status** is not a column: derive it. `enabled=false` → disabled; `requires_key` and
  `api_key IS NULL` → `needs_key`; `disabled_until > now()` → `degraded`; else ok.
  `news.failure_threshold` (3) and `news.disable_minutes` (30) are in defaults.

## Tables

### news_sources
| column | type | notes |
|---|---|---|
| key | varchar(64) UNIQUE | source key, see list below |
| kind | enum rss\|api\|calendar\|earnings\|sentiment | transport |
| category | enum exchange\|crypto_news\|markets_news\|calendar\|earnings\|sentiment, NOT NULL | topic the Data sources screen groups by and the "all sources for a class are down" banner keys on |
| name | varchar(100) | display name |
| url | text null | endpoint/feed URL |
| enabled | bool default true | owner toggle |
| poll_seconds | int | |
| requires_key | bool default false | |
| api_key | text null | Laravel `encrypted` ciphertext. Python: `atlas_shared.laravel_crypt.decrypt_string(raw, APP_KEY)`. Never log, never return; show last 4 only |
| consecutive_failures | int default 0 | ingest writes |
| disabled_until | timestamptz null | ingest writes (now + 30 min after 3 failures) |
| last_success_at, last_item_at | timestamptz null | ingest writes |
| last_error | text null | ingest writes — never put a key or a keyed URL in it |

### news_items
| column | type | notes |
|---|---|---|
| source | varchar(64) | a `news_sources.key` (not a FK) |
| url | text | as fetched |
| canonical_url | text | |
| canonical_url_hash | bytea, generated, UNIQUE | do not write |
| title | text | English (or original if untranslated) |
| title_original | text null | original-language title (translation is patch E) |
| summary, body | text null | |
| published_at | timestamptz NOT NULL, btree | fall back to fetched_at if the feed has none |
| fetched_at | timestamptz NOT NULL | |
| language | varchar(16) null | |
| entities | jsonb null | |
| instruments | text[] NOT NULL default `{}`, GIN | instIds / families |
| asset_classes | text[] NOT NULL default `{}` | |
| event_type | enum null | earnings, guidance, macro_release, central_bank, regulation, hack_exploit, listing, delisting, ETF_flow, M&A, lawsuit, product, partnership, token_unlock, geopolitics, supply_shock, other (exact case) |
| sentiment | numeric(6,4) null, CHECK −1..1 | |
| importance | smallint NOT NULL default 1, CHECK 1..5 | |
| novelty | numeric(6,4) null | |
| cluster_id | uuid null FK news_clusters ON DELETE SET NULL | |
| embedding | vector(384) null, HNSW cosine | |
| raw | jsonb null | |

Also btree on `source`.

### news_clusters
`headline text`, `first_seen timestamptz` (btree), `last_seen timestamptz`, `item_count int default 0`,
`max_importance smallint default 1` (CHECK 1..5), `instruments text[] NOT NULL default '{}'` (GIN).

### economic_events — UNIQUE (source, external_id)
`source varchar(64)`, `external_id varchar(191)`, `country varchar(16)`, `name varchar(255)`,
`importance smallint default 1` (CHECK 1..5), `scheduled_at timestamptz` (btree),
`actual`/`forecast`/`previous` varchar(64) null (kept as published, e.g. "3.2%", "245K"),
`surprise numeric(38,18) null`.

### earnings_events — UNIQUE (symbol, date)
`symbol varchar(32)`, `date date` (btree), `time varchar(16) null` (Finnhub hour: bmo/amc/dmh),
`eps_est`, `eps_act`, `rev_est`, `rev_act` numeric(38,18) null.

### market_sentiment — UNIQUE (index_key, ts)
`index_key varchar(64)` (suggest `crypto_fng` for Alternative.me), `value numeric(38,18)`,
`classification varchar(64) null`, `ts timestamptz`.

### instrument_aliases — UNIQUE (alias_normalized, target)
`alias varchar(191)`, `alias_normalized varchar(191)`, `target_kind enum instrument_family|asset_class`,
`target varchar(64)`, `priority smallint default 0`. No column says whether a row was generated
from instruments or curated from `config/news_aliases.yaml`; the alias builder can replace the
table wholesale.

## Source keys (seeded)

| key | kind | category | poll_seconds | requires_key |
|---|---|---|---|---|
| okx.announcements | api | exchange | 120 | no |
| cryptopanic | api | crypto_news | 180 | yes |
| rss.coindesk | rss | crypto_news | 180 | no |
| rss.cointelegraph | rss | crypto_news | 180 | no |
| rss.theblock | rss | crypto_news | 300 | no |
| rss.decrypt | rss | crypto_news | 300 | no |
| rss.cnbc | rss | markets_news | 180 | no |
| rss.marketwatch | rss | markets_news | 180 | no |
| finnhub.news | api | markets_news | 180 | yes |
| finnhub.calendar | calendar | calendar | 900 | yes |
| finnhub.earnings | earnings | earnings | 86400 | yes |
| forexfactory.calendar | calendar | calendar | 900 | no |
| alternative_me.fng | sentiment | sentiment | 3600 | no |

URLs are in `config/defaults.yaml`. They were **not** checked against the live feeds (no network
in the build sandbox) — verify when the collectors are recorded on a networked run. Not seeded:
Reuters/AP (no free RSS), Yahoo per-symbol RSS and project blogs (per-symbol, better generated by
the collector), Trading Economics, FRED, Reddit/X.

All sources seed `enabled = true`; whether Finnhub's calendar replaces ForexFactory when keyed is
the collector's decision.
