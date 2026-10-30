import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { api, ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import type { WarroomMessage } from './api'

/*
 * Market Scout's watchlist (design v9 "Watchlist and news briefs" §A8).
 * - The card is the `watchlist` message's `card` (docs/handoff/ATL-108-market-scout.md, ATL-109
 *   re-runs, ATL-120 degraded_since/cap_reason). It is what the Scout published.
 * - The day's state lives in ATL-103's API: GET /api/watchlists returns the highest version for
 *   the Baghdad date with its items and removed_items (docs/handoff/ATL-103-watchlist-api.md).
 *   The card renders from the API when it is the current version, so the owner's edits show.
 * `date` stays a "YYYY-MM-DD" string end to end: it is a calendar day, never an instant.
 */

export type Bias = 'long' | 'short' | 'neutral'
export type CapReason = 'partial_evidence' | 'news_class_down'
export type WlStatus = 'published' | 'partial_evidence' | 'empty'

export interface Catalyst {
  name: string
  time: string
  importance: number
}

export interface Pick {
  /** watchlist_items.id, when the API row is known. */
  itemId: string | null
  instId: string
  rank: number
  assetClass: string
  bias: Bias
  conviction: number
  convictionBeforeCap: number | null
  capReason: CapReason | null
  thesis: string
  catalysts: Catalyst[]
  levels: { support: number | null; resistance: number | null; invalidation: number | null }
  invalidation: string
  pinned: boolean
  removedAt: string | null
  /** An owner pin that no Scout run has analysed yet (ATL-103's placeholder row). */
  placeholder: boolean
}

export type Trigger =
  | { kind: 'news_alert'; messageId: string | null; headline: string; importance: number; at: string }
  | { kind: 'vol_spike'; instId: string; sigma: number; at: string }

export interface PartialEvidence {
  /** Teammates that did not answer: agent slug + how. */
  teammates: Array<{ agent: string; status: 'timed_out' | 'not_asked' }>
  staleMinutes: number | null
}

export interface WatchlistCard {
  watchlistId: string
  version: number
  date: string
  status: WlStatus
  createdAt: string | null
  editEndsAt: string | null
  picks: Pick[]
  backdrop: string[]
  rejected: Array<{ instId: string; reason: string }>
  degradedClasses: string[]
  degradedSince: Record<string, string | null>
  partial: PartialEvidence | null
  shortlistCount: number | null
  /** True when nothing passed the universe filter (no model run, no backdrop). */
  emptyShortlist: boolean
  trigger: Trigger | null
  changes: { since: string | null; added: string[]; dropped: string[] } | null
  dataAsOf: string | null
}

// ------------------------------------------------------------------------------------ readers

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const ts = (v: unknown): string | null => {
  const s = str(v)
  return s && !Number.isNaN(Date.parse(s)) ? s : null
}
/** A finite number from a number or a decimal string ("0.7100"). */
export const num = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  return Number.isFinite(n) ? n : null
}
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.trim() !== '') : [])
const BIASES = new Set<Bias>(['long', 'short', 'neutral'])
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function readCatalysts(v: unknown): Catalyst[] {
  if (!Array.isArray(v)) return []
  return v.flatMap((c) => {
    if (!isObj(c)) return []
    const name = str(c.name)
    const time = ts(c.time)
    if (!name || !time) return []
    const imp = num(c.importance) ?? 1
    return [{ name, time, importance: Math.max(1, Math.min(5, Math.round(imp))) }]
  })
}

