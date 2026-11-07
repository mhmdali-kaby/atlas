# ATL-91 — Numeric hallucination guard (`atlas_agents.numguard`)

Brief §7.11. Pure module, no I/O: `services/agents/src/atlas_agents/numguard.py`.
Tests: `services/agents/tests/test_numguard.py`.

## The two-minute version

- Call `check(text, card, tool_outputs)` once per agent message, with **that turn's** tool results
  (the ATL-84 envelopes `{as_of, source, truncated, data}` exactly as the model saw them — error
  results too). Attach `result.to_meta()` to the message (ATL-88 warroom tools / ATL-93 Historian).
- It never blocks or rewrites a message. It only reports. Count `len(result.unmatched)` against the
  agent and let the UI highlight the spans.
- Only **one** derivation step is allowed: `52.5 / 41.83` matches if 52.5 is itself a tool number;
  `(4062.5 - 4010) / 41.83` (a difference then a ratio) does **not**. Tools that want such numbers
  quoted should return them (e.g. `stop_distance`, `stop_distance_atr`).

## API

```python
from atlas_agents.numguard import check, extract_numbers, load_config, GuardResult, StatedNumber

result: GuardResult = check(
    text: str,
    card: Mapping[str, Any] | None,
    tool_outputs: Sequence[Mapping[str, Any]],
    *, config: NumGuardConfig | Mapping | None = None,   # default: agents.numguard
)
result.matched     # tuple[StatedNumber, ...]  (matched_by + tool_value set)
result.unmatched   # tuple[StatedNumber, ...]
result.checked     # int: numbers examined (text + card)
result.ok          # no unmatched numbers
result.to_meta()
```

`StatedNumber`: `raw` (as written, sign and unit included: `"−14.6%"`), `value` (float in the
written units: "4.1%" → 4.1, "$1.2M" → 1200000), `kind` (`number|percent|bps`), `location`
(`text|card`), `span` (char range in the text, or in the card string at `path`; `None` for a numeric
card leaf), `path` (card path like `distribution.24h.p90`, `items[0].stop`), `half_unit`,
`matched_by` (`tool_value|percent_of_ratio|bps_of_ratio|difference|ratio`), `tool_value`.

## `to_meta()` shape

```json
{
  "unmatched_numbers": [
    {"raw": "−14.6%", "value": -14.6, "kind": "percent", "location": "text", "span": [301, 308], "path": null},
    {"raw": "0.0731", "value": 0.0731, "kind": "number", "location": "card", "span": null, "path": "distribution.24h.p90"}
  ],
  "checked": 16
}
```

Suggested key on the persisted message: `card.meta.numguard` or a `numguard` field in the
`warroom.message` payload — the War Room owner (ATL-88/96) decides; the shape above is fixed.

## What counts as a number

Integers, decimals, thousands separators (`4,062.5`), `%`, `bps`/`bp`, `R` (`2.4R`), `x`/`×`
multiples (`1.3x ATR`), `ATR` (`1.4 ATR`), `$`, suffixes `k K M B bn mn`, signs `-`, `−` (U+2212), `+`.

Ignored: ISO dates/timestamps, slash dates, clock times, day-month / month-day dates, standalone years
1970–2100, line-start list markers (`1.`, `2)`, `- 3.`), instrument ids (`BTC-USDT-SWAP`,
`BTC-USD-251226-60000-C`), bar/horizon labels (`1H`, `4H`, `15m`, `24h`, `7d`), indicator
parameters (`ATR(14)`, `MACD(12,26,9)`). Letter-glued numbers (`p10`, `US100`) are not numbers.
Known trade-off: a price like `2026` standing alone is treated as a year.

Tool side: every numeric leaf, every decimal string (`"0.0005"`), and numbers written inside string
leaves (a headline's "3.2%") — with the same ignores.

## Matching

Tolerance per candidate `c`: `max(rel_tolerance × |c|, abs_floor, half_unit if rounding_aware)`.
Percent stated numbers also try `100 × leaf`; bps try `10,000 × leaf`; all kinds try pairwise
differences and ratios of the first `max_pair_leaves` distinct leaves. A positive stated number
matches by magnitude ("fell 2.3%" vs −0.023); a negative one must match the sign.

Config, `agents.numguard` in config/defaults.yaml (regenerate with `php artisan atlas:defaults`):

```yaml
numguard:
  rel_tolerance: "0.005"
  abs_floor: "0.0001"
  rounding_aware: true
  derive_pairs: true
  max_pair_leaves: 200
```

## Not built here

Attaching the result to messages (ATL-88 warroom tools, ATL-93 Historian task), UI highlighting
(ATL-96/97), and scoring unmatched counts into `agent_scores`. With many leaves, pairwise ratios make
a random number likelier to match by chance: the guard catches invented numbers, not every one.
