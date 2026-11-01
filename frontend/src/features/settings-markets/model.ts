import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import type { SettingsChanges, SettingsDoc } from '../onboarding'
import { clean } from '../settings-money/model'

/*
 * Settings → Markets (brief §14.5, ATL-131; design v14 js/pages/settings-markets.js).
 * The draft keeps every number as the text in its field; lists and toggles as values.
 * Keys are ATL-124's flat settings keys. Client checks follow the design's field rules
 * (stricter than the server's ranges); the server's 422 still shows under the field.
 */

export type Env = SettingsDoc['env']

/** `markets.kinds` order (ATL-124): enabled_markets is stored and sent in this order. */
export const KINDS = ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp', 'futures', 'options'] as const
export type Kind = (typeof KINDS)[number]
/** The classes the owner can switch in this phase; futures and options arrive later. */
export const LIVE_KINDS = ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'] as const
export type Market = (typeof LIVE_KINDS)[number]
export const LATER_KINDS = ['futures', 'options'] as const
/** Volume rows, in the design's order. */
export const VOLUME_MARKETS: readonly Market[] = ['crypto_perp', 'stock_perp', 'commodity_perp', 'spot']

export const volKey = (m: Market) => `min_24h_volume_usd_${m}` as const
export type VolKey = ReturnType<typeof volKey>
export type NumKey = VolKey | 'max_spread_bps' | 'max_instruments_per_day'
export type ListKey = 'whitelist' | 'blocklist'
export type FieldKey = NumKey | ListKey | 'enabled_markets' | 'avoid_stock_perps_outside_us_hours'
export const NUM_KEYS: readonly NumKey[] = [...VOLUME_MARKETS.map(volKey), 'max_spread_bps', 'max_instruments_per_day']
export const FIELD_KEYS: readonly FieldKey[] = ['enabled_markets', ...NUM_KEYS, 'avoid_stock_perps_outside_us_hours', 'whitelist', 'blocklist']

export type NumKind = 'usd' | 'bps' | 'int'
export const kindOf = (k: NumKey): NumKind => (k === 'max_spread_bps' ? 'bps' : k === 'max_instruments_per_day' ? 'int' : 'usd')

export const USD_MAX = 100_000_000_000
export const SPREAD_MIN = 1
export const SPREAD_MAX = 100
export const PER_DAY_MIN = 1
export const PER_DAY_MAX = 10
/** GET /api/settings/markets/eligibility answers at most 50 ids (ATL-155). */
export const ELIGIBILITY_MAX = 50

export interface Draft {
  enabled_markets: string[]
  min_24h_volume_usd_spot: string
  min_24h_volume_usd_crypto_perp: string
  min_24h_volume_usd_stock_perp: string
  min_24h_volume_usd_commodity_perp: string
  max_spread_bps: string
  max_instruments_per_day: string
  avoid_stock_perps_outside_us_hours: boolean
  whitelist: string[]
  blocklist: string[]
}

const asList = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => String(x)) : [])

/** "20000000" → "20,000,000"; "12.5" stays; anything that isn't a plain decimal is left as typed. */
export function grouped(text: string): string {
  const s = clean(text)
  if (!/^\d+(\.\d+)?$/.test(s)) return text
  const [i = '', f] = s.split('.')
  const int = i.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return f ? `${int}.${f}` : int
}

/** The decimal string the API stores: no grouping, no leading zeros, no trailing decimal zeros. */
export function canon(text: string): string {
  const s = clean(text)
  if (!/^\d+(\.\d+)?$/.test(s)) return s
  let [i = '', f = ''] = s.split('.')
  i = i.replace(/^0+(?=\d)/, '')
  f = f.replace(/0+$/, '')
  return f ? `${i}.${f}` : i
}

