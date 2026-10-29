# ATL-62 — Entity linking through `instrument_aliases`

For ATL-65 (enrich every ingested item). Brief §7.2 step 3, decision #243.

## Score

`uv run --package atlas-ingest python -m atlas_ingest.evaluation report` (from `services/`):

```
linking: precision=1.0000 recall=0.9922 f1=0.9961 tp=256 fp=0 fn=2 (target precision>=0.95: PASS)
```

The linker is registered through the `linker` entry point in `services/ingest/pyproject.toml`
(`atlas_ingest.evaluation.linking_stage:link_headline`). `ingest/tests/test_linking.py` fails below
precision 0.95 or recall 0.85. Both misses are h031 ("Crypto bridge loses $48 million…"), which names
neither chain it is labelled with.

## Entry points

```python
from atlas_ingest.linking import link, set_default_linker, LinkResult
r = link("Oil and gold rise", "Spot gold rose 1.2%.")
r.instruments    # ('CL-USDT', 'XAU-USDT')   canonical instrument families in the active env
r.asset_classes  # ('commodity',)            classes of the linked instruments + asset-class aliases
r.entities       # (Entity(text='Oil', alias='oil', target_kind='instrument_family', target='CL-USDT',
                 #         field='title', start=0, end=3), ...)
```

- `link(title, summary="")` uses the process-wide default linker. If none is installed, the first call
  loads one from `instrument_aliases` + `instruments` of the active env (`ATLAS_ENV` →
  `ATLAS_DEFAULT_ENV` → `defaults.yaml` `env.default`) via `aliases.load_linker(engine, env)`.
- **The pipeline should build the linker itself** after each alias sync and install it with
  `set_default_linker(aliases.load_linker(engine, env))`, so a resync is picked up.
- `news_items.instruments` ← `r.instruments`, `news_items.asset_classes` ← `r.asset_classes`,
  `news_items.entities` ← the entities as JSON (offsets refer to the normalised field text).
- "Fed cuts rates" links no instrument, but `asset_classes` gets `[commodity, crypto, etf, index, stock]`.
  Decide in ATL-65 whether that should count towards importance; the fixture does not score classes.

## Targets: canonical family form

A target is the instrument family, one per base symbol per env: `BASE-USDT` if listed, else
`BASE-USDC`, else the first family alphabetically. So on demo `Apple` → `AAPL-USDC` if only the USDC
perp exists. To find tradeable instIds, match `instruments.inst_family` (or `base-quote` for spot).

## Alias sync

```
cd services
uv run --package atlas-ingest python -m atlas_ingest.aliases sync [--env demo]
# instrument_aliases: <n> rows for env demo
uv run --package atlas-ingest python -m atlas_ingest.aliases watch [--interval 60]
```

- `instrument_aliases` has no env column, so `sync` replaces the whole table with the active env's
  aliases in one transaction. Running it twice leaves the same row count.
- `aliases.AliasWatcher(engine, env).poll()` re-syncs when the env's instrument count changes (first
  poll always syncs) and returns the rows written, or `None`. **ATL-65's ingest loop should call it**
  (e.g. every minute) and reinstall the default linker when it returns a number. `watch` is the same
  loop as a standalone command.
- Rows: curated aliases (priority 10), `BASE-USDT/USDC/USD` family forms (5), bare ticker (0).
  Tickers of ≤ 2 letters not curated, and the `ticker_stoplist` in the YAML (`AI`, `GAS`, `ONE`…), are
  stored as `$TICKER` and match only as cashtags.
- The linker also ignores rows whose family is not listed in the env today, so a stale table never
  links a delisted or other-env instrument.

## Matching rules (written in the alias text, so DB rows carry them)

| alias text | match |
| --- | --- |
| lowercase / caseless script (`bitcoin`, `الذهب`) | case-insensitive whole word; `ال…` aliases take up to two و ف ب ل ك prefixes |
| any uppercase (`Apple`, `SOL`) | exact case or ALL CAPS, whole word; `-` + uppercase/digit blocks (`OP-ED`); tickers also as `$cashtag` in any case |
| `$ONE` | cashtag only |
| `биткоин*` | word prefix |
| CJK / kana / hangul only (`比特币`) | substring |

Text is NFKC-normalised; Arabic diacritics/tatweel removed and alef forms folded. Overlaps: longest span
wins ("Bitcoin Cash" → BCH only). Context rules in `config/news_aliases.yaml` drop hard negatives
(`unless`: gold + medal, Amazon + rainforest, Avalanche + skiers…) or weak names (`require_any`:
"Strategy" needs bitcoin/shares/…). Config is read from `ATLAS_CONFIG_DIR` if set, else the repo's
`config/`.

## Not built

No translation (patch E); per-instrument sentiment is ATL-63's. Curating more aliases is a YAML edit plus
`aliases sync`; add a fixture row when you add a tricky one.
