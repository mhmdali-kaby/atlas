# ATL-209: the Backtester form and run page (for ATL-210, the results)

Code: `frontend/src/features/backtester/`. The API is ATL-208's `/api/backtests` (see
`docs/handoff/ATL-208-backtests-api.md`).

## Routes and area

- A new shell area, `backtester` (`AreaId`, `AREAS` in `src/app/feature.ts`, label `shell:area.backtester`,
  icon FlaskConical), between Journal and Settings. The design puts it there.
- `/backtester` is the form (`BacktesterPage` → `BacktestForm`). `/backtester?from=<run id>` fills the form
  from that run's `request` ("Edit and run again").
- `/backtester/run?id=<run id>` is one run (`RunPage`).
- The feature has **no `nav` entry**. The sidebar is one custom section (`RunsSidebar`): the "New backtest"
  link, then "Recent runs". Without a nav entry the toolbar title reads "Backtester" on every page, and only
  the open run is marked current.

## Where the results go (ATL-210)

`RunPage.tsx` renders, in order: `Header` (rule name, `StatusChip`, plus "Edit and run again" when the run is
done), `<Sentence form={fromRequest(run.request)} compact />`, then one of three things: `Running` when the run
is queued or running, `Failed` when it failed, and for `done` nothing yet:

```tsx
{/* run.status === 'done': ATL-210 renders run.result here. */}
```

Put the results component in that slot. `run` is a `RunDetail` (`api.ts`): every list field plus `request`
(a `BacktestRequest`) and `result`, typed `Record<string, unknown> | null` and exactly trading_core's body.
Type `result` properly in your task. The design's result header ("Ran {when}, on {env} candles…", fees) sits
in the header area. Move that into `Header` if you need to; nothing else uses `Header`.

## Polling

- `useRun(env, id)` polls every `POLL_MS` (2000) while the status is `queued` or `running`. It stops at
  `done` or `failed`, and also stops on an error. When a run finishes it invalidates the list.
- `useRuns(env)` (the sidebar) polls only while some row is still active.
- When a run the owner is watching goes from active to `done`, it raises a toast (`backtester:doneToast`).
- Query keys: `['backtests', env]` for the list and `['backtests', env, id]` for one run.

## The form model (`model.ts`)

- `FormState` holds text drafts. `toRequest(form)` gives the exact POST body. Period bounds are
  `YYYY-MM-DDT00:00:00+03:00` (00:00 Baghdad on the chosen days). `fromRequest(request)` reverses it:
  the API returns UTC `Z` times, and they are mapped back to the Baghdad day.
- `validate(form)` mirrors StoreBacktestRequest. `serverFieldKey(path, form)` maps a 422 path to a field:
  `rule_spec.entry.0.right` → `entry.0`, `rule_spec.indicators.<name>.*` → that indicator's row,
  `period.*` → `period`, `inst_ids.*` → `insts`. A path with no field goes into the banner.
- `LIMITS.maxBars` (50,000, from `backtest.max_bars`) is only a hint. trading_core still decides
  `too_many_bars`.

## Reusable pieces

`Sentence` (the rule as tokens), `StatusChip`, `Fig` and `FieldError` are in `parts.tsx`; `fill`, `dayText`,
`pctText` and `useIndicatorLabel` are in `text.tsx`. `backtester.css` carries `.bt-panel`, `.bt-status`,
`.bt-tok`, `.bt-state-icon` and the design’s form classes. The results classes (`.bt-tiles`, `.bt-curve`,
`.bt-pair`, `.bt-table`…) from the design's `css/backtester.css` are **not** ported. Add them.

Tests: `Backtester.test.tsx` (`fakeBackend` with `detail: RunDetail[]` served in order, `runOf(status)`),
`e2e/backtester.spec.ts` (fake Laravel with the running state).
