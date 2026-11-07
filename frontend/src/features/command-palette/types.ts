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
  /**
   * An instrument row (ATL-250): drawn with its asset-class tile, inst_id and OKX state. It was
   * matched by the server, so it is listed in the order it came and not ranked by its label.
   */
  instrument?: PaletteInstrument
}

export interface PaletteInstrument {
  instId: string
  /** `command-palette:inst.class.<key>`: crypto, spot, stock, etf, index, commodity, other. */
  classKey: 'crypto' | 'spot' | 'stock' | 'etf' | 'index' | 'commodity' | 'other'
  /** OKX's state: live, suspend, preopen, test, … */
  state: string
}

/** A status row at the end of a group (ATL-250): a search in flight, nothing found, or a failure. */
export interface PaletteStatus {
  group: PaletteGroup
  kind: 'searching' | 'none' | 'error'
}

/** What a `useEntries` hook returns: entries alone, or entries with a status row. */
export type PaletteProvided = PaletteEntry[] | { entries: PaletteEntry[]; status?: PaletteStatus | null }

/**
 * What a palette.ts default-exports. `entries` are fixed; `useEntries` is a React hook called on
 * every render of the open palette with the current query, for entries that depend on live state
 * (trading paused or not) or on a search (ATL-250's instruments, which also returns a status row).
 */
export interface PaletteModule {
  entries?: PaletteEntry[]
  useEntries?: (query: string) => PaletteProvided
}
