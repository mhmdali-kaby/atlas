# ATL-23: Portfolio Positions

## The two-minute version

- `/portfolio/positions` (`PositionsPage.tsx`) and the "Positions" nav entry (`portfolio:nav.positions`,
  order 20, `Layers` icon) are in `src/features/portfolio/routes.tsx`. Holdings and Cash flows take
  orders 30 and 40 in the same arrays.
- **Shared pieces moved out of `OverviewPage.tsx`** so every Portfolio page uses the same ones:
  - `parts.tsx`: `ReconciledIndicator({ summary, lang, now })`, `Skeleton`, and
    `StateBlock({ kind, title, body, compact?, onRetry?, icon? })`. `kind` is `empty` (still syncing:
    seal and "Waiting for OKX…"), `none` (synced, nothing to list: an icon tile, no spinner) or `error`
    (`role=alert`, and a **Try again** button when `onRetry` is given).
  - `hooks.ts`: `useLang()` and `useNow(ms)`.
- Table styles live in `portfolio.css` as `.pf-panel` + `.pf-table`, ported from the design's
  `.panel .table`. The header is sticky to the scrolling `main`, separators are hairlines, there are no
  zebra rows, and `.is-num` makes a cell end-aligned and tabular. `.pf-cell-sub` is the grey second
  line, and `.pf-badge` is the pill. Below 1280px the panel scrolls sideways with the first column
  pinned, in both directions. Holdings and Cash flows should reuse these classes instead of adding
  their own.
- `api.ts` now types the full `Position` row from ATL-12. Phase 2 fields (`stop`, `take_profits`,
  `agent`, `proposal_id`) are typed `null` and not rendered.

## Page behaviour

| Situation | What renders |
| --- | --- |
| positions pending | head skeleton plus a 10-column table skeleton, `aria-busy` |
| positions failed (no cached data) | `StateBlock kind=error` with retry (refetches positions and summary) |
| `taken_at === null` (no sync tick yet) | `StateBlock kind=empty` ("No data yet … syncing") |
| `items` empty | head with recon pill, plus a panel with "No open positions on the OKX demo account. …" |
| otherwise | "N open positions" (`openPositions` plural keys), Unrealised total = summary `unrealised_pnl_usd`, recon pill, table |

- Columns in the design's order: instrument, side, size (contracts), leverage (`5×`), margin mode,
  entry, mark, liq. price, unrealised PnL (USD plus % sub-line, `SignedValue`), time in trade
  (`formatDuration` in `time.ts`, `Intl.DurationFormat`, with the Baghdad opening day as the sub-line
  and the full date and time in a tooltip).
- Prices keep every digit OKX sent and are never rounded. The design's minimum fraction digits apply:
  1 for values of 10,000 and above, otherwise 2. Figures are en-US in both languages.
- Test hooks: `data-testid=positions-table|position-row|positions-summary|positions-upl-total`,
  `data-inst` on each row, and `data-col` on every `th`/`td`.

## Not built here

- A count badge on the sidebar's Positions entry. The design shows one, but `NavEntry` has no count
  field; adding one means changing the shell.
