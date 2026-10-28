# ATL-17: app shell, language and theme switch, risk footer

## The two-minute version

- **To get a sidebar item, add `nav` to your feature's `routes.tsx`.** The shell builds the sidebar, the highlight and the toolbar title from it. You never edit `AppShell.tsx`.
- **Don't render a page title as `<h1>`.** The toolbar's `<h1>` is the label of your nav entry. Your page starts with its own content (`title-2` summary and so on).
- **Don't add a footer.** `RiskFooter` is on every shell page and on `/login`. A page outside the shell (onboarding, for example) renders `<RiskFooter />` from `src/app/shell/RiskFooter.tsx` itself. `EnvFooter` in `AppShell.tsx` still exists for old imports; it ignores its `label` and renders `RiskFooter`.
- The shell page wrapper already gives you `max-width: var(--content-max)` (1180px), centred, with 32/24/16px gutters and the fade-and-rise page entry. Settings forms can use `max-w-[var(--form-max)]` (720px).

## Registering nav entries (`src/app/feature.ts`)

```tsx
import { Briefcase, ChartLine } from 'lucide-react'
const portfolio: FeatureModule = {
  routes: [{ path: 'portfolio', element: <Overview /> }, { path: 'portfolio/positions', element: <Positions /> }],
  nav: [
    { path: '/portfolio', labelKey: 'portfolio:nav.overview', order: 10, section: 'portfolio', icon: ChartLine },
    { path: '/portfolio/positions', labelKey: 'portfolio:nav.positions', order: 20, section: 'portfolio', icon: Briefcase },
  ],
}
```

- `nav` is a `NavEntry` or a `NavEntry[]`.
- `section` is `'portfolio' | 'settings'` (`NAV_SECTIONS`). A section with no entries is not drawn.
- `order` sorts within a section. Suggested: Portfolio 10–40, Settings 100–110.
- `labelKey` must carry its namespace (`portfolio:nav.overview`).
- `icon` is a Lucide icon component. It is drawn at 18px, stroke 1.5, in the accent colour.
- **Highlighting** goes to the entry with the longest path that equals the URL or prefixes it at a `/`. So `/portfolio/positions/BTC` highlights Positions, not Overview, and `/portfolios` highlights nothing.
- **Signed in at `/`** redirects to the first nav entry (lowest `order`). A feature's own index route at `/`, if one exists, wins over that. An unknown URL shows a "Nothing here yet" empty state inside the shell.
- `navEntries` and `features` now come from `src/app/registry.ts`. `router.tsx` re-exports them.

## Language (`src/i18n/language.ts`, `src/i18n/index.ts`)

- `setLanguage('ar' | 'en')` from `src/i18n` changes the language immediately (`<html lang dir>` flips with no reload) and stores it in `localStorage['atlas.lang']`.
- i18n starts from the stored value. The inline script in `index.html` sets `lang` and `dir` before the first paint, so a reload in Arabic never flashes LTR. `AppShell.test.tsx` runs that script against `readStoredLanguage()`.
- Theme is unchanged from ATL-14. The menu only calls `useTheme().setChoice`.
- Not done: the owner's `locale`/`theme` on `GET /api/auth/me` is **not** synced either way. localStorage is the only store, as the task says.

## Pieces you can reuse (`src/app/shell/`)

- `ToolbarGroup`, `LanguageButton` and `ThemeMenu` (`Prefs.tsx`). The login page uses them for its top-end prefs.
- `EnvBadge`: the teal "Demo" capsule with its tooltip.
- `toolbarButton` / `toolbarIconButton` (`toolbarClasses.ts`): 32px capsule button classes.
- `useTradingEnv()` (`tradingEnv.ts`) returns `'demo'` until Phase 4 reads it from settings. The footer and the badge both use it, and `'live'` turns the footer's environment name red.
- The menus are Radix `DropdownMenu`, styled as design §9 Menu: accent-solid highlight, ↑/↓ and Esc.

## Sign out

- The account menu (initials avatar) shows name and email, then Sign out.
- Sign out sets `SIGNED_OUT_KEY` (`['auth','signed-out']`) and runs `useLogout()`. `RequireAuth` then redirects to a plain `/login`: no `next`, replacing the history entry. A successful login clears the flag.
- Back from `/login` lands on a protected URL, and the guard sends it straight back to `/login`. The session is `null`, so nothing protected renders in between.
- The "Signed out" toast comes from `<Toaster />`, which is now mounted once in `main.tsx`. Every feature can call `toast()`.

## Not built here

- The sidebar's **account chip** (mini seal, `•••• 4821`, status dot) needs the connection and sync status. That belongs to Connections/Portfolio (ATL-18 / ATL-22): add it as the sidebar's last child when the data exists.
- The account menu's "Connections" item from the prototype is left out until a Connections route exists.
- The ⌘K palette and kill switch: the toolbar has an empty `data-slot="toolbar-actions"` slot before the Demo badge.
- `Positions` count in the sidebar (`nav-count`).

## Tests

- `src/app/AppShell.test.tsx` (jsdom): footer in both languages, sections and highlight, ↑/↓/Home/Tab plus Enter, language flip and persistence, the theme menu including System following the OS, sign out and Back, and the index.html language script.
- `e2e/shell.spec.ts` (Chromium, fake API via `page.route`): RTL puts the sidebar on the right and survives a reload, the theme survives a reload, the footer appears on login and in the shell, and sign out plus Back. Screenshots go to `../../evidence/ATL-17/`.
