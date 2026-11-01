import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { api, API_URL, ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * Portfolio → History (ATL-173) over ATL-172's API (docs/handoff/ATL-172-portfolio-history-api.md):
 * GET /api/portfolio/history?type=fills|closed&…filters (cursor pages, newest first) and
 * GET /api/exports/{fills|closed}.csv with the same filters. Decimals are strings; `r` has 2 dp.
 */

export type HistoryType = 'fills' | 'closed'
export const HISTORY_TYPES: readonly HistoryType[] = ['fills', 'closed']

export interface FillRow {
  id: string
  ts: string
  inst_id: string
  asset_class: string | null
  side: 'buy' | 'sell'
  pos_side: string | null
  price: string
  size: string
  fee: string | null
  fee_ccy: string | null
  fill_pnl: string | null
  exec_type: string | null
  trade_id: string | null
  ord_id: string | null
  order_id: string | null
  proposal_id: string | null
  agent: string | null
  origin: 'atlas' | 'external'
}

export interface ClosedRow {
  id: string
  pos_id: string
  inst_id: string
  asset_class: string | null
  side: 'long' | 'short'
  pos_side: string | null
  margin_mode: string | null
  close_type: string | null
  leverage: string | null
  ccy: string | null
  size: string
  entry: string | null
  exit: string | null
  realised_pnl: string | null
  pnl: string | null
  fees: string | null
  funding: string | null
  liq_penalty: string | null
  r: string | null
  outcome: 'win' | 'loss' | 'flat' | null
  opened_at: string | null
  closed_at: string
  proposal_id: string | null
  agent: string | null
  setup_tag: string | null
  origin: 'atlas' | 'external'
}

export interface HistoryPage<T> {
  data: T[]
  meta: { type: HistoryType; per_page: number; next_cursor: string | null; prev_cursor: string | null }
}

export const RANGE_PRESETS = ['today', '7d', '30d', 'all'] as const
export type RangePreset = (typeof RANGE_PRESETS)[number] | 'custom'

export const ASSET_CLASSES = ['crypto', 'stock', 'etf', 'index', 'commodity', 'other'] as const
/** Closed positions and fills are linked to the proposal's author: only these two agents write proposals. */
export const HISTORY_AGENTS = ['strategist', 'portfolio_manager'] as const
/** TechnicalSetup.setup_type values (brief §7.4) — what `positions_closed.setup_tag` will carry. */
export const STRATEGY_TAGS = [
  'breakout',
  'pullback_to_support',
  'breakdown',
  'rally_to_resistance',
  'range_fade',
  'squeeze_expansion',
  'reversal_divergence',
] as const

export interface HistoryFilters {
  /** An exact OKX instId, or '' for every instrument. */
  inst: string
  assetClass: string
  agent: string
  tag: string
  range: RangePreset
  /** Y-m-d Baghdad days, only for range = custom. */
  from: string | null
  to: string | null
  side: 'all' | 'buy' | 'sell'
  result: 'all' | 'win' | 'loss'
}

export const BLANK_FILTERS: HistoryFilters = {
  inst: '',
  assetClass: 'all',
  agent: 'all',
  tag: 'all',
  range: 'all',
  from: null,
  to: null,
  side: 'all',
  result: 'all',
}

/** ATL-172 timestamps keep microseconds ("…:00.123456Z"); trim to milliseconds before Date reads them. */
export function isoMs(ts: string): number {
  return Date.parse(ts.replace(/(\.\d{3})\d+/, '$1'))
}

const DAY = 86_400_000
/** Baghdad is UTC+3 all year (no DST since 2008), the same rule as time.ts. */
const BAGHDAD_OFFSET = 3 * 3_600_000

/** The Baghdad calendar day of an instant, as Y-m-d. */
export function baghdadDay(ms: number): string {
  return new Date(ms + BAGHDAD_OFFSET).toISOString().slice(0, 10)
}

/** The inclusive Baghdad days a range covers, or null for all time. */
export function rangeDays(f: Pick<HistoryFilters, 'range' | 'from' | 'to'>, now: number): { from: string; to: string } | null {
  const today = baghdadDay(now)
  switch (f.range) {
    case 'today':
      return { from: today, to: today }
    case '7d':
      return { from: baghdadDay(now - 6 * DAY), to: today }
    case '30d':
      return { from: baghdadDay(now - 29 * DAY), to: today }
    case 'custom':
      return f.from && f.to ? { from: f.from, to: f.to } : null
    default:
      return null
  }
}

/** The filters as ATL-172 query parameters. `strategy_tag` exists for closed positions only (fills answer 422). */
export function filterParams(type: HistoryType, f: HistoryFilters, env: TradingEnv, now: number): URLSearchParams {
  const p = new URLSearchParams()
  p.set('env', env)
  if (f.inst.trim()) p.set('inst_id', f.inst.trim().toUpperCase())
  if (f.assetClass !== 'all') p.set('asset_class', f.assetClass)
  if (f.agent !== 'all') p.set('agent', f.agent)
  if (type === 'closed' && f.tag !== 'all') p.set('strategy_tag', f.tag)
  const days = rangeDays(f, now)
  if (days) {
    p.set('from', days.from)
    p.set('to', days.to)
  }
  if (f.side !== 'all') p.set('side', f.side)
  if (f.result !== 'all') p.set('win_loss', f.result)
  return p
}

export const HISTORY_PAGE_SIZE = 200

export function historyPath(type: HistoryType, f: HistoryFilters, env: TradingEnv, now: number, cursor?: string | null): string {
  const p = filterParams(type, f, env, now)
  p.set('type', type)
  p.set('limit', String(HISTORY_PAGE_SIZE))
  if (cursor) p.set('cursor', cursor)
  return `/api/portfolio/history?${p.toString()}`
}

/** The CSV export of exactly what the filters show (absolute: it is fetched outside apiFetch). */
export function exportUrl(type: HistoryType, f: HistoryFilters, env: TradingEnv, now: number): string {
  return `${API_URL}/api/exports/${type}.csv?${filterParams(type, f, env, now).toString()}`
}

/** Every page loaded so far, newest first; the table asks for the next one as it scrolls near the end. */
export function useHistory<T extends FillRow | ClosedRow>(type: HistoryType, f: HistoryFilters, env: TradingEnv, now: number) {
  const params = filterParams(type, f, env, now).toString()
  return useInfiniteQuery<HistoryPage<T>, ApiError>({
    queryKey: ['portfolio', env, 'history', type, params],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.get<HistoryPage<T>>(historyPath(type, f, env, now, pageParam as string | null), { signal }),
    getNextPageParam: (last) => last.meta.next_cursor ?? undefined,
    // Keep the old rows on screen while a filter changes, never across the tab switch: a fill is not a closed position.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[3] === type ? keepPreviousData(prev) : undefined),
  })
}

/** Data rows in a CSV body (quote-aware: a newline inside quotes is not a row), excluding the header. */
export function csvRowCount(text: string): number {
  let rows = 0
  let quoted = false
  let any = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '"') quoted = !quoted
    if (c === '\n' && !quoted) {
      if (any) rows++
      any = false
    } else if (c !== '\r') any = true
  }
  if (any) rows++
  return Math.max(0, rows - 1)
}

/** Downloads the export with the session cookie; resolves with the file name and the number of rows written. */
export async function downloadExport(url: string, fallbackName: string): Promise<{ rows: number; file: string }> {
  let res: Response
  try {
    res = await fetch(url, { credentials: 'include', headers: { Accept: 'text/csv' } })
  } catch {
    throw new ApiError(0, 'Network error')
  }
  if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`)
  const text = await res.text()
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? fallbackName
  const href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 0)
  return { rows: csvRowCount(text), file: name }
}