export function draftFrom(doc: SettingsDoc): Draft {
  const s = doc.settings as unknown as Record<string, unknown>
  const text = (k: string) => (s[k] === null || s[k] === undefined ? '' : String(s[k]))
  return {
    enabled_markets: asList(s.enabled_markets),
    min_24h_volume_usd_spot: grouped(text('min_24h_volume_usd_spot')),
    min_24h_volume_usd_crypto_perp: grouped(text('min_24h_volume_usd_crypto_perp')),
    min_24h_volume_usd_stock_perp: grouped(text('min_24h_volume_usd_stock_perp')),
    min_24h_volume_usd_commodity_perp: grouped(text('min_24h_volume_usd_commodity_perp')),
    max_spread_bps: text('max_spread_bps'),
    max_instruments_per_day: text('max_instruments_per_day'),
    avoid_stock_perps_outside_us_hours: s.avoid_stock_perps_outside_us_hours !== false,
    whitelist: asList(s.whitelist),
    blocklist: asList(s.blocklist),
  }
}

export type ErrKey = 'empty' | 'nan' | 'decimals' | 'zero' | 'max' | 'spread' | 'perDay'

/** The locale key (under `err.`) of the client error for this field, or null when it is fine. */
export function numError(key: NumKey, text: string): ErrKey | null {
  const s = clean(text)
  const kind = kindOf(key)
  if (kind === 'int') return /^\d{1,2}$/.test(s) && Number(s) >= PER_DAY_MIN && Number(s) <= PER_DAY_MAX ? null : 'perDay'
  if (kind === 'bps') return /^\d{1,3}(\.\d)?$/.test(s) && Number(s) >= SPREAD_MIN && Number(s) <= SPREAD_MAX ? null : 'spread'
  if (s === '') return 'empty'
  if (!/^\d+(\.\d+)?$/.test(s)) return 'nan'
  if ((s.split('.')[1] ?? '').length > 2) return 'decimals'
  if (Number(s) <= 0) return 'zero'
  if (Number(s) > USD_MAX) return 'max'
  return null
}

export function clientErrors(d: Draft): Partial<Record<NumKey, ErrKey>> {
  const out: Partial<Record<NumKey, ErrKey>> = {}
  for (const k of NUM_KEYS) {
    const e = numError(k, d[k])
    if (e) out[k] = e
  }
  return out
}

const sameList = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i])

/** The value a field sends: lists and toggles as they are, decimals canonical, the count an integer. */
export function apiValue(key: FieldKey, d: Draft): string | number | boolean | string[] {
  if (key === 'max_instruments_per_day') return Number(clean(d[key]))
  if (key === 'enabled_markets' || key === 'whitelist' || key === 'blocklist' || key === 'avoid_stock_perps_outside_us_hours') return d[key]
  return canon(d[key])
}

export function isChanged(key: FieldKey, d: Draft, saved: Draft): boolean {
  if (key === 'enabled_markets' || key === 'whitelist' || key === 'blocklist') return !sameList(d[key], saved[key])
  if (key === 'avoid_stock_perps_outside_us_hours') return d[key] !== saved[key]
  return canon(d[key]) !== canon(saved[key])
}

export const changedKeys = (d: Draft, saved: Draft): FieldKey[] => FIELD_KEYS.filter((k) => isChanged(k, d, saved))

export function changesFor(d: Draft, saved: Draft): SettingsChanges {
  const out: Record<string, unknown> = {}
  for (const k of changedKeys(d, saved)) out[k] = apiValue(k, d)
  return out as SettingsChanges
}

/** A class switched on or off, kept in `markets.kinds` order. */
export function toggled(list: readonly string[], kind: Kind, on: boolean): string[] {
  const set = new Set(list)
  if (on) set.add(kind)
  else set.delete(kind)
  return KINDS.filter((k) => set.has(k))
}

