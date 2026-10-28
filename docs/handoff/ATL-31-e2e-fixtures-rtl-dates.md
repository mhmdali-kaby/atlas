# ATL-31 — Portfolio e2e specs pass again; Arabic dates no longer LTR-isolated

Two independent fixes in `frontend/`, both left over from ATL-19/ATL-20/ATL-23/ATL-24.

## The onboarding-gate fixture shape

`OnboardingGate` (`src/features/onboarding/OnboardingGate.tsx`) sends a signed-in owner from
`/` to `/portfolio` only when **both**:

1. `/api/connections` has a demo OKX row with `last_verified_at !== null` (`isVerified`), and
2. `/api/settings?env=demo` returns a doc where `isOnboarded(doc)` is true — `version > 1`
   **and** `settings.onboarding_completed === 1` (`src/features/onboarding/settings.ts`).

A failed lookup on either (404, network error) lets the owner through rather than trapping
them — so a spec that doesn't mock `/api/settings` at all (e.g. `shell.spec.ts`) still reaches
`/portfolio`, because the unmatched route falls through to the fixture's `404` default and
`settings.isError` short-circuits the redirect.

**The bug this task found was already fixed on develop** (commit `0494aee`, folded into
ATL-19): `portfolio-overview.spec.ts`'s fixture already sets
`settings: { risk_profile: 'balanced', autonomy_level: 2, onboarding_completed: 1 }`. The only
other specs that fake `/api/settings` — `onboarding-steps.spec.ts` and `settings-risk.spec.ts`
— set it deliberately (`onboarding_completed: 0` as the pre-finish state they're testing). A
full `npx playwright test e2e/` run on develop before this task's edits was already
60/60 green; see `evidence/ATL-31/playwright-run.log`.

**What this task added**, so the fixture shape stays load-bearing rather than accidental:
`portfolio-overview.spec.ts`'s `fakeApi(page, { stale?, onboarded? })` now takes an
`onboarded` flag (default `true`). Setting it `false` omits `onboarding_completed` from the
settings payload (version stays high, 3) and the fixture also serves the three `presets` the
risk step needs to render cleanly. A new test, `'a verified owner whose settings lack
onboarding_completed stays on /onboarding, not /portfolio'`, proves the gate itself hasn't
weakened: it still redirects on a fixture that reproduces the old bug.

**If you add a spec that fakes `/api/settings` and expects to land past `/`:** always include
`onboarding_completed: 1` with `version > 1` (or route `/` through a step that sets it, as
`onboarding-steps.spec.ts` does). If you navigate straight to a nested route
(`/portfolio/positions`, `/settings/connections`, …) instead of `/`, the gate's index route
never runs and you don't need the settings fixture at all — that's why
`portfolio-positions.spec.ts` and `portfolio-holdings-cashflows.spec.ts` don't mock
`/api/settings`.

There is still no single shared e2e fixture module — each spec keeps its own local `fakeApi`
(deliberate duplication so two developers don't collide in one file, per the monorepo
decision). If ATL-25 wants to share the onboarded-settings shape, copy the `onboarded`-flag
pattern above rather than importing across spec files.

## The RTL date bug (ATL-20's bidi fix, now also on Positions and Cash flows)

`@utility fig` (`src/styles/tokens.css`) is `direction: ltr; unicode-bidi: isolate;` — correct
for a figure (money, %, ×, a UID tail) so it reads the same order in English and Arabic. A
**date** is not a figure: "25 تشرين الأول" must read in the Arabic sentence's own direction, no
isolate, exactly like the "Last verified" date on Settings → Connections (ATL-20).

Fixed in this task:
- `PositionsPage.tsx`'s time-in-trade sub-label (`formatDay`) — was `<span className="pf-cell-sub fig">`, now `<span className="pf-cell-sub">`.
- `CashflowsPage.tsx`'s date column (`formatDayTime`) — was `<span className="fig" title={f.ts}>`, now `<span title={f.ts}>`.
- `EquityChart.tsx`'s `FrostedTooltip` (the Overview hover card, found by QA in round 1 review)
  — the `when` line (`formatDayTime`/`formatClock`+`formatDay` on 1D) was
  `<span className="fig text-caption ...">`, now `<span className="text-caption ...">`. The
  money line right below it keeps `fig`, since it's a figure.

**Round-1 miss, for whoever greps next**: a search for `fig` only across `*Page.tsx` and
`parts.tsx` isn't enough — `EquityChart.tsx` is a component the Overview page composes, not a
`*Page.tsx` file itself, and it carried the same bug in its tooltip. Grep the whole
`src/features/portfolio/` folder (and any component a Portfolio page imports) for
`className="fig` next to a `formatDay`/`formatDayTime`/`formatClock` call, not just the page
files.

Checked and clean: Overview's `sinceDate` sub-text (already a plain `<span>`), Holdings (no
date column at all — quantity/value/% are legitimately `fig`).

**Pattern for anyone adding a date to a Portfolio page or component**: render it as plain
text/`<span>`/`<time>` with no `fig` class. Only wrap in `fig` when the content is a
number-with-unit that must read the same way in both languages (money, %, counts, leverage, a
masked key tail). A regression test per surface is cheap insurance — Vitest for
`PositionsPage.test.tsx`'s `'mirrors in Arabic...'` test and `CashflowsPage.test.tsx`'s
`'renders in Arabic...'` test (`expect(dateEl).not.toHaveClass('fig')`); e2e (Playwright) for
anything recharts-rendered like `EquityChart`'s tooltip, since jsdom can't lay out or hover a
recharts `ResponsiveContainer` — see `portfolio-overview.spec.ts`'s `'the equity-chart hover
card date reads RTL in Arabic...'` test, which asserts both `not.toHaveClass(/fig/)` and the
computed `direction` CSS.

## Evidence

- `evidence/ATL-31/playwright-run.log` — full `npx playwright test e2e/`, 61/61 passing.
- `evidence/ATL-31/positions-ar-1280.png` — Positions in Arabic at 1280px; the time-in-trade
  date reads "25 تشرين الأول" in the Arabic order (right to left), Western digits, no LTR
  isolate.
- `evidence/ATL-31/overview-ar-hover.png` — Overview equity-chart hover card in Arabic; the
  date line "15 تشرين الأول، 03:51" reads in Arabic order, the money line below keeps LTR
  figures.
