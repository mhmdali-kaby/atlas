# ATL-41 — Sidebar areas: how a feature joins an area and adds sidebar sections

The sidebar now shows one **area** at a time (design v4: Shell, "Areas"). At its top a vertical
segmented switcher (War Room / Portfolio / Settings); below it only the current area's sections.
Everything is registered from the feature's own `src/features/<name>/routes.tsx` — no shared nav
list exists, and nothing in `src/app` needs editing to add an area's content.

## The contract (`src/app/feature.ts`)

```ts
type AreaId = 'warroom' | 'portfolio' | 'settings'

interface FeatureModule {
  routes: RouteObject[]
  publicRoutes?: RouteObject[]
  area?: AreaId                              // required when nav or sidebar is set (else buildShellRegistry throws)
  nav?: NavEntry | NavEntry[]                // links: { path, labelKey, order, icon? } — no `section` field any more
  sidebar?: SidebarSection | SidebarSection[] // sections the feature draws itself
}

interface SidebarSection {
  id: string                                 // unique within the feature; used for the heading id `nav-heading-<id>`
  order: number                              // sorted with the area's link list (which uses its first link's order)
  labelKey?: string                          // optional visible heading, e.g. 'warroom:sidebar.agents'
  component: ComponentType<{ onNavigate: () => void }>
}
```

- All `nav` links of one area (from any number of features) render as **one** list, sorted by `order`.
  Settings is two features (connections 110, risk 120) and shows one list.
- Each `sidebar` section renders in its own `role="group"` (labelled by its heading when `labelKey` is set).
- An area appears in the switcher **only once some feature contributes to it**. Today War Room is
  hidden because nothing registers it; it appears (first, before Portfolio) as soon as ATL-39's
  module declares `area: 'warroom'` with a `sidebar` section or a `nav` link.
- The area of the current URL: the matching nav link's area, else the area whose base path holds the
  URL (`/warroom`, `/portfolio`, `/settings` — `AREAS` in feature.ts). So `/warroom/threads/<id>` is in
  the War Room area with no nav links at all.
- The switcher's link for an area goes to the last page opened in it this session, else its first nav
  link, else its base path (`/warroom`).
- Toolbar title: the active nav link's label, else the area's label ("War Room"), else "Atlas".

## For ATL-39 — the War Room sidebar

`src/features/warroom/routes.tsx` (or whatever folder you own):

```tsx
const warroom: FeatureModule = {
  area: 'warroom',
  routes: [{ path: 'warroom', element: <WarRoomPage /> }, { path: 'warroom/threads/:id', element: <WarRoomPage /> }],
  sidebar: [
    { id: 'agents', order: 10, labelKey: 'warroom:sidebar.agents', component: AgentList },
    { id: 'threads', order: 20, labelKey: 'warroom:sidebar.threads', component: ThreadList },
  ],
}
export default warroom
```

- The shell gives your component `onNavigate`; call it after a row click that navigates (it closes
  the sidebar overlay at narrow widths).
- Put `data-nav-item` on every focusable row (link or button): the shell's ↑/↓/Home/End handler on the
  sidebar list moves focus between all `[data-nav-item]` elements in the area, links and your rows alike.
- Rows: use the existing link look (34px high, `rounded-[9px]`, `hover:bg-fill-4`, current row
  `bg-selection font-semibold` + `aria-current="page"`), see `src/app/shell/Sidebar.tsx`.
- If you want "/" to land on the War Room instead of Portfolio, that is the onboarding gate's
  `HomeRedirect` (first nav link overall) and portfolio's index route — not part of this task.

## Shell API changes (for anyone rendering the shell in tests)

- `AppShell` / `HomeRedirect` / `OnboardingGate` take `shell?: ShellRegistry` instead of `nav?: NavEntry[]`.
  Build one with `buildShellRegistry([fakeFeature, …])`; `src/app/registry.ts` exports `shellRegistry`
  (and `navEntries`) from the globbed features.
- Helpers: `activeArea(registry, pathname)`, `areaHome(registry, areaId)`, `activeNavEntry(entries, pathname)`.
- Test hooks: the switcher is `<nav aria-label="Areas" data-testid="area-switch">` with links carrying
  `data-area` and `aria-current="true"` on the current one; the list below is `<nav aria-label="<Area name>">`.

## Keyboard

Switcher: one Tab stop (roving tabindex on the current area); ↑/↓/Home/End move; Enter or Space opens.
Focus ring: `var(--focus-ring)` (3px accent at 40%). New tokens: `--area-thumb`, `--area-thumb-shadow`.

## Tests

- `src/app/feature.test.tsx` — the registry with fake FeatureModules (a War Room feature with a custom
  section, an extra Portfolio link) and a shell render on `/warroom`.
- `e2e/flows/area-switcher.appearance.spec.ts` — 1440×900 in en/ar × light/dark, click and keyboard;
  screenshots in `evidence/ATL-41/`.