/** One stepper press (design stepValue): ±1 on the count and the spread, two significant digits on USD. */
export function stepped(key: NumKey, text: string, dir: 1 | -1, savedText: string): string {
  const kind = kindOf(key)
  const s = clean(text)
  const v0 = /^\d+(\.\d+)?$/.test(s) ? Number(s) : NaN
  if (kind === 'int') {
    const v = Number.isFinite(v0) ? Math.round(v0) : 5
    return String(Math.min(PER_DAY_MAX, Math.max(PER_DAY_MIN, v + dir)))
  }
  const base = Number.isFinite(v0) && v0 > 0 ? v0 : Number(canon(savedText)) || 1
  if (kind === 'bps') {
    const next = dir > 0 ? Math.floor(base) + 1 : Math.ceil(base) - 1
    return String(Math.min(SPREAD_MAX, Math.max(SPREAD_MIN, next)))
  }
  const step = 10 ** Math.max(0, String(Math.floor(base)).length - 2)
  let next = dir > 0 ? Math.floor(base / step) * step + step : Math.ceil(base / step) * step - step
  next = Math.min(USD_MAX, Math.max(1, next))
  return grouped(String(next))
}

/** Whether the − or + button of this field is at its bound. */
export function atBound(key: NumKey, text: string, dir: 1 | -1): boolean {
  const s = clean(text)
  if (!/^\d+(\.\d+)?$/.test(s)) return false
  const n = Number(s)
  const max = kindOf(key) === 'int' ? PER_DAY_MAX : kindOf(key) === 'bps' ? SPREAD_MAX : USD_MAX
  return dir < 0 ? n <= 1 : n >= max
}

/** 422 `changes.<key>` / `changes.<key>.<i>` messages, by field. */
export function serverErrors(err: unknown): Partial<Record<FieldKey, string>> {
  if (!(err instanceof ApiError) || err.status !== 422) return {}
  const errors = (err.body as { errors?: Record<string, string[]> } | null)?.errors ?? {}
  const out: Partial<Record<FieldKey, string>> = {}
  for (const [key, messages] of Object.entries(errors)) {
    if (!key.startsWith('changes.') || !messages[0]) continue
    const field = key.slice('changes.'.length).split('.')[0] as FieldKey
    if ((FIELD_KEYS as readonly string[]).includes(field)) out[field] ??= messages[0]
  }
  return out
}

// ------------------------------------------------------------------ instruments (ATL-119)

export interface Instrument {
  inst_id: string
  inst_type: string
  asset_class: string
  base_ccy: string | null
  quote_ccy: string | null
  settle_ccy: string | null
  blocked: boolean
}

/** The settings class an instrument belongs to (the eligibility API's `market`). */
export function marketOf(i: Pick<Instrument, 'inst_type' | 'asset_class'>): Kind | null {
  if (i.inst_type === 'SPOT') return 'spot'
  if (i.inst_type === 'FUTURES') return 'futures'
  if (i.inst_type === 'OPTION') return 'options'
  if (i.inst_type !== 'SWAP') return null
  if (i.asset_class === 'crypto') return 'crypto_perp'
  if (i.asset_class === 'commodity') return 'commodity_perp'
  if (i.asset_class === 'stock' || i.asset_class === 'etf' || i.asset_class === 'index') return 'stock_perp'
  return null
}

export const SEARCH_LIMIT = 8
export const searchKey = (env: Env, q: string) => ['instruments', env, 'markets-picker', q.trim().toUpperCase()] as const
export const searchUrl = (env: Env, q: string) => `/api/instruments?q=${encodeURIComponent(q.trim())}&env=${env}&limit=${SEARCH_LIMIT}`

export function useInstrumentSearch(env: Env, q: string) {
  return useQuery<{ data: Instrument[] }, ApiError>({
    queryKey: searchKey(env, q),
    enabled: q.trim() !== '',
    queryFn: ({ signal }) => api.get(searchUrl(env, q), { signal }),
    staleTime: 60_000,
    retry: false,
    placeholderData: (prev) => prev,
  })
}

// ----------------------------------------------------------------- eligibility (ATL-155)

