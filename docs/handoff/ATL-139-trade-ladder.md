# ATL-139 — TechnicalSetup card and the shared trade ladder

For ATL-164 (proposal card + Edit sheet) and ATL-178 (PM position_update), which draw the same ladder.

## Files

| File | What |
| --- | --- |
| `frontend/src/features/warroom/cards/Ladder.tsx` + `ladder.css` | The trade ladder (design v11 Phase 2 §4) as a React component |
| `frontend/src/features/warroom/cards/rText.ts` | `rText(v, digits = 1)` → `"+1.2R"`, `"−1.0R"`, `"0.0R"` (sign from the rounded value) |
| `frontend/src/features/warroom/cards/technicalSetup.ts` | `readTechnicalSetup(card)` (ATL-135 card → typed, decimal strings kept), `isThin()` (> 10 bps) |
| `frontend/src/features/warroom/cards/TechnicalSetupCard.tsx` + `technicalSetup.css` | The card; registered in `Message.tsx` under `research_report`, before the Historian reader |
| `frontend/src/features/warroom/fixtures/technicalSetup.ts` | SOL long breakout / XAU no setup messages (design fixture day) |

## Ladder API

```tsx
<Ladder
  side="long"                       // 'long' | 'short'
  entry={['248.60', '250.20']}      // [low, high] decimal strings; equal values = a single price
  stop="241.80"
  riskStop="241.80"                 // optional: the stop the trade opened with (defines 1R); default = stop
  targets={[{ price: '258.50', r: '1.20', pct: '50' }]}  // r optional (computed from geometry), pct optional
  mark="249.95"                     // optional price now
  decimals={2}                      // tick precision; every price printed from its string at this precision
  compact                           // optional: 4 px rail, middle targets as ticks only, no notes
  lang={lang}
/>
```

- Always `dir="ltr"` (a number line), `role="img"` with the §4 sentence as aria-label. Labels: Entry/Now above, Stop/targets below.
- The 10 px label spacing ("labels never touch") runs in a `useLayoutEffect` + ResizeObserver + `document.fonts.ready`.
- Not built yet (no caller needed it): `wasStop` (dashed old stop) and the `liq` caption. The CSS for both is already in `ladder.css` (`.ladder-tick.is-was`, `.ladder-label.is-was`, `.ladder-liq`); add the props when ATL-164/178 need them.
- Strings are under `ld.*` in `warroom.json` (en/ar). Test ids: `ladder` / `ladder-compact`, `ld-stop`, `ld-entry`, `ld-target`, `ld-now`.
- The TechnicalSetup card renders both a full and a compact ladder; a container query on `.hv-card` (≤ 520 px) shows the compact one. Do the same in other cards.

## Two-minute version

ATL-135's card has no `tick_decimals`, `last_price` or level labels (the design asks for them). The reader takes tick precision from `tick_decimals`, then `meta.corrections[rule=tick_rounding].tick_sz`, then the most decimals on any price; level chips show the zone id (`1H:z2`) when there is no label; the Now dot appears only when `last_price` is present. Clicking the instrument navigates to its instrument thread (the Inspector shows it); with no such thread it is plain text.
