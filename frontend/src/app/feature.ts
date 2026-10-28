import type { LucideIcon } from 'lucide-react'
import type { RouteObject } from 'react-router'

/** The sidebar's source-list sections (design: Shell), in display order. */
export const NAV_SECTIONS = ['portfolio', 'settings'] as const
export type NavSection = (typeof NAV_SECTIONS)[number]

/** One sidebar item. The shell sorts by `order` inside its section. */
export interface NavEntry {
  path: string
  /** i18n key with its namespace, e.g. `portfolio:nav.overview`. Also the toolbar title. */
  labelKey: string
  order: number
  section: NavSection
  /** A Lucide icon, drawn at 18px in the accent colour. */
  icon?: LucideIcon
}

/** What every src/features/<name>/routes.tsx default-exports. */
export interface FeatureModule {
  /** Routes mounted inside the app shell. */
  routes: RouteObject[]
  /** Routes outside the shell that need no session (the login page). */
  publicRoutes?: RouteObject[]
  /** Sidebar entries: one, or several when a feature has several pages (Portfolio has four). */
  nav?: NavEntry | NavEntry[]
}

/** Every nav entry from every feature, in order. */
export function collectNavEntries(features: readonly FeatureModule[]): NavEntry[] {
  return features
    .flatMap((f) => (f.nav === undefined ? [] : Array.isArray(f.nav) ? f.nav : [f.nav]))
    .sort((a, b) => a.order - b.order)
}

/** The entry the current URL belongs to: an exact match, else the longest path prefix. */
export function activeNavEntry(entries: readonly NavEntry[], pathname: string): NavEntry | undefined {
  let best: NavEntry | undefined
  for (const entry of entries) {
    const base = entry.path.replace(/\/+$/, '') || '/'
    const hit = pathname === base || (base === '/' ? false : pathname.startsWith(`${base}/`))
    if (hit && (!best || base.length > best.path.length)) best = entry
  }
  return best
}