/** One pick from the card or one item from the API (same field names, conviction number or string). */
export function readPick(p: unknown): Pick | null {
  if (!isObj(p)) return null
  const instId = str(p.inst_id)
  if (!instId) return null
  const kl = isObj(p.key_levels) ? p.key_levels : {}
  const conviction = Math.max(0, Math.min(1, num(p.conviction) ?? 0))
  const before = num(p.conviction_before_cap)
  const capReason = p.cap_reason === 'partial_evidence' || p.cap_reason === 'news_class_down' ? p.cap_reason : null
  const catalysts = readCatalysts(p.catalysts)
  const pinned = p.pinned_by_owner === true
  const bias = BIASES.has(p.bias as Bias) ? (p.bias as Bias) : 'neutral'
  return {
    itemId: str(p.id),
    instId,
    rank: num(p.rank) ?? 0,
    assetClass: str(p.asset_class) ?? 'other',
    bias,
    conviction,
    convictionBeforeCap: before !== null && capReason ? before : null,
    capReason: before !== null ? capReason : null,
    thesis: str(p.thesis) ?? '',
    catalysts,
    levels: { support: num(kl.support), resistance: num(kl.resistance), invalidation: num(kl.invalidation) },
    invalidation: str(p.invalidation) ?? '',
    pinned,
    removedAt: ts(p.removed_by_owner_at),
    // ATL-103: a fresh pin is neutral, 0 conviction, no catalysts, "Pinned by the owner.".
    placeholder: pinned && conviction === 0 && catalysts.length === 0 && (p.analysed === false || bias === 'neutral'),
  }
}

function readTrigger(v: unknown): Trigger | null {
  // On 08:00 and @Scout cards `trigger` is a string ("schedule", "mention"): not a re-run.
  if (!isObj(v)) return null
  const at = ts(v.at)
  if (!at) return null
  if (v.kind === 'news_alert') {
    return { kind: 'news_alert', messageId: str(v.message_id), headline: str(v.headline) ?? '', importance: Math.round(num(v.importance) ?? 5), at }
  }
  // ATL-109 builds `vol_spike`; the design called it `volatility_spike`.
  if (v.kind === 'vol_spike' || v.kind === 'volatility_spike') {
    const instId = str(v.inst_id)
    const sigma = num(v.sigma)
    return instId && sigma !== null ? { kind: 'vol_spike', instId, sigma, at } : null
  }
  return null
}

function readPartial(card: Obj, status: WlStatus): PartialEvidence | null {
  if (status !== 'partial_evidence') return null
  const ev = isObj(card.evidence) ? card.evidence : {}
  const teammates = (Array.isArray(ev.teammates) ? ev.teammates : []).flatMap((tm) => {
    if (!isObj(tm)) return []
    const agent = str(tm.agent)
    const status: PartialEvidence['teammates'][number]['status'] | null = tm.status === 'timed_out' || tm.status === 'not_asked' ? tm.status : null
    return agent && status ? [{ agent, status }] : []
  })
  const age = num(ev.feature_table_age_minutes)
  return { teammates, staleMinutes: ev.feature_table_stale === true && age !== null ? Math.round(age) : null }
}

