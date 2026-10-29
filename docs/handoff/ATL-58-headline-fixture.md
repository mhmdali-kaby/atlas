# ATL-58 — 300 labelled headlines and the enrichment scorer

For whoever builds entity linking, event typing and clustering in patch C (brief §7.2 quality tests:
linking precision ≥ 95%, event type ≥ 85%, duplicate clustering ≥ 90% F1).

## Files

- `services/ingest/tests/fixtures/headlines_300.jsonl` — 300 rows, one JSON object per line, sorted by
  `published_at`, ids `h001`…`h300`.
- `services/ingest/src/atlas_ingest/evaluation/` — no DB, no network:
  - `fixture.py` — `HeadlineRow` (pydantic, `extra="forbid"`), `EVENT_TYPES`, `INSTRUMENT_ASSET_CLASS`
    (the label vocabulary), `load_fixture(path=None)`, `gold_linking/gold_event_types/gold_clusters(rows)`.
  - `scoring.py` — `score_linking`, `score_event_type`, `score_clustering` (pure).
  - `registry.py` — where your stage plugs in.
  - `__main__.py` — the `report` CLI.

## Row schema (real row)

```json
{"id": "h146", "source": "Reuters", "published_at": "2026-10-16T05:10:00Z",
 "title": "Tanker seized near Strait of Hormuz, Brent jumps 4%", "summary": "", "language": "en",
 "category": "commodity", "expected_instruments": ["CL-USDT"], "expected_asset_classes": ["commodity"],
 "expected_event_type": "geopolitics", "cluster_label": "c-hormuz",
 "notes": "Brent maps to CL, the only oil perp"}
```

| field | type | rule |
| --- | --- | --- |
| `published_at` | ISO 8601, UTC only | `Z`; any other offset fails validation |
| `language` | ISO 639-1 | 32 rows are non-English: ar, zh, ja, ko, de, es, fr, ru, tr |
| `category` | `crypto` `equity` `commodity` `macro` `negative` | coverage slice only (not a model output). Added beyond the task's field list so the coverage counts are explicit |
| `expected_instruments` | list of `BASE-USDT` | OKX instFamily/base form, never `-SWAP`; must be in `INSTRUMENT_ASSET_CLASS`. A USDC-margined perp (`WLD-USDC-SWAP`) is still labelled `WLD-USDT` |
| `expected_asset_classes` | list | exactly the sorted set of classes of `expected_instruments` (validator enforces it). Empty when no instrument is linked — macro headlines with no named instrument have `[]` |
| `expected_event_type` | one of the 17 brief types | `earnings guidance macro_release central_bank regulation hack_exploit listing delisting ETF_flow M&A lawsuit product partnership token_unlock geopolitics supply_shock other` (exact spelling, incl. `ETF_flow`, `M&A`) |
| `cluster_label` | string | same story across sources share a `c-…` label; singletons are `s-<id>` |
| `notes` | string | why a tricky label is what it is; hard negatives start with `hard negative:` |

Counts: crypto 127, equity 71, commodity 42, macro 43, negative 17; 69 clusters of 2+ rows (each has ≥ 2
sources and spans ≤ 12 h, matching the §7.2 clustering window); every event type ≥ 6 rows.

## Label rules (what your linker/classifier should reproduce)

- **Link only what the text names**, by ticker, name or an unambiguous alias — never by inferred market
  impact. "Fed cuts rates" links nothing; "Gold rises after Fed cut" links `XAU-USDT`.
- Aliases used: bitcoin/BTC → BTC; ether/Ethereum → ETH; Ethereum Classic → ETC (not ETH); Bitcoin Cash →
  BCH (not BTC); Ripple (the company) → XRP; Polygon → POL; Strategy/MicroStrategy → MSTR; Alphabet/Google
  → GOOGL; TSMC → TSM; Eli Lilly → LLY; Circle → CRCL; Robinhood → HOOD; Coinbase → COIN; gold/bullion →
  XAU; silver → XAG; oil/crude/WTI/Brent → CL; natural gas → NG; copper → XCU; S&P 500 → SPY;
  Nasdaq 100 → QQQ + US100; Russell 2000 → IWM. TQQQ/SQQQ only when named.
- **Never linked:** stablecoins (USDT, USDC), Tether, Binance/Telegram alone, "Wall Street", the dollar,
  Treasury yields.
- A company that is only the venue still links when named (Coinbase listing WLD → `WLD-USDT`, `COIN-USDT`).
- Hard negatives (17): apple harvest, gold medal (en + ar), Amazon rainforest, meta-analysis, ripple effect,
  Golden State, snow avalanche, Shiba Inu the dog, oil painting, Silver Alert, Tesla coil, copper cable theft,
  Pepe the Frog, Solana Beach, silver lining, "tons of grain". All link nothing and are typed `other`.
- Event typing: scheduled data (CPI, NFP, EIA inventories, gas storage, GDP) → `macro_release`; OPEC+ output
  decisions and unscheduled disruptions → `supply_shock`; wars, strikes, tariffs, shutdowns → `geopolitics`;
  central-bank gold buying → `central_bank`; price moves, records, treasury purchases (Strategy buying BTC),
  deliveries → `other`. Within one cluster the type can differ (Palantir earnings vs guidance rows).

## Scorer semantics

Inputs are dicts keyed by row id. Only gold ids are scored; prediction ids not in gold are ignored.

- `score_linking(pred: Mapping[id, Iterable[str]], gold)` → `LinkingScore(precision, recall, f1, tp, fp, fn)`,
  micro over (id, instrument) pairs. A missing id = empty prediction. An empty denominator scores 1.0
  (nothing claimed, nothing wrong).
- `score_event_type(pred: Mapping[id, str], gold)` → `EventTypeScore(accuracy, correct, n,
  confusion[gold][pred])`; a missing id counts as `"<missing>"`.
- `score_clustering(pred: Mapping[id, label], gold)` → pairwise P/R/F1 over same-cluster pairs, plus
  B-cubed P/R/F1. Labels are opaque. A missing id becomes its own singleton.

## Plugging a stage in

Stage signatures (`registry.py`): `Linker = (HeadlineRow) -> Iterable[str]`,
`Classifier = (HeadlineRow) -> str`, `Clusterer = (Sequence[HeadlineRow]) -> Mapping[id, label]`.
The clusterer gets all 300 rows at once (sorted by time); use `title`, `summary`, `published_at`, `source`.

Preferred: an entry point, so the report finds it without edits here. In `services/ingest/pyproject.toml`:

```toml
[project.entry-points."atlas_ingest.evaluation"]
linker = "atlas_ingest.enrich.linking:link_headline"
```

(names `linker`, `classifier`, `clusterer`; `uv run` re-syncs the install). Or call
`register_linker(fn)` etc. yourself, or pass `--linker module:attr` on the CLI.

## Running

```
cd services
uv run --package atlas-ingest python -m atlas_ingest.evaluation report [--confusion] [--fixture PATH]
```

Today it prints:

```
fixture: 300 rows
linking: not registered
event_type: not registered
clustering: not registered
```

With stages registered each line shows the scores to 4 decimals plus `PASS` / `BELOW TARGET` against the
§7.2 target. The CLI exits 0 either way; if you want CI to gate on targets, add an assertion test in your
task. Tests: `uv run pytest -q ingest/tests`.

## Not built here

No fake embedder, no latency measurement (ingest-to-alert < 90 s belongs to the pipeline task), no
translation. Headlines are invented in newswire style, not copied; dates are October–early November 2026.
