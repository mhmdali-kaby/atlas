# ATL-249 — ⌘K command palette: how to add entries (for ATL-250 instruments)

Design v22 §6, decision 265 (2). Code: `frontend/src/features/command-palette/`.

## What exists

- `CommandPalette` is mounted once in `AppShell` (so only signed-in pages have it). ⌘K / Ctrl+K
  toggles it; it is ignored while a `[data-sheet]` is open. Esc, the scrim, the `esc` key cap and
  ⌘K again close it, and focus goes back to the element that had it. After a choice focus is *not*
  sent back (the page or the sheet takes it).
- Doors: the sidebar brand row's search capsule (`PaletteDoors.tsx`, `data-testid="palette-open"`)
  and "Search or jump to…" first in the toolbar's More menu. Both call `openPalette()` from `store.ts`.
- It is `cmdk` (`Command`, `shouldFilter={false}`, `loop`) inside a Radix `Dialog`. Filtering and
  ranking are ours (`registry.ts`), so labels match in English **and** Arabic whichever is showing.

## The contract — `src/features/command-palette/types.ts`

```ts
type PaletteGroup = 'actions' | 'pages' | 'instruments'   // drawn in this order

interface PaletteEntry {
  id: string            // unique, e.g. 'page.portfolio.positions', 'action.stop', 'inst.BTC-USDT-SWAP'
  group: PaletteGroup
  labelKey: string      // 'ns:key' — needs en AND ar (a Vitest iterates every entry and fails otherwise)
  subKey?: string; subValues?: Record<string, string>   // second line
  keywords?: string     // extra words, both languages, space-separated
  icon: LucideIcon
  area?: AreaId         // pages: area named at the row end; the area name also finds the page
  order?: number        // within its group (pages sort by AREAS order, then this)
  tone?: 'stop'         // red octagon tile
  disabled?: boolean
  to?: string           // ↵ navigates …
  run?: () => void | Promise<void>   // … or runs this (palette already closed)
}

interface PaletteModule {
  entries?: PaletteEntry[]                       // fixed
  useEntries?: (query: string) => PaletteEntry[] // a React hook, called every render while open
}
```

Each feature default-exports a `PaletteModule` from `src/features/<name>/palette.ts`. They are found
by `import.meta.glob('../*/palette.ts', { eager: true })` in `registry.ts` — never edit a shared list.
`useEntries` hooks are collected once at module load (`entryHooks`), so the hook order is stable.

Example (kill-switch/palette.ts): `useEntries` reads `useSystemHealth()` to offer Pause **or** Resume,
and returns Stop (`run: () => useKillSheets.getState().setKill(true)` — it only opens the sheet) and
Run a cycle now (`POST /api/cycles` once, toast).

## Ranking (registry.ts `matchRank`)

0 label starts with the query · 1 a word of the label does · 2 label contains it · 3 a keyword starts
with it · −1 no match. Labels are tried in both languages. A page whose **area** name matches gets
area-rank + 0.5 (so "المحفظة" lists Portfolio's pages, Overview first). Results sort by group, then
rank, then the registered order. `normalise()` folds case, Arabic diacritics, tatweel, أ/إ/آ→ا, ى→ي, ة→ه.

## For ATL-250 (instruments)

- Add `src/features/<yours>/palette.ts` with `useEntries(query)` returning `group: 'instruments'`
  entries. The group heading `command-palette:g.instruments` ("Instruments" / "الأدوات") exists.
- **Not built here:** debounce/cancel, the "Searching instruments…", "No instruments match.",
  "Couldn't search instruments…" status rows, the instrument row layout (asset-class tile, ID in
  `--font-num`, name + class sub-line, OKX state badge, "Open in the War Room" hint) and the CSS for
  them (`.pal-status`, `.pal-badge`, `.pal-state`, `.pal-hint`, `.pal-inst` are already in
  `palette.css`, copied from the design). `Row` in `CommandPalette.tsx` draws only the page/action
  layout today, and `PaletteModule` has no status channel. You will need to extend both: e.g. let
  `useEntries` return `{ entries, status }` or add a per-entry render hint (`kind: 'instrument'`,
  `instrument: {...}`). Keep `results.length === 0` → the "No results for …" empty state.
- The instrument entry's `run` should select it in the War Room inspector (design §6), not navigate
  to a thread.

## Other things settled

- Labels reuse the sidebar keys (one name everywhere). New palette-only strings live in the
  `command-palette` namespace (`en`/`ar`), including `page.replay` and `page.pfScorecards`.
- No "Prompts" page entry: ATL-253 builds that page; add `settings-agents` (or its own) palette entry then.
- No "cycle running" disabled state: there is no API that says a cycle is running; a 429 from
  `POST /api/cycles` shows "A cycle started less than a minute ago."
- `kill-switch/sheetStore.ts` (`useKillSheets`) now holds the toolbar Stop sheet's open state and a
  Resume confirmation that `KillSwitchButton` renders; reset on unmount.
- New token pair `--palette-scrim` (6% light / 32% dark) in tokens.css.
- Tests: `CommandPalette.test.tsx` (jsdom needs `scrollIntoView` and `ResizeObserver` stubs for cmdk),
  `e2e/command-palette.spec.ts` (keyboard only, light LTR and dark RTL).
