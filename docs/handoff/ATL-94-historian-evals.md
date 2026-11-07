# ATL-94 — Thirty Historian replay eval cases

Cases: `services/agents/evals/historian/<case_id>/`. Generator: `services/agents/evals/historian/build_cases.py`.
Tests: `services/agents/tests/test_historian_evals.py`. Builds on `ATL-92-eval-harness.md`.

## The two-minute version

- **30 cases + ATL-92's 3 examples.** The id prefix is the category: `analog_` (8), `event_` (5: cpi, fomc,
  earnings, token_unlock, listing), `backtest_` (5, of which `backtest_overfit_*` are 2 deliberately
  overfit rules), `small_` (4, n < 20), `gap_` (3, backtests on unrepaired gaps), `stopnoise_` (3),
  `regime_table_` (2, 07:30 mornings). `test_historian_evals.py` counts them per prefix.
- **One shared snapshot.** Every case's `data/<table>.json` is a relative symlink into `_pool/`
  (candles 4H/1D/1H, funding, instruments, economic_events, earnings_events, ~3 MB). Cases differ
  by `replay_as_of`; the tools bound everything by it. The dev DB has no history and this machine
  has no route to OKX, so the pool is seeded, real-shaped data (OKX columns, tick-rounded prices,
  8-hourly funding, ForexFactory CPI m/m + y/y rows and a EUR CPI decoy, Finnhub-style earnings).
  DOGE/AVAX/LINK carry interior holes; HYPE has 25 days of history.
- **Fake answers are generated, not typed.** `build_cases.py` runs each case's tool steps through
  the real tools on the test DB, then writes the answer text and the HistoryVerdict card from those
  outputs. Change a tool or the pool → re-run it:
  `cd services && uv run python agents/evals/historian/build_cases.py` (needs `php artisan test` first).
- **Prompts are production prompts**: `historian.mention_prompt(...)` around the question, and for
  the regime mornings `historian.regime_prompt(build_regime_table(...))` plus a short eval suffix
  asking the agent to echo the table as a fenced RegimeTable block (in production the code posts the
  table, so there is no card in the agent's own text otherwise).

## Verdict bounds

- small-sample: `verdict_in: [weakly_supports, does_not_support]`.
- gapped-data: `verdict_in: [does_not_support]`.
- overfit backtests: `[does_not_support, contradicts]`; their fake card has `overfit_warning: true`.
- token_unlock / listing: the tool refuses the event type, so `[does_not_support]` with n = 0.
- data-driven cases (analogs, stop-noise): the computed verdict and one step weaker (`NEAR` in the
  generator). The regime table has no verdict, so it asserts valid_schema RegimeTable, the guard,
  no_trade and mentions_n only.

## Harness changes (ATL-92 code)

- `no_invented_numbers: {from_prompt: true}` also counts the case prompt as a source, as production
  does for the mention text and the regime table. Used by the stop-noise (the asker states the stop
  distance) and regime cases only. `Transcript` gained `prompt`; `runner.transcript(result, prompt)`.
- `properties.MODELS` gained `RegimeTable`.
- `test_evals.py`'s example tests now run on a temp copy of the 3 examples.

## Baseline

`baseline.json` is a **real CLI run** (owner's logged-in `claude`, 2026-10-30), merged from two
consecutive batches because a full pass (~22 min at ~40 s per case) is too close to the 25-minute
command limit: 93.25% overall (152/163), 97.97% (145/148) on the 30 new cases. Raw batch results and
logs are in `evidence/ATL-94/`. Real-mode failures worth knowing:

- `gap_avax_ema_backtest`: the raw answer said `contradicts` on gapped data. In production the
  VerdictReviewer's `backtest_on_gapped_data` rule corrects it; the eval scores the raw answer.
- `event_cpi_crypto`: the card put a non-number in `distribution.4h.pct_positive`.
- `analog_btc_after_selloff`: answered `does_not_support` where the bound is supports/weakly.
- The 3 ATL-92 examples send the bare question (no mention_prompt), so the real agent writes no card.