/** The watchlist card of a `watchlist` message, or null when it is not one (GenericCard then). */
export function readWatchlistCard(card: WarroomMessage['card']): WatchlistCard | null {
  if (!isObj(card) || card.type !== 'watchlist') return null
  const watchlistId = str(card.watchlist_id)
  const date = str(card.date)
  if (!watchlistId || !date || !DATE_RE.test(date)) return null
  const status: WlStatus = card.status === 'partial_evidence' || card.status === 'empty' ? card.status : 'published'
  const picks = (Array.isArray(card.picks) ? card.picks : []).map(readPick).filter((p): p is Pick => p !== null)
  picks.sort((a, b) => a.rank - b.rank)
  const rejected = (Array.isArray(card.rejected) ? card.rejected : []).flatMap((r) => {
    if (!isObj(r)) return []
    const instId = str(r.inst_id)
    return instId ? [{ instId, reason: str(r.reason) ?? '' }] : []
  })
  const since: Record<string, string | null> = {}
  if (isObj(card.degraded_since)) for (const [k, v] of Object.entries(card.degraded_since)) since[k] = ts(v)
  const backdrop = strs(card.backdrop)
  const ch = isObj(card.changes) ? card.changes : null
  // The newest data any pick (or the feature table) was built on; ISO-Z strings sort by time.
  const asOfs = [card.feature_table_as_of, ...(Array.isArray(card.picks) ? card.picks : []).map((p) => (isObj(p) ? p.data_as_of : null))]
  const dataAsOf =
    asOfs
      .map(ts)
      .filter((x): x is string => x !== null)
      .sort((x, y) => Date.parse(x) - Date.parse(y))
      .at(-1) ?? null
  return {
    watchlistId,
    version: Math.max(1, Math.round(num(card.version) ?? 1)),
    date,
    status,
    createdAt: ts(card.created_at),
    editEndsAt: ts(card.edit_window_ends_at),
    picks,
    backdrop,
    rejected,
    degradedClasses: strs(card.degraded_asset_classes),
    degradedSince: since,
    partial: readPartial(card, status),
    shortlistCount: Array.isArray(card.shortlist) ? card.shortlist.length : null,
    emptyShortlist: status === 'empty' && backdrop.length === 0 && rejected.length === 0,
    trigger: readTrigger(card.trigger),
    changes: ch ? { since: ts(ch.since), added: strs(ch.added), dropped: strs(ch.dropped) } : null,
    dataAsOf,
  }
}

// ------------------------------------------------------------------------------------- the API

export interface ApiWatchlist {
  id: string
  date: string
  version: number
  status: WlStatus
  created_at: string
  edit_window_ends_at: string | null
  edit_window_open: boolean
  message_id: string | null
  items: unknown[]
  removed_items: unknown[]
}

export interface WatchlistDay {
  env: TradingEnv
  date: string
  watchlist: ApiWatchlist | null
}

export const watchlistKey = (env: TradingEnv, date: string) => ['watchlists', env, date] as const

export function useWatchlistDay(env: TradingEnv, date: string) {
  return useQuery<WatchlistDay, ApiError>({
    queryKey: watchlistKey(env, date),
    queryFn: ({ signal }) => api.get<WatchlistDay>(`/api/watchlists?date=${encodeURIComponent(date)}&env=${env}`, { signal }),
    staleTime: 15_000,
    retry: 1,
  })
}

/** The day's rows as the API has them: visible items and removed ones, by rank. */
export function apiRows(w: ApiWatchlist): Pick[] {
  const rows = [...w.items, ...w.removed_items].map(readPick).filter((p): p is Pick => p !== null)
  return rows.sort((a, b) => a.rank - b.rank)
}

/** Refetches every cached watchlist day of the env (a `watchlist.updated` or a new card arrived). */
export function invalidateWatchlists(qc: QueryClient, env: TradingEnv): Promise<void> {
  return qc.invalidateQueries({ queryKey: ['watchlists', env] })
}

export type WriteKind = 'remove' | 'restore' | 'pin'
export type WriteResult =
  | { ok: true; watchlist: ApiWatchlist }
  | { ok: false; status: number; error: 'edit_window_closed' | 'not_current_version' | 'invalid' | 'other'; field?: string }

/** POSTs one edit and puts the returned watchlist into the cache (every write answers with it). */
export function useWatchlistWrite(env: TradingEnv, date: string) {
  const qc = useQueryClient()
  return async (kind: WriteKind, watchlistId: string, target: { itemId?: string; instId?: string }): Promise<WriteResult> => {
    const base = `/api/watchlists/${encodeURIComponent(watchlistId)}`
    const path = kind === 'pin' ? `${base}/pin` : `${base}/items/${encodeURIComponent(target.itemId ?? '')}/${kind}`
    try {
      const res = await api.post<{ watchlist: ApiWatchlist }>(path, kind === 'pin' ? { inst_id: target.instId } : undefined)
      qc.setQueryData<WatchlistDay>(watchlistKey(env, date), (d) => (d ? { ...d, watchlist: res.watchlist } : { env, date, watchlist: res.watchlist }))
      return { ok: true, watchlist: res.watchlist }
    } catch (e) {
      if (!(e instanceof ApiError)) return { ok: false, status: 0, error: 'other' }
      const code = isObj(e.body) ? e.body.error : null
      if (e.status === 409 && (code === 'edit_window_closed' || code === 'not_current_version')) {
        void qc.invalidateQueries({ queryKey: watchlistKey(env, date) })
        return { ok: false, status: 409, error: code }
      }
      if (e.status === 422) return { ok: false, status: 422, error: 'invalid', field: e.fieldErrors.inst_id?.[0] ?? '' }
      return { ok: false, status: e.status, error: 'other' }
    }
  }
}

