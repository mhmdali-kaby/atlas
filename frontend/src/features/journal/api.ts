import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { baghdadDay } from '../warroom/time'

/*
 * The Journal (ATL-202) over ATL-200's read-only API (docs/handoff/ATL-200-journal-scores-api.md):
 * GET /api/journal (cursor pages, newest first, active env) and GET /api/journal/{id} (404 for an
 * entry of the other env). `data` is the PM post-mortem's jsonb (docs/handoff/ATL-194-post-mortems.md);
 * every number in it is a decimal string. Calendar filters are Baghdad days, inclusive.
 */

export interface JournalVote {
  agent: string
  vote: 'take' | 'change' | 'skip' | string
  confidence?: string | null
  reason?: string | null
}

export interface JournalTakeProfit {
  price: string
  pct?: string | null
}

export interface JournalExpected {
  entry?: string | null
  entry_zone?: [string, string] | string[] | null
  stop?: string | null
  take_profits?: JournalTakeProfit[] | null
  expected_r?: string | null
  probability?: string | null
  invalidation?: string | null
  time_stop_minutes?: number | null
  author?: string | null
}

export interface JournalActual {
  avg_entry?: string | null
  avg_exit?: string | null
  fees_usd?: string | null
  funding_usd?: string | null
  realised_pnl_usd?: string | null
  initial_risk_usd?: string | null
  realised_r?: string | null
  mfe_r?: string | null
  mae_r?: string | null
  opened_at?: string | null
  closed_at?: string | null
  holding_minutes?: number | null
  close_reason?: string | null
}

/** journal_entries.data as ATL-194 writes it; every key is optional (older or hand-written rows). */
export interface JournalData {
  side?: 'long' | 'short' | null
  asset_class?: string | null
  outcome?: 'win' | 'loss' | 'scratch' | null
  realised_r?: string | null
  expected?: JournalExpected | null
  actual?: JournalActual | null
  votes?: JournalVote[] | null
  agents?: string[] | null
  review?: { right_md?: string | null; wrong_md?: string | null } | null
  lesson?: string | null
  lesson_pending?: boolean | null
  lesson_attempts?: number | null
  da_rating?: number | string | null
  [key: string]: unknown
}

export interface JournalEntry {
  id: string
  env: TradingEnv
  title: string | null
  inst_id: string | null
  agent: string | null
  proposal_id: string | null
  expected_md: string | null
  actual_md: string | null
  lessons_md: string | null
  tags: string[]
  data: JournalData
  created_at: string
}

export interface JournalPage {
  data: JournalEntry[]
  meta: { env: TradingEnv; per_page: number; next_cursor: string | null; prev_cursor: string | null }
}

export type Outcome = 'win' | 'loss' | 'scratch'
export type RangePreset = 'all' | '7d' | '30d' | 'custom'

/** The filters, exactly as the URL holds them (design §7 "URL"). */
export interface JournalFilters {
  inst: string
  agent: string
  tag: string
  outcome: Outcome | 'all'
  range: RangePreset
  from: string
  to: string
}

export const BLANK: JournalFilters = { inst: '', agent: 'all', tag: 'all', outcome: 'all', range: 'all', from: '', to: '' }

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/
const OUTCOMES: readonly string[] = ['win', 'loss', 'scratch']
const RANGES: readonly string[] = ['all', '7d', '30d', 'custom']

/** Reads the filters from the query string; anything malformed falls back to "all". */
export function readFilters(search: URLSearchParams): JournalFilters {
  const outcome = search.get('outcome') ?? 'all'
  let range = (search.get('range') ?? 'all') as RangePreset
  if (!RANGES.includes(range)) range = 'all'
  const from = search.get('from') ?? ''
  const to = search.get('to') ?? ''
  if (range === 'custom' && !(DAY_RE.test(from) && DAY_RE.test(to) && from <= to)) range = 'all'
  return {
    inst: (search.get('inst') ?? '').trim().toUpperCase(),
    agent: search.get('agent') || 'all',
    tag: search.get('tag') || 'all',
    outcome: OUTCOMES.includes(outcome) ? (outcome as Outcome) : 'all',
    range,
    from: range === 'custom' ? from : '',
    to: range === 'custom' ? to : '',
  }
}

/** The filters as URL keys, leaving out every "all" (the open entry is added by the caller). */
export function filterQuery(f: JournalFilters): URLSearchParams {
  const q = new URLSearchParams()
  if (f.inst) q.set('inst', f.inst)
  if (f.agent !== 'all') q.set('agent', f.agent)
  if (f.tag !== 'all') q.set('tag', f.tag)
  if (f.outcome !== 'all') q.set('outcome', f.outcome)
  if (f.range !== 'all') q.set('range', f.range)
  if (f.range === 'custom') {
    q.set('from', f.from)
    q.set('to', f.to)
  }
  return q
}

export function isFiltered(f: JournalFilters): boolean {
  return f.inst !== '' || f.agent !== 'all' || f.tag !== 'all' || f.outcome !== 'all' || f.range !== 'all'
}

const DAY = 86_400_000

/** The inclusive Baghdad days a range covers, or null for all time. */
export function rangeDays(f: Pick<JournalFilters, 'range' | 'from' | 'to'>, now: number): { from: string; to: string } | null {
  const today = baghdadDay(now)
  if (f.range === '7d') return { from: baghdadDay(now - 6 * DAY), to: today }
  if (f.range === '30d') return { from: baghdadDay(now - 29 * DAY), to: today }
  if (f.range === 'custom' && f.from && f.to) return { from: f.from, to: f.to }
  return null
}

/** ATL-200's query parameters for these filters. */
export function apiParams(f: JournalFilters, now: number): URLSearchParams {
  const p = new URLSearchParams()
  if (f.inst) p.set('inst_id', f.inst)
  if (f.agent !== 'all') p.set('agent', f.agent)
  if (f.tag !== 'all') p.set('tag', f.tag)
  if (f.outcome !== 'all') p.set('outcome', f.outcome)
  const days = rangeDays(f, now)
  if (days) {
    p.set('from', days.from)
    p.set('to', days.to)
  }
  return p
}

/** "Infinite scroll loads 12 at a time" (design §7). */
export const PAGE_SIZE = 12

export function useJournal(f: JournalFilters, env: TradingEnv, now: number) {
  const params = apiParams(f, now)
  params.set('limit', String(PAGE_SIZE))
  const key = params.toString()
  return useInfiniteQuery<JournalPage, ApiError>({
    queryKey: ['journal', env, 'list', key],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => {
      const q = new URLSearchParams(key)
      if (pageParam) q.set('cursor', pageParam as string)
      return api.get<JournalPage>(`/api/journal?${q.toString()}`, { signal })
    },
    getNextPageParam: (last) => last.meta.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  })
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function useJournalEntry(id: string | null, env: TradingEnv) {
  return useQuery<JournalEntry | null, ApiError>({
    queryKey: ['journal', env, 'entry', id],
    enabled: Boolean(id),
    // Not a UUID: ATL-200 answers 404 anyway, so skip the request and show "not found".
    queryFn: async ({ signal }) => {
      if (!id || !UUID_RE.test(id)) return null
      try {
        const res = await api.get<{ data: JournalEntry }>(`/api/journal/${encodeURIComponent(id)}`, { signal })
        return res.data
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
  })
}
