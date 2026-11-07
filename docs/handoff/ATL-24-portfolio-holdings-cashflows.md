# ATL-24: Portfolio Holdings and Cash flows

## The two-minute version

- Two routes and nav entries are in `src/features/portfolio/routes.tsx`:
  - `/portfolio/holdings` → `HoldingsPage.tsx`. Nav label `portfolio:nav.holdings`, order 30, `Wallet` icon.
  - `/portfolio/cashflows` → `CashflowsPage.tsx`. Nav label `portfolio:nav.cashflows`, order 40, `ArrowLeftRight` icon.
  - The Portfolio section now has four entries, in the order 10/20/30/40.
- New hooks are in `api.ts`, with query keys under `['portfolio', env, …]`:
  - `useHoldings(env)` → `{ data: HoldingsPayload }`, polled every 30 s.
  - `useCashflows(env, page)` → `Paginated<CashflowMarker>`, using the Laravel paginator's `data` and `meta`. `per_page` is `CASHFLOWS_PER_PAGE = 20`, and it keeps the previous page's data while the next page loads.
  - Types: `Holding`, `HoldingsPayload` and `Paginated<T>`. Cash flow rows reuse the existing `CashflowMarker` type.
- **`formatDecimal(value, lang, minFrac = 2, signDisplay = 'auto')` is now in `src/lib/format.ts`.** It groups digits en-US, never rounds, and uses a typographic minus. Positions now uses it instead of its own copy. Use it for every plain decimal: quantities, sizes and counts.
- `ledger.ts` holds the pure helpers:
  - `byValueDesc` sorts holdings by `value_usd` as decimal strings. Rows with a null value go last.
  - `flowState(code)` maps the raw OKX state: `"2"` → completed, `"-1"`/`"-2"` → failed, anything else → pending. The raw code is shown in the badge tooltip.
  - `shortTx` turns a tx id into its first 6 and last 4 characters.
- The Cash flows page number is stored in `?page=`. A page past the end is replaced with the last page.

## Page behaviour

| Situation | Holdings | Cash flows |
| --- | --- | --- |
| pending | head skeleton + 7-col table skeleton, `aria-busy` | head skeleton + 6-col skeleton |
| failed, no cached data | `StateBlock kind=error` + Try again | same |
| `taken_at === null` | `StateBlock kind=empty` (syncing) | n/a (the paginator has no taken_at) |
| no rows | panel with "No balances" (`kind=none`) | panel with "No deposits or withdrawals"; no buttons at all |
| rows | "N balances across trading and funding", Total value, recon pill, table | "N deposits and withdrawals", read-only note (lock), recon pill, table + pager |

- **Holdings** shows the quantity exactly as the API sends it, with at least 2 decimals.
  - The value uses `formatMoney(USD)`.
  - The share column shows a bar (minimum width 1.5%; only its width is computed from a float) and `formatPercent` (2 dp). The full API figure is in the `title`.
  - Average cost and 24h change show `—`, with `title`/`aria-label` "Available in a later phase" (`portfolio:laterPhase`).
  - The API has no currency name, so the design's grey name line under the ticker is not rendered.
- **Cash flows**:
  - Date is `formatDayTime(ts, lang, true)`, with the year, in Baghdad time. `formatDayTime` gained an optional `year` argument.
  - The amount is signed: `+` for deposits and `−` for withdrawals, whatever sign is stored.
  - The pager's range ("21–27 of 27") wraps each figure in U+2066…U+2069, so it doesn't reverse in Arabic.
- Test hooks:
  - `data-testid` values: `holdings-table|holding-row|holdings-summary|holdings-total|later-phase` and `cashflows-table|cashflow-row|cashflows-summary|cashflows-pager|cashflows-range`.
  - Holding rows carry `data-ccy`/`data-account`.
  - Flow rows carry `data-kind`/`data-state`.
  - Every cell has `data-col`.

## Tests

- `HoldingsPage.test.tsx`, `CashflowsPage.test.tsx` and the `formatDecimal` cases in `format.test.ts`.
- `e2e/portfolio-holdings-cashflows.spec.ts`: ltr/rtl × light/dark for both pages, paging, empty and error states, and the 1024px RTL view. Screenshots go to `../../evidence/ATL-24/`.

## Not built here

- Currency names, average cost and 24h change (decision 236).
- The real-demo quantity comparison against OKX `account/balance` and `asset/balances`. The page renders `quantity` verbatim, so this is a check of ATL-10's sync, run on develop.
