# ATL-22: Portfolio Overview

## The two-minute version

- `src/features/portfolio/` now exists. `routes.tsx` registers `/portfolio` (Overview), the
  "Overview" nav entry (`portfolio:nav.overview`, section `portfolio`, order 10, `ChartSpline`), and an
  **index route that redirects `/` to `/portfolio`**, so it is the landing page after sign-in.
  Positions, Holdings and Cash flows add their routes and nav entries **to this same `routes.tsx`**
  (orders 20–40); the `routes` and `nav` arrays are the only shared lines.
- Data hooks live in `src/features/portfolio/api.ts` and follow the ATL-12 contract exactly:
  `useSummary(env)`, `useEquity(env, range)`, `usePositions(env)`, `useConnections()`, the response
  types (`PortfolioSummary`, `EquitySeries`, `CashflowMarker`, `PositionsPayload`), the query keys
  (`portfolioKeys`, all start with `['portfolio', env, …]`), and `POLL_MS = 30_000`. Reuse them on
  the other Portfolio pages instead of new fetches, so the caches are shared.
- `env` comes from `useTradingEnv()` and is always passed as `?env=`.
- Times: `src/features/portfolio/time.ts` has `formatClock`, `formatDay`, `formatDayTime`,
  `formatAgo` (Asia/Baghdad, h23, `DATE_LOCALE`, so Western digits in Arabic), and the chart's
  `timeTicks` / `valueTicks`.
- Strings: `src/i18n/locales/{en,ar}/portfolio.json`. `nav.overview`, the shared `empty.*`,
  `error.*` and `waiting` keys are there for the sibling pages to reuse. Add your own keys under a
  page prefix (`positions.*`, `holdings.*`, `cashflows.*`); never redefine an existing key.

## Page behaviour

| Situation | What renders |
| --- | --- |
| summary pending | skeletons for hero, recon pill, chart and the six tiles; `aria-busy`, a "Loading your portfolio" status |
| summary failed (no cached data) | error block (`role=alert`) with **Try again**, which refetches summary, equity and positions |
| `total_equity_usd === null` | page-level empty state: "No data yet — Atlas is syncing your OKX demo account" plus "Waiting for OKX…" |
| equity pending / failed / 0 points | the chart area only shows a skeleton / compact error with retry / compact empty state |
| `stale: true` | orange pill "Last synced N minutes ago" (or "Not synced with OKX yet" when `reconciled_at` is null) and a muted seal with a warning mark |
| otherwise | green-check pill "Reconciled with OKX at HH:MM:SS" in Baghdad time |

- The account seal (176px, `seed = okx-demo-<uid_last4>`, caption `•••• 4821`) appears only when
  `GET /api/connections` has an `okx` connection for the env with `uid_last4`. It is hidden below 760px.
- Tiles: Net deposits and Fees are plain money; Total, Today, Unrealised PnL and Funding use
  `SignedValue`. `total_pnl_pct` null shows "—" with the decision-238 tooltip. "since <date>" uses
  `pnl_since`. Net deposits links to `/portfolio/cashflows`, Unrealised to `/portfolio/positions`
  (those routes are yours to add). The open-positions count comes from `/api/portfolio/positions`.
- The ledger is 6 across ≥1440px, 3×2 from 761px, 2 columns below (design `portfolio.css`).

## The chart (`EquityChart.tsx`)

- Recharts `AreaChart`, X is a numeric time axis (`t` = ms), Y uses `valueTicks` (nice round bounds).
  Points are plotted as `Number(equity_usd)` for drawing only; every label is formatted from the
  original decimal string.
- RTL: the wrapper is `dir="ltr"`, the X axis is `reversed` and the Y axis moves to the left, so
  time runs right to left. `data-reversed`, `data-range` and `data-points` on
  `[data-testid=equity-chart]` let tests check it without pixels.
- Markers: every `markers[]` row is a ring with an arrow at the foot of the plot and a dashed stem up
  to the curve, with a `<title>`. There is a legend at the head's end when markers are visible.
- Accessibility: `role="img"` with a range/from/to label, Recharts' keyboard layer (arrow keys move
  the tooltip), a visually hidden table of ~12 sampled points, a hidden list of the markers, and a
  polite live region repeating the selected range.

## Tests

- `src/features/portfolio/OverviewPage.test.tsx` (jsdom, `fetch` stubbed): tiles and hero, null pct,
  1M → 1D refetch, stale, empty, no points, slow, error + retry, the 30 s poll, Arabic digits and
  reversed axis, and `/` → `/portfolio`. jsdom needs a `ResizeObserver` stub for Recharts.
- `e2e/portfolio-overview.spec.ts` (fake API with seeder-shaped data): ltr/rtl × light/dark
  screenshots, axis direction, tile mirroring, the 1D hover card, the stale pill and a 720px RTL
  view. Shots go to `../../evidence/ATL-22/`.
- `e2e/shell.spec.ts`: the narrow-width test now looks for the `h2` inside `main`, because the
  sidebar has a "Portfolio" section heading now.

## Not built here

- The sidebar account chip from ATL-17's list. The connection data is available through
  `useConnections()` if someone builds it.
- The equity count-up animation from the prototype. A changed hero figure flashes green or red only.
- The benchmark toggle (decision 236).
