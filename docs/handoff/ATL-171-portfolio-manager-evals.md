# ATL-171 — Portfolio Manager replay evals

Brief §7.8 (scoring: rule compliance must be 100 %) and §7.11. Built on the ATL-92 harness, ATL-168's
rules (`docs/handoff/ATL-168-pm-rules.md`), ATL-169's checks (`docs/handoff/ATL-169-portfolio-manager.md`)
and ATL-170's daily report. Cases: `services/agents/evals/portfolio_manager/`. Tests:
`services/agents/tests/test_portfolio_manager_evals.py`.

## The two-minute version

- **35 cases in 12 categories**, named by case-id prefix:
  - one per management rule, 2–3 cases each: `breakeven_`, `tp1_` (partial + trail, TP resting on
    OKX → trail only, TP1 already filled → trail), `tworr_`, `timestop_`, `invalid_`, `event_`,
    `funding_` (advisory: the model runs, nothing is submitted);
  - `silent_` (6): nothing fires (quiet, stop already at breakeven, external position, an amend
    already pending, a stale "broken" structure report) → no model call, no message, nothing saved;
  - `nowiden_` (3): a rule's stop would be looser than the stop on OKX, so code drops it;
  - `noadd_` (3): a losing position the model is asked about (pre-event runs, an event reduce);
  - `news_` (4): a news alert on a held instrument, RUMOUR / CONFIRMED / REPORTED, one while
    breakeven fires;
  - `report_` (3): the 23:30 daily report (quiet day, two trades, funding only).
- Check cases assert `rules_fired` (pins what the rules must compute), `rule_compliance`,
  `never_widens_stop`, `never_adds`; silent ones add `silent`, news ones `news_labelled {expect}`,
  report ones `report_figures_untouched` + `no_invented_numbers`. All properties are documented
  at the top of `atlas_agents/evals/properties_pm.py`.
- **PM code has its own modules**: `evals/properties_pm.py` and `evals/drivers_pm.py`. On import they
  add themselves to `properties.PROPERTIES` and `drivers.DRIVERS`; `runner.py` imports both. Another
  agent's evals should follow the same pattern rather than append to the shared files.
- **Everything is generated** by `evals/portfolio_manager/build_cases.py`. Rebuild with
  `cd services && uv run --package atlas-agents python agents/evals/portfolio_manager/build_cases.py`
  (no DB needed). The builder computes the expected actions with the production
  `rules.evaluate_full` on the same inputs (30 flat 1H candles, so ATR = the bar range and there is
  no confirmed swing) and exits if a scenario's `rules` do not match what the rules fire.
- Replay time is 2087-05-13 09:00 UTC (report cases 20:30 UTC = 23:30 Baghdad), far from every
  other test's data, because economic events and bills are not keyed by instrument.
- `baseline.json` is the `--fake` run: 100 %, 149/149.
- `uv run --package atlas-agents python -m atlas_agents.evals run --agent portfolio_manager --dry-run`
  lists all 35 cases without the DB or the CLI.

## The driver (`drivers_pm.py` → `portfolio_manager_driver`)

`inputs.mode`:

- `check` (default): one `manage.tick()` with a fake risk client (`_EvalRisk`: stores an approved
  risk check, no network). With `inputs.structure: {status, age_minutes}` the case supplies the TA
  structure report production cannot load yet (ATL-168's gap) and each position with an action
  goes through `manage.manage()` directly.
- `alert`: `inputs.alert` (a news.alert payload) goes to `hooks.on_news_alert`, then
  `manage.settled()`.
- `report`: `daily_report.run()` for `inputs.day`.

`Transcript.meta`: `computed` (kind, rule label, stop, close_pct), `fired`, `dropped`,
`model_blocks` (every JSON block the model wrote, one per attempt), `refusals`, `code_submitted`,
`proposals` (PM proposals saved by the run), `messages`, `positions` (side, current stop, mark).
Report mode: `code_card`, `model_blocks`, `posted_meta`.

## Harness change: scripted follow-ups

`fake.follow_ups: [[steps…], …]` in `case.yaml` answers the reviewer's follow-up prompts in order
(`EvalFakeClient` takes them; a follow-up the script does not answer still fails as before). The
planted tests use it so a model that repeats a refused answer completes its run and is scored on
the property, not on "run error".

## Planted answers (pytest)

A widened stop (58500 under a 59000 stop) fails `never_widens_stop` and `rule_compliance`; an entry
submitted on a −0.7R long fails `never_adds`; a close on a headline with no label fails
`news_labelled`; a news text without the label fails it too; a report block restating `equity`
fails `report_figures_untouched` and its invented number fails `no_invented_numbers`.

## Not built

No real-CLI baseline (it costs subscription usage). The invalidation cases feed the structure report
from the case until the TechnicalSetup card carries intact/weakening/broken.