export const FAIL_CODES = [
  'blocked',
  'unknown_instrument',
  'asset_class_disabled',
  'not_live',
  'listed_under_7_days',
  'min_24h_volume',
  'max_spread',
  'no_data',
] as const
export type FailCode = (typeof FAIL_CODES)[number]

export interface Eligibility {
  inst_id: string
  eligible: boolean
  fails: FailCode[]
  market: Kind | 'other' | null
  on_whitelist: boolean
  volume_24h_usd: string | null
  min_24h_volume_usd: string | null
  spread_bps: string | null
  max_spread_bps: string | null
  notes: string[]
}

export interface EligibilityAnswer {
  env: Env
  as_of: string
  settings_version: number | null
  draft: string[]
  instruments: Eligibility[]
}

/** The draft keys the eligibility API previews (ATL-155); only valid, changed ones are sent. */
const DRAFT_KEYS: readonly FieldKey[] = ['enabled_markets', 'whitelist', 'blocklist', 'max_spread_bps', ...VOLUME_MARKETS.map(volKey)]

/** The query string for the whitelist's labels, or null when there is nothing to ask. */
export function eligibilityParams(d: Draft, saved: Draft, env: Env): string | null {
  const ids = d.whitelist.slice(0, ELIGIBILITY_MAX)
  if (ids.length === 0) return null
  const p = new URLSearchParams({ env })
  for (const id of ids) p.append('inst_ids[]', id)
  for (const k of DRAFT_KEYS) {
    if (!isChanged(k, d, saved)) continue
    if ((NUM_KEYS as readonly string[]).includes(k) && numError(k as NumKey, d[k as NumKey])) continue
    const v = apiValue(k, d)
    if (Array.isArray(v)) {
      // An empty list can't be written as [] in a query string: an empty value means [] (ATL-155).
      if (v.length === 0) p.append(`draft[${k}]`, '')
      else for (const x of v) p.append(`draft[${k}][]`, x)
    } else p.append(`draft[${k}]`, String(v))
  }
  return p.toString()
}

export function useEligibility(qs: string | null) {
  return useQuery<{ data: EligibilityAnswer }, ApiError>({
    queryKey: ['settings', 'markets-eligibility', qs ?? ''],
    enabled: qs !== null,
    queryFn: ({ signal }) => api.get(`/api/settings/markets/eligibility?${qs ?? ''}`, { signal }),
    staleTime: 30_000,
    retry: false,
    placeholderData: (prev) => prev,
  })
}

// ------------------------------------------------------------------- US cash session

/**
 * Today's US cash session (09:30–16:00 New York) as UTC instants, DST included: computed from the
 * America/New_York calendar, never a fixed Baghdad time.
 */
export function usSession(now: number): { open: number; close: number; isOpen: boolean } {
  const nyDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now))
  const at = (hh: number, mm: number) => {
    const want = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
    for (const off of [4, 5]) {
      const ts = Date.parse(`${nyDate}T${String(hh + off).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00Z`)
      const shown = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts))
      if (shown === want) return ts
    }
    return Date.parse(`${nyDate}T${String(hh + 5).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00Z`)
  }
  const open = at(9, 30)
  const close = at(16, 0)
  // No cash session on a New York Saturday or Sunday (exchange holidays are not modelled).
  const weekday = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short' }).format(new Date(now))
  const weekend = weekday === 'Sat' || weekday === 'Sun'
  return { open, close, isOpen: !weekend && now >= open && now < close }
}

/** "$3.1M", "$20M", "$4.2B": short USD for warnings. */
export function usdShort(v: string | number): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const one = (x: number, u: string) => (x < 10 && Math.round(x * 10) % 10 ? (Math.round(x * 10) / 10).toFixed(1) : String(Math.round(x))) + u
  if (n >= 1e9) return `$${one(n / 1e9, 'B')}`
  if (n >= 1e6) return `$${one(n / 1e6, 'M')}`
  if (n >= 1e3) return `$${one(n / 1e3, 'K')}`
  return `$${Math.round(n)}`
}
