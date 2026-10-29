# ATL-63 — rule-based event type, sentiment and importance

Module: `services/ingest/src/atlas_ingest/classify.py`. Pure functions, no DB, no network, no LLM.

## Entry points

```python
from decimal import Decimal
from atlas_ingest.classify import event_type, sentiment, headline_sentiment, importance

event_type("Exchange X hacked for $40M", "")          # -> "hack_exploit"
sentiment("Solana surges", "", ["SOL-USDT", "SOL-USDT-SWAP"])
# -> {"SOL-USDT": Decimal("0.707"), "SOL-USDT-SWAP": Decimal("0.707")}
headline_sentiment("Bitcoin does not fall")           # -> Decimal("0.351")
importance("hack_exploit", "Reuters", 1, Decimal("0.2"), None)  # -> 5
```

- `event_type(title, summary) -> str` — one of the 17 brief types (`EVENT_TYPES` in
  `atlas_ingest.evaluation.fixture`). Rules in `config/news_event_rules.yaml` are tried in order on
  the **title**; first match wins. Only if no rule matches the title are they tried on the summary.
  Nothing → `other`.
- `sentiment(title, summary, instruments) -> dict[str, Decimal]` — headline-level lexicon score
  (`config/news_sentiment_lexicon.yaml`), the same value for each instrument (deduplicated, order
  kept), in -1..1, quantized to 0.001. Empty instruments → `{}`. Non-English text scores 0 (lexicon
  is English; translation is patch E). Store it in `news_items.sentiment`/`entities` as you see fit.
- `importance(event_type, source, linked_count, sentiment_abs, novelty) -> int` — 1..5. Weights in
  `config/defaults.yaml` → `news.importance` (formula in the comment above it; read at runtime from
  `atlas_shared.defaults.DEFAULTS`). `source` matches `tier1_sources` case-insensitively by display
  name or source key (`Reuters`, `okx.announcements`). `novelty=None` (not computed yet) adds nothing;
  NaN/inf also count as unknown. Pass `abs(max sentiment)` for a multi-instrument item.

## Config location

Found by walking up from the module to the first `config/news_event_rules.yaml`; set
`ATLAS_CONFIG_DIR` to override (e.g. a packaged install). Rules and lexicon are cached per process
(`functools.cache`); restart ingest after editing them.

## Evaluation

Registered as the `classifier` entry point of `atlas_ingest.evaluation`. On the 300-headline fixture
the report shows 300/300; that is in-sample (the rules were written against it), so
`tests/test_classify.py::test_unseen_headlines` holds 16 headlines that are not in the fixture.
`test_fixture_event_type_accuracy_meets_target` fails below 0.85.

Forecast headlines (TL's ruling): "raises annual forecast" in a results headline → `earnings`
(h287, h299); "forecasts X revenue", "guidance", "outlook", "guides Q4 sales" → `guidance` (h143, h300).

## Not built

No per-instrument sentiment (every linked instrument gets the headline score), no translation, no
novelty computation (that is the embedding/clustering task's job; pass it in when you have it).
