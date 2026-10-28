# ATL-14 — design tokens, theme and Storybook

Everything a screen or component needs from the design system ("Atlas: Phase 0" §3–§7). Built from the approved design's `css/tokens.css`, with the same values.

## The one rule

`frontend/src/styles/tokens.css` is the **only** file under `src/` that may contain a raw colour. `src/styles/no-raw-colours.test.ts` fails on any hex literal anywhere else under `src/`, and on `rgb()/rgba()/hsl()/oklch()…` outside `*.test.*` / `*.stories.*` files. Those two kinds of file may name `rgb(...)` because browsers report computed colours that way. Need a colour that doesn't exist? Add it to tokens.css as a token. Never put it inline.

Tailwind's stock palette, radii, shadows, type sizes and fonts are **wiped** (`--color-*: initial` and so on). So `bg-slate-100`, `rounded-lg`, `text-sm`, `shadow-md` and `font-serif` generate nothing.

## Utilities you get

Use these names. They switch with the theme on their own, so components never need `dark:` for a colour.

| Kind | Utilities (prefix `bg-` / `text-` / `border-` / `ring-` …) |
| --- | --- |
| Surfaces | `background`, `background-secondary`, `background-tertiary`, `background-grouped`, `background-grouped-row`, `background-elevated`, `background-window` |
| Text, lines | `label`, `label-secondary`, `label-tertiary`, `separator`, `separator-strong`, `hairline`, `on-solid` (white on filled buttons) |
| Accent | `accent` (fills, icons, focus), `accent-ink` (text, links), `accent-solid` / `-hover` / `-active` (filled buttons), `accent-wash` |
| Status | `profit` `profit-ink` `profit-wash`, `loss` `loss-ink` `loss-wash`, `warning` `warning-ink` `warning-wash`, `demo` `demo-ink` `demo-wash`, `agent`, `destructive-solid`, `neutral` |
| Controls | `fill-1`…`fill-4`, `field`, `field-border`, `field-border-hover`, `switch-off`, `switch-off-border`, `knob`, `selection`, `scrim` |
| Seal, chart | `seal-ink-1`, `seal-ink-2`, `seal-ground`, `seal-muted`, `chart-line`, `chart-area-top`, `chart-area-bottom`, `chart-grid`, `chart-axis`, `wash-1`, `wash-2` |

Design rule (§3.1): **text never uses a plain status colour**. Use `text-profit-ink`, never `text-profit`.

- **Radius:** `rounded-control` 10, `rounded-card` 12, `rounded-panel` 16, `rounded-sheet` 20, `rounded-full` (capsules).
- **Type:** `text-large-title`, `text-title-1`, `text-title-2`, `text-title-3`, `text-headline`, `text-body`, `text-callout`, `text-subheadline`, `text-footnote`, `text-caption`, plus `text-hero` (equity only). Each one sets size, line height and tracking; the titles and the headline also set weight 600. Under `lang="ar"` the line heights grow, tracking goes to 0, and footnote and caption step up to 14 and 13. That happens through variables, so markup stays the same.
- **Fonts:** `font-sans` (switches to Arabic under `lang="ar"`), `font-display`, `font-num` (Latin figures in both languages), `font-mono`, `font-arabic`.
- **Shadows:** `shadow-1`, `shadow-2`, `shadow-popover`, `shadow-sheet`, `shadow-sidebar`, `shadow-focus` (the §15 focus ring), `shadow-glass-edge`.
- **Materials and helpers** (from `@utility`):
  - `vibrancy-bar`, `vibrancy-sidebar`, `vibrancy-popover`: the §15 translucent fill plus `backdrop-filter`.
  - `sheet-surface`: elevated background, 0.5px hairline, radius 20 and the sheet shadow.
  - `fig`: an LTR-isolated figure in the number font (design §5).
- **Motion:** `ease-settle` (the 300/30 spring that never bounces), `ease-out`, `ease-standard`. The durations are CSS variables: `--dur-fast`, `--dur-base`, `--dur-slow`, `--dur-sheet`.

Plain CSS can use the design document's own names directly: `var(--label)`, `var(--accent-ink)`, `var(--shadow-sheet)`, `var(--material-filter)`, `var(--focus-ring)`, `var(--hairline)`.

Base styles in tokens.css:

- tabular numbers on `html`;
- `:focus-visible` shows the focus ring;
- `::selection` uses the accent wash;
- `prefers-reduced-motion` makes animations instant.

## Theme

- Dark mode is class-based: `@custom-variant dark (&:where(.dark, .dark *))`. `<html>` carries `scheme-light dark:scheme-dark`.
- Any element can carry `.light` or `.dark` to force a theme on its subtree. The Tokens story uses this to show both side by side.
- `localStorage['atlas.theme']` is `"light"` or `"dark"`. A missing key, or any other value, means **system**.
- The inline `<script id="atlas-theme-init">` in `index.html` applies the theme before the first paint. `theme.test.ts` runs that script to keep it in step with `theme.ts`, so if you change one, change the other.
- `src/theme/useTheme.ts` is a Zustand store: `useTheme((s) => s.choice)` (`'system' | 'light' | 'dark'`), `s.resolved` (`'light' | 'dark'`) and `s.setChoice(choice)`. `setChoice` persists the choice (system removes the key) and toggles the class.
- `initTheme()` is called once in `main.tsx`. It also follows OS changes live while the choice is system.
- **ATL-17's appearance menu** only needs `setChoice`.
- Pure helpers in `src/theme/theme.ts`: `resolveTheme`, `applyResolvedTheme`, `watchSystemTheme`, `readStoredChoice`, `THEME_CHOICES`.

## Storybook

- `npm run storybook` serves on :6006; `npm run build-storybook` builds to `storybook-static/`, which is gitignored.
- Stories live anywhere matching `src/**/*.stories.tsx`.
- The toolbar has two controls:
  - **Theme:** Light, Dark or System. It sets the `dark` class on `<html>`.
  - **Direction:** LTR · English, or RTL · العربية. It sets `dir` and `lang` on `<html>` and on the story root, and switches i18n.
- Every story renders inside `src/storybook/StoryFrame.tsx`, as `<div data-story-root class="bg-background text-label text-body">`.
- **Stories are tests.** Vitest has two projects, `unit` (jsdom) and `storybook` (real headless Chromium through `@storybook/addon-vitest`). `npx vitest --run` runs both. Each story's `play` function runs as a test; use it to assert computed styles in both themes and both directions. Per-story toolbar state: `globals: { theme: 'dark', direction: 'rtl' }`.
- Examples: `src/storybook/StoryFrame.stories.tsx` and `src/styles/Tokens.stories.tsx`.

## E2E

- `frontend/playwright.config.ts` and `frontend/e2e/`.
- `npx playwright test` starts its own Vite server on `E2E_PORT` (default 3140, never 3000).
- Output goes to `../../evidence/playwright/`.
- `e2e/theme-no-flash.spec.ts` proves that a stored dark theme is on `<html>` at the first frame, before React mounts.

## Changed outside the token files

- `AppShell`'s footer moved from `text-xs opacity-60` to `text-caption text-label-secondary`, because the stock sizes are gone.
- `src/index.css` is now only the two imports: Tailwind, then tokens.

## Not built here

No components (ATL-15) and no seal. The seal tokens exist, but the geometry is ATL-15's or ATL-22's.
