# ATL-92 — Eval harness (`atlas_agents.evals`)

Brief §7.11 and §9, decision #245. Code: `services/agents/src/atlas_agents/evals/`.
Cases: `services/agents/evals/<agent>/<case_id>/`. Tests: `services/agents/tests/test_evals.py`.

## The two-minute version

- **A case = `case.yaml` + `data/<table>.json`.** Each data file is a JSON list of rows for a table
  known to `atlas_shared.db` (`candles`, `funding_rates`, `news_items`, `economic_events`, ...).
  Timestamps are ISO strings with a zone; decimals as strings.
- **Each case runs in one transaction on the checkout's test DB that is always rolled back.** The
  snapshot, the agent_runs row, usage and state writes all disappear afterwards. Run
  `cd backend && php artisan test` first so the DB is migrated.
- **The agent is built through `Supervisor(replay_as_of=...)`**, so it gets the same §7.10 options,
  prompt renderer and observers as production, and every tool is bounded by the cutoff. The
  publisher is a recorder, so an eval never puts agent states on the live bus.
- **`--fake` replays the case's `fake.steps`, but tool steps run the real tool handlers** against
  the snapshot at `replay_as_of`. That is what lets a pytest prove replay isolation. A tool step
  outside the agent's allow-list gets "not allowed" and is not run, as the CLI would do.
- **Without `--fake` the real CLI login is used.** pytest never does this: `agents/tests/conftest.py`
  has an autouse fixture that makes `ClaudeSDKClient(...)` raise. The one exception is
  `test_env.py`'s stand-in-CLI test, which is marked `@pytest.mark.stand_in_cli`.

## Commands

```
uv run python -m atlas_agents.evals run --agent historian [--case ID] [--fake] [--label prompt-v3]
    [--cases-dir DIR] [--results-dir DIR] [--baseline FILE]
uv run python -m atlas_agents.evals compare A.json B.json
```

`run` prints `PASS|FAIL  <case>  <rate>% (p/t)  [status]`, with each failed property and its detail,
then the `OVERALL` line and the baseline check. It writes
`services/agents/evals/results/<agent>/<UTC stamp>.json`, which is gitignored. Exit codes:

- 0: ok.
- 1: the overall rate is more than `agents.evals.max_drop_points` (default "5") below
  `evals/<agent>/baseline.json`. Exactly 5 points below still exits 0.
- 2: no cases were found.

A missing baseline passes. `compare` prints A/B per case, with `n/a` for a case found in only one file,
and an `OVERALL` row.

## case.yaml

```yaml
agent: historian
replay_as_of: "2026-10-20T08:00:00Z"
env: demo                      # optional
description: one line          # optional
prompt: "@Historian ..."
properties:
  valid_schema: { model: HistoryVerdict }          # name in properties.MODELS, or pkg.module:Class
  no_invented_numbers: {}                           # { guard: auto|numguard|stub } optional
  verdict_in: { values: [supports, weakly_supports] }   # field: verdict by default
  no_trade: {}                                      # { expected: false } asserts a trade instead
  mentions_n: { min: 20 }                           # or equals / max
fake:
  steps:
    - tool: okx_market.get_candles                  # or the full mcp__server__name
      input: { inst_id: BTC-USDT-SWAP, bar: 1H, limit: 3 }
    - text: "..."
    - tool: warroom.post_message
      input: { kind: research_report, content_md: "...", card: { ... } }
    - result: { tokens_in: 4200, tokens_out: 380, turns: 5 }   # optional; added if missing
```

## What gets scored

`Transcript` (in `properties.py`) is built from `RunResult.messages`:

- `text`: every assistant TextBlock plus the `content_md` of any post_message/reply call.
- `card`: the last `card` passed to post_message/reply. Failing that, the last fenced ```json block in
  the text.
- `tool_calls`: (name, input) pairs.
- `tool_outputs`: every ToolResultBlock parsed to its `{as_of, source, data}` envelope.

If the run's status is not `ok`, every property fails with the status. An unknown property, or one
that raises, fails that property only. A case that raises (bad snapshot, unreadable yaml) is a
failed case with status `error` or `invalid`, and the run continues with the next case.

`no_invented_numbers` goes through `evals/guard.py`. With `resolve("auto")` it uses
`atlas_agents.numguard.check(...).to_meta()` when that module exists, and the built-in stub
otherwise. The example cases pass under both; I checked them against ATL-91's guard.

## Results JSON

```json
{"agent": "historian", "mode": "fake", "label": "prompt-v3", "started_at": "...", "finished_at": "...",
 "cases": [{"case_id": "btc_stats_1h", "status": "ok", "error": null, "passed": 5, "total": 5,
            "pass_rate": 100.0, "properties": [{"name": "valid_schema", "passed": true, "detail": "..."}],
            "tool_calls": 4, "tokens_in": 4200, "tokens_out": 380, "turns": 5}],
 "overall": {"passed": 15, "total": 15, "pass_rate": 100.0},
 "baseline": {"path": "...", "pass_rate": 100.0, "max_drop_points": 5.0, "ok": true}}
```

Rates are percents, micro-averaged over properties. A baseline file is any results file. The
committed `evals/historian/baseline.json` is the fake run of the 3 examples at 100%.

## For ATL-93 / ATL-94

- Add the 30 real cases next to the 3 examples (`btc_stats_1h`, `btc_regime_1h`, `doge_no_data`).
  `test_the_harness_ships_three_example_historian_cases` pins that list, so update it.
- Re-baseline after a real CLI run: `cp evals/results/historian/<stamp>.json evals/historian/baseline.json`.
- The isolation test checks the example snapshots' rows with `ts` > replay_as_of. Those rows carry
  candle prices 99999.5 and funding 0.0777, so do not reuse those values in real data.
- A new card model for `valid_schema`: add it to `properties.MODELS`.
