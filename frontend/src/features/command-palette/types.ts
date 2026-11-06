import type { LucideIcon } from 'lucide-react'
import type { AreaId } from '../../app/feature'

/*
 * The ⌘K palette's contract (ATL-249, decision 265 (2), design v22 §6). Each feature lists what
 * the palette can do in its own src/features/<name>/palette.ts, found by glob like routes.tsx,
 * so two tasks never edit one list. See docs/handoff/ATL-249-command-palette.md.
 */

/** The palette's groups, in the order they are drawn. ATL-250 adds `instruments`. */
export type PaletteGroup = 'actions' | 'pages' | 'instruments'

export interface PaletteEntry {
  /** Unique across every feature, e.g. `page.portfolio.positions`, `action.stop`. */
  id: string
  group: PaletteGroup
  /** i18n key with its namespace; it needs an en and an ar value (a test checks every entry). */
  labelKey: string
  /** Optional second line (i18n key with namespace). */
  subKey?: string
  /** Interpolation values for `subKey`. */
  subValues?: Record<string, string>
  /** Extra words to match, in both languages, space-separated. */
  keywords?: string
  icon: LucideIcon
  /** Pages: the area named at the row's end. */
  area?: AreaId
  /** Sorts inside its group (pages: after the area order). Default 0. */
  order?: number
  /** `stop` draws the red octagon tile (Stop trading…). */
  tone?: 'stop'
  /** Shown but not choosable. */
  disabled?: boolean
  /** Navigate here on ↵ … */
  to?: string
  /** … or run this. The palette is closed before it runs. */
  run?: () => void | Promise<void>
}

/**
 * What a palette.ts default-exports. `entries` are fixed; `useEntries` is a React hook called on
 * every render of the open palette with the current query, for entries that depend on live state
 * (trading paused or not) or on a search (ATL-250's instruments).
 */
export interface PaletteModule {
  entries?: PaletteEntry[]
  useEntries?: (query: string) => PaletteEntry[]
}
