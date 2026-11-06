import { useQuery } from '@tanstack/react-query'
import { Bitcoin, ChartSpline, Gem, Landmark, Shapes, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTradingEnv, type TradingEnv } from '../../app/shell/tradingEnv'
import { api, type ApiError } from '../../lib/api'
import type { PaletteEntry, PaletteInstrument, PaletteProvided } from '../command-palette/types'

/*
 * The ⌘K palette's Instruments group (ATL-250, decision 265 (2), design v22 §6). From 2 typed
 * characters (spaces ignored) and 150 ms after the last key, GET /api/instruments?q= searches the
 * active env's instruments by inst_id or base currency, at most 20, every OKX state. A newer query
 * replaces the older one, and TanStack Query aborts the stale request through its signal. ↵ opens
 * the War Room with the instrument picked in the inspector (`?inst=`, see INSPECT_PARAM).
 */

export const MIN_CHARS = 2
export const DEBOUNCE_MS = 150
export const MAX_RESULTS = 20

/** `/warroom?inst=<inst_id>`: the inspector shows this instrument over the thread's own context. */
export const INSPECT_PARAM = 'inst'

export function inspectParam(search: string): string | null {
  const id = new URLSearchParams(search).get(INSPECT_PARAM)
  return id && id.trim() !== '' ? id.trim() : null
}

export function inspectPath(instId: string): string {
  return `/warroom?${INSPECT_PARAM}=${encodeURIComponent(instId)}`
}

export interface InstrumentHit {
  inst_id: string
  inst_type: string
  asset_class: string
  base_ccy: string | null
  quote_ccy: string | null
  settle_ccy: string | null
  state: string
  blocked: boolean
}

const CLASS_ICON: Record<PaletteInstrument['classKey'], LucideIcon> = {
  crypto: Bitcoin,
  spot: Bitcoin,
  commodity: Gem,
  stock: Landmark,
  etf: Landmark,
  index: ChartSpline,
  other: Shapes,
}

export function classKeyOf(hit: Pick<InstrumentHit, 'inst_type' | 'asset_class'>): PaletteInstrument['classKey'] {
  if (hit.inst_type === 'SPOT' && hit.asset_class === 'crypto') return 'spot'
  switch (hit.asset_class) {
    case 'crypto':
    case 'stock':
    case 'etf':
    case 'index':
    case 'commodity':
      return hit.asset_class
    default:
      return 'other'
  }
}

/** The query as the server gets it, or null below 2 characters. */
export function searchTerm(query: string): string | null {
  const q = query.replace(/\s+/g, '')
  return q.length >= MIN_CHARS ? q.slice(0, 32) : null
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return settled
}

export const instrumentSearchKey = (env: TradingEnv, q: string) => ['instruments', 'palette', env, q] as const

export function toEntry(hit: InstrumentHit): PaletteEntry {
  const classKey = classKeyOf(hit)
  return {
    id: `inst.${hit.inst_id}`,
    group: 'instruments',
    labelKey: `command-palette:inst.class.${classKey}`,
    icon: CLASS_ICON[classKey],
    to: inspectPath(hit.inst_id),
    instrument: { instId: hit.inst_id, classKey, state: hit.state },
  }
}

/** The palette provider: instrument rows and the group's status row. */
export function useInstrumentEntries(query: string): PaletteProvided {
  const env = useTradingEnv()
  const wanted = searchTerm(query)
  const settled = useDebounced(wanted, DEBOUNCE_MS)
  const search = useQuery<{ data: InstrumentHit[] }, ApiError>({
    queryKey: instrumentSearchKey(env, settled ?? ''),
    enabled: settled !== null && settled === wanted,
    queryFn: ({ signal }) =>
      api.get(`/api/instruments?q=${encodeURIComponent(settled ?? '')}&limit=${MAX_RESULTS}&state=any&env=${env}`, { signal }),
    staleTime: 60_000,
    retry: false,
  })

  if (wanted === null) return []
  if (settled !== wanted || search.isPending) return { entries: [], status: { group: 'instruments', kind: 'searching' } }
  if (search.isError) return { entries: [], status: { group: 'instruments', kind: 'error' } }
  const entries = search.data.data.slice(0, MAX_RESULTS).map(toEntry)
  return { entries, status: entries.length === 0 ? { group: 'instruments', kind: 'none' } : null }
}
