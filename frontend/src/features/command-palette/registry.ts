import { AREAS } from '../../app/feature'
import type { PaletteEntry, PaletteGroup, PaletteModule, PaletteProvided, PaletteStatus } from './types'

/* Every src/features/<name>/palette.ts, found by glob (decision 265 (2)), like routes.tsx. */
const modules = import.meta.glob<{ default: PaletteModule }>('../*/palette.ts', { eager: true })

export const paletteModules: PaletteModule[] = Object.keys(modules)
  .sort()
  .map((k) => modules[k]!.default)

/** Every fixed entry from every feature. */
export const staticEntries: PaletteEntry[] = paletteModules.flatMap((m) => m.entries ?? [])

/** The live-state and search hooks, fixed at load, so each render calls the same hooks in the same order. */
export const entryHooks: NonNullable<PaletteModule['useEntries']>[] = paletteModules.flatMap((m) => (m.useEntries ? [m.useEntries] : []))

/** A `useEntries` result as entries plus an optional status row (ATL-250). */
export function unpack(provided: PaletteProvided): { entries: PaletteEntry[]; status: PaletteStatus | null } {
  return Array.isArray(provided) ? { entries: provided, status: null } : { entries: provided.entries, status: provided.status ?? null }
}

export const GROUP_ORDER: readonly PaletteGroup[] = ['actions', 'pages', 'instruments']

const areaRank = (e: PaletteEntry) => {
  const i = AREAS.findIndex((a) => a.id === e.area)
  return i === -1 ? AREAS.length : i
}

/** Pages in the area switcher's order, then each feature's own order. */
export function sortEntries(list: readonly PaletteEntry[]): PaletteEntry[] {
  return [...list].sort(
    (a, b) =>
      GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) || areaRank(a) - areaRank(b) || (a.order ?? 0) - (b.order ?? 0),
  )
}

/**
 * Folds case, Arabic diacritics and tatweel, and the alef/yeh/teh-marbuta spellings, so
 * "المحفظه" finds "المحفظة" and "اعدادات" finds "الإعدادات".
 */
export function normalise(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f\u064b-\u065f\u0670\u0640]/g, '')
    .replace(/[أإآٱ]/g, 'ا')
    .replace(/ى/g, 'ي')
    .replace(/ة/g, 'ه')
    .replace(/\.{3}|…/g, '')
}

/**
 * Design §6 ranking: a name starting with the query (0), a word of it starting with it (1), a
 * name containing it (2), then a keyword starting with it (3); -1 is no match. Names are the
 * entry's label in both languages, whichever is showing.
 */
export function matchRank(names: readonly string[], keywords: string | undefined, query: string): number {
  const q = normalise(query).trim()
  if (!q) return 0
  const ns = names.map(normalise)
  if (ns.some((n) => n.startsWith(q))) return 0
  if (ns.some((n) => n.split(/\s+/).some((w) => w.startsWith(q)))) return 1
  if (ns.some((n) => n.includes(q))) return 2
  if (normalise(keywords ?? '').split(/\s+/).some((w) => w.length > 0 && w.startsWith(q))) return 3
  return -1
}
