# ATL-201 — Agents → Scorecards and the reusable scorecard card

Route `/agents/scorecards` (Agents area nav: **Status** `/agents`, **Scorecards**). Reads ATL-200's
`GET /api/agents/scores?days=7|30|90` only. Code: `frontend/src/features/agents/scorecards/`.

## What ATL-204 (Portfolio → Scorecards) reuses

```ts
import { ScorecardsView, ScorecardCard, SC_NS } from '../agents/scorecards'

// The whole page body, Portfolio variant: no weights panel, P&L line on each card, pfIntro text.
<ScorecardsView portfolio />
```

`ScorecardsView` owns the period state (default 30), the query (`useAgentScores(env, days)`, key
`['agents','scores',env,days]`, previous period kept on screen dimmed while the next loads), the
loading / error / no-scores states (period control hidden in the last two) and a `Tooltip.Provider`.

Single card, if you need it on its own (put a radix `Tooltip.Provider` above it):

```tsx
<ScorecardCard slug="strategist" scores={agentEntry} days={30} from={meta.from} to={scoredThroughDay} weight={null} pnl />
```

- `scores`: the agent's element of `data[]` from the API (`undefined` = no rows).
- `to`: pass the newest scored day, not `meta.to` — today is never scored, so a trend ending at
  today always has a blank last point. `ScorecardsView` does this for you.
- `pnl`: shows "P&L of trades it led" with a `SignedValue` **only when** the agent entry has
  `pnl_attributed_usd` (string decimal) that is not null. ATL-200 does not send that field yet;
  ATL-204 adds it to the API (it is typed as optional on `AgentScores`).

## How figures are worked out (`model.ts`)

- Every metric is a `MetricSpec` (data key, its own sample key, hits key, minimum n, target or
  reference, better up/down), per the design's "What each card shows" table and the scorers' keys in
  the ATL-110/195/196/197 handoffs. Window value = Σ(value × n)/Σn over days with a value and n>0;
  hits sum; counts (alerts, loops) sum. Brier uses `data.brier_score` (falls back to the row column)
  weighted by `brier_n`, with `mean_p`/`observed` weighted the same way for the calibration pair.
- Under the minimum: "Not enough data" + `n = 3, needs 10`, aria-label with both. No value is shown.
- Weights: the newest day carrying `data.reliability_weight` (with `reliability_n`,
  `reliability_correct`); under 10 votes it is drawn as the outlined grey default. Rows that only
  carry `reliability*` keys (ATL-198's n=0 rows) are not counted as scored days.
- "Scored through" = newest scored day across agents; ≥ 4 days behind today shows the stale notice.

Strings: `src/i18n/locales/{en,ar}/scorecards.json` (`pfIntro` and `pnl` are already there for you).
Tests: `ScorecardsPage.test.tsx` (vitest) and `e2e/agents-scorecards.spec.ts`; fixture responses in
`scorecards/fixtures.ts` (`scoresFixture(days, { history, empty })`) are reusable for your tests.
