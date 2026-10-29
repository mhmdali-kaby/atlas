import type { ComponentType } from 'react'
import { ChartSpline, MessagesSquare, Settings, type LucideIcon } from 'lucide-react'
import type { RouteObject } from 'react-router'

/**
 * The shell's areas (design v4: Shell, "Areas"). The sidebar shows one area at a time: the
 * switcher at its top moves between them and the list below belongs to the current area.
 * An area appears in the switcher only once a feature contributes to it.
 */
export type AreaId = 'warroom' | 'portfolio' | 'settings'

export interface AreaDef {
  id: AreaId
  /** i18n key with its namespace. */
  labelKey: string
  icon: LucideIcon
  /** URLs under this prefix belong to the area even when no sidebar link matches them. */
  basePath: string
}

export const AREAS: readonly AreaDef[] = [
  { id: 'warroom', labelKey: 'shell:area.warroom', icon: MessagesSquare, basePath: '/warroom' },
  { id: 'portfolio', labelKey: 'shell:area.portfolio', icon: ChartSpline, basePath: '/portfolio' },
  { id: 'settings', labelKey: 'shell:area.settings', icon: Settings, basePath: '/settings' },
]

/** One sidebar link. The shell sorts by `order` inside the feature's area. */
export interface NavEntry {
  path: string
  /** i18n key with its namespace, e.g. `portfolio:nav.overview`. Also the toolbar title. */
  labelKey: string
  order: number
  /** A Lucide icon, drawn at 18px in the accent colour. */
  icon?: LucideIcon
}

/** A nav entry with the area of the feature that registered it. */
export interface AreaNavEntry extends NavEntry {
  area: AreaId
}

/** What a custom sidebar section component receives from the shell. */
export interface SidebarSectionProps {
  /** Call after a click that navigates, so the narrow-window sidebar closes. */
  onNavigate: () => void
}

/**
 * A sidebar section a feature draws itself (the War Room's agents and threads). Interactive
 * rows inside it should carry `data-nav-item` so ↑/↓ move between them like the links.
 */
export interface SidebarSection {
  id: string
  order: number
  /** Optional visible heading (i18n key). Also the group's accessible name. */
  labelKey?: string
  component: ComponentType<SidebarSectionProps>
}

/** What every src/features/<name>/routes.tsx default-exports. */
export interface FeatureModule {
  /** Routes mounted inside the app shell. */
  routes: RouteObject[]
  /** Routes outside the shell that need no session (the login page). */
  publicRoutes?: RouteObject[]
  /** The area this feature's pages and sidebar sections belong to. Required with `nav` or `sidebar`. */
  area?: AreaId
  /** Sidebar links: one, or several when a feature has several pages (Portfolio has four). */
  nav?: NavEntry | NavEntry[]
  /** Sidebar sections the feature renders itself, shown under its area. */
  sidebar?: SidebarSection | SidebarSection[]
}

/** One block of the active area's sidebar: a feature's links, or a section it draws. */
export type ShellSection =
  | { kind: 'links'; key: string; area: AreaId; order: number; entries: AreaNavEntry[] }
  | { kind: 'custom'; key: string; area: AreaId; order: number; section: SidebarSection }

/** Everything the shell draws from the registered features. */
export interface ShellRegistry {
  /** Areas with at least one contribution, in switcher order. */
  areas: AreaDef[]
  /** Every link from every feature, in order. */
  navEntries: AreaNavEntry[]
  sections: ShellSection[]
}

const asList = <T,>(v: T | T[] | undefined): T[] => (v === undefined ? [] : Array.isArray(v) ? v : [v])

/** Builds the switcher and the per-area sections from the features alone (no shared nav list). */
export function buildShellRegistry(features: readonly FeatureModule[]): ShellRegistry {
  const sections: ShellSection[] = []
  // All links of one area form one list (Settings' categories come from two features).
  const links = new Map<AreaId, AreaNavEntry[]>()
  features.forEach((feature, index) => {
    const nav = asList(feature.nav)
    const custom = asList(feature.sidebar)
    if (nav.length === 0 && custom.length === 0) return
    const area = feature.area
    if (!area || !AREAS.some((a) => a.id === area)) {
      throw new Error(`A FeatureModule with nav or sidebar sections must declare a known area (got ${String(area)}).`)
    }
    if (nav.length > 0) links.set(area, [...(links.get(area) ?? []), ...nav.map((e) => ({ ...e, area }))])
    for (const section of custom) sections.push({ kind: 'custom', key: `custom-${index}-${section.id}`, area, order: section.order, section })
  })
  for (const [area, list] of links) {
    const entries = [...list].sort((a, b) => a.order - b.order)
    sections.push({ kind: 'links', key: `links-${area}`, area, order: entries[0]!.order, entries })
  }
  sections.sort((a, b) => a.order - b.order)
  const used = new Set(sections.map((s) => s.area))
  return {
    areas: AREAS.filter((a) => used.has(a.id)),
    navEntries: sections.flatMap((s) => (s.kind === 'links' ? s.entries : [])).sort((a, b) => a.order - b.order),
    sections,
  }
}

/** Every nav entry from every feature, in order. */
export function collectNavEntries(features: readonly FeatureModule[]): AreaNavEntry[] {
  return buildShellRegistry(features).navEntries
}

const under = (pathname: string, base: string) => pathname === base || (base !== '/' && pathname.startsWith(`${base}/`))

/** The entry the current URL belongs to: an exact match, else the longest path prefix. */
export function activeNavEntry<E extends NavEntry>(entries: readonly E[], pathname: string): E | undefined {
  let best: E | undefined
  for (const entry of entries) {
    const base = entry.path.replace(/\/+$/, '') || '/'
    if (under(pathname, base) && (!best || base.length > best.path.length)) best = entry
  }
  return best
}

/** The area the current URL is in: its sidebar link's area, else the area whose base path holds it. */
export function activeArea(registry: ShellRegistry, pathname: string): AreaDef | undefined {
  const entry = activeNavEntry(registry.navEntries, pathname)
  if (entry) return registry.areas.find((a) => a.id === entry.area)
  return registry.areas.find((a) => under(pathname, a.basePath))
}

/** Where choosing an area goes when it has no remembered page: its first link, else its base path. */
export function areaHome(registry: ShellRegistry, area: AreaId): string {
  return registry.navEntries.find((e) => e.area === area)?.path ?? AREAS.find((a) => a.id === area)!.basePath
}