export interface InstrumentHit {
  inst_id: string
  asset_class: string
  blocked: boolean
}

/** ATL-119's search: live instruments of the env whose id or base starts with `q`. */
export function useInstrumentSearch(env: TradingEnv, q: string) {
  return useQuery<{ data: InstrumentHit[] }, ApiError>({
    queryKey: ['instruments', env, q],
    enabled: q.trim() !== '',
    queryFn: ({ signal }) => api.get(`/api/instruments?q=${encodeURIComponent(q.trim())}&env=${env}&limit=20`, { signal }),
    staleTime: 60_000,
    retry: false,
  })
}

// ------------------------------------------------------------------------------- conflicts (409)

export interface Conflict {
  kind: WriteKind
  instId: string
  error: 'edit_window_closed' | 'not_current_version'
}

/** A 409 hides the controls for good; kept outside the card so a virtualised remount keeps it. */
export const useWlConflicts = create<{ byId: Record<string, Conflict>; set: (id: string, c: Conflict) => void }>((setState) => ({
  byId: {},
  set: (id, c) => setState((s) => ({ byId: { ...s.byId, [id]: c } })),
}))

// --------------------------------------------------------------------------------- the day rail

const HOUR = 3_600_000

/** 08:00 Baghdad on the watchlist's day (Baghdad is UTC+3 all year, no DST since 2008). */
export const dayStart = (date: string) => Date.parse(`${date}T05:00:00Z`)

function nyOffsetMin(ms: number): number {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      .formatToParts(new Date(ms))
      .map((x) => [x.type, x.value]),
  )
  return (Date.UTC(+p.year!, +p.month! - 1, +p.day!, +p.hour!, +p.minute!) - ms) / 60_000
}
const nyToUtc = (y: number, mo: number, d: number, h: number, mi: number) => {
  const g = Date.UTC(y, mo, d, h, mi)
  return g - nyOffsetMin(g) * 60_000
}

/**
 * New York's regular sessions (09:30–16:00, weekdays) that touch [a, b], from the New York
 * calendar so a DST change moves them; each is [clipped open, clipped close, real open, real close].
 */
export function usSessions(a: number, b: number): Array<[number, number, number, number]> {
  const out: Array<[number, number, number, number]> = []
  for (let k = -1; k <= 2; k++) {
    const day = new Date(a + k * 24 * HOUR)
    const p = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' })
        .formatToParts(day)
        .map((x) => [x.type, x.value]),
    )
    if (p.weekday === 'Sat' || p.weekday === 'Sun') continue
    const o = nyToUtc(+p.year!, +p.month! - 1, +p.day!, 9, 30)
    const c = nyToUtc(+p.year!, +p.month! - 1, +p.day!, 16, 0)
    if (c > a && o < b && !out.some((s) => s[2] === o)) out.push([Math.max(o, a), Math.min(c, b), o, c])
  }
  return out
}

/** "7:12": minutes and seconds left, rounded up. */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export const baseOf = (id: string) => id.split('-')[0] ?? id
/** Perpetuals and dated futures carry the class word alone; spot adds "spot". */
export const isDerivative = (id: string) => /-SWAP$|-\d{6}$/.test(id)
