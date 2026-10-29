/*
 * Settings → Data sources: the rules the client applies (design "Data sources and news alerts" §7.2).
 * Pure functions over the API's report, so they are unit-tested without a browser.
 */

export const CATEGORIES = ['exchange', 'crypto_news', 'markets_news', 'calendar', 'earnings', 'sentiment'] as const
export type Category = (typeof CATEGORIES)[number]

/** Sources that write headlines, so they draw a strip and feed the wire. */
export const FEED_CATEGORIES: ReadonlySet<string> = new Set(['exchange', 'crypto_news', 'markets_news'])
/** The kinds that get an outage notice when every source is down. */
export const NOTICE_CATEGORIES = ['crypto_news', 'markets_news', 'calendar'] as const
export type NoticeCategory = (typeof NOTICE_CATEGORIES)[number]

export type ApiStatus = 'ok' | 'degraded' | 'needs_key' | 'disabled' | 'never_run'

export interface ApiSource {
  key: string
  provider: string
  name: string
  kind: string
  category: string
  url: string | null
  enabled: boolean
  requires_key: boolean
  has_key: boolean
  key_last4: string | null
  poll_seconds: number
  status: ApiStatus
  consecutive_failures: number
  disabled_until: string | null
  last_success_at: string | null
  last_item_at: string | null
  last_error: string | null
  /** 24 counts, oldest first; the last is the current (partial) UTC hour. */
  hourly_24h: number[] | null
  covered_by: string | null
  updated_at?: string | null
}

export interface CategorySummary {
  total: number
  available: number
  all_down: boolean
}

export interface SourcesReport {
  data: ApiSource[]
  summary: Partial<Record<Category, CategorySummary>>
}

export type RowStatus = 'needs_key' | 'off' | 'standby' | 'retrying' | 'starting' | 'failing' | 'never_run' | 'working'

export const MIN = 60_000
export const HOUR = 3_600_000

const ts = (v: string | null | undefined): number | null => {
  if (!v) return null
  const n = Date.parse(v)
  return Number.isFinite(n) ? n : null
}

export const lastSuccess = (s: ApiSource) => ts(s.last_success_at)
export const disabledUntil = (s: ApiSource) => ts(s.disabled_until)
export const lastItem = (s: ApiSource) => ts(s.last_item_at)

/** When the owner turned a source on in this page visit (component state), by source key. */
export type StartedAt = Readonly<Record<string, number>>

/** Row status precedence (design §7.2). */
export function rowStatus(s: ApiSource, now: number, startedAt: StartedAt = {}): RowStatus {
  if (s.requires_key && !s.has_key) return 'needs_key'
  if (!s.enabled) return 'off'
  if (s.covered_by) return 'standby'
  const until = disabledUntil(s)
  if (until !== null && until > now) return 'retrying'
  const started = startedAt[s.key]
  const success = lastSuccess(s)
  if (started !== undefined && !(success !== null && success > started)) return 'starting'
  if (s.consecutive_failures > 0) return 'failing'
  if (success === null) return 'never_run'
  return 'working'
}

/** The API's own status for a row (ATL-66 precedence), used when the client edits the cache. */
export function apiStatus(s: ApiSource, now: number): ApiStatus {
  if (!s.enabled) return 'disabled'
  if (s.requires_key && !s.has_key) return 'needs_key'
  const until = disabledUntil(s)
  if (until !== null && until > now) return 'degraded'
  if (!s.last_success_at && !s.last_error && s.consecutive_failures === 0) return 'never_run'
  return 'ok'
}

const isDown = (st: ApiStatus) => st === 'disabled' || st === 'needs_key' || st === 'degraded'

/** The API's summary, recomputed from rows (ATL-66: all_down when no source in the category is up). */
export function summarise(rows: ApiSource[]): SourcesReport['summary'] {
  const out: SourcesReport['summary'] = {}
  for (const cat of CATEGORIES) {
    const list = rows.filter((s) => s.category === cat)
    const available = list.filter((s) => !isDown(s.status)).length
    out[cat] = { total: list.length, available, all_down: available === 0 }
  }
  return out
}

/**
 * The optimistic toggle: flips `enabled`, re-derives the API status and the summary, and (turning
 * off a coverer such as finnhub.calendar) releases the sources it covered.
 */
export function withEnabled(report: SourcesReport, key: string, enabled: boolean, now: number): SourcesReport {
  const data = report.data.map((s) => {
    if (s.key === key) {
      const next = { ...s, enabled }
      return { ...next, status: apiStatus(next, now) }
    }
    if (!enabled && s.covered_by === key) return { ...s, covered_by: null }
    return s
  })
  return { data, summary: summarise(data) }
}

/** Keys the client should show as Starting after a toggle: the source turned on, and any it released. */
export function startedBy(report: SourcesReport, key: string, enabled: boolean): string[] {
  if (enabled) return [key]
  return report.data.filter((s) => s.covered_by === key && s.enabled).map((s) => s.key)
}

export const isFeed = (s: ApiSource) => FEED_CATEGORIES.has(s.category) && Array.isArray(s.hourly_24h)
export const sum24 = (s: ApiSource) => (isFeed(s) ? s.hourly_24h!.reduce((a, v) => a + v, 0) : 0)

/** Groups in the design's fixed order; only groups that have sources; rows by the API name. */
export function groups(rows: ApiSource[]): { cat: Category; rows: ApiSource[] }[] {
  return CATEGORIES.map((cat) => ({
    cat,
    rows: rows.filter((s) => s.category === cat).sort((a, b) => a.name.localeCompare(b.name, 'en') || a.key.localeCompare(b.key)),
  })).filter((g) => g.rows.length > 0)
}

/* ---- providers ---- */

/** Display names for keyed providers (brand names stay Latin in Arabic). */
export const PROVIDER_NAMES: Readonly<Record<string, string>> = { cryptopanic: 'CryptoPanic', finnhub: 'Finnhub' }
/** Where a free key comes from, shown on a Needs a key row. */
export const PROVIDER_SITES: Readonly<Record<string, string>> = { cryptopanic: 'cryptopanic.com', finnhub: 'finnhub.io' }

const MONOGRAMS: Readonly<Record<string, string>> = {
  okx: 'OKX',
  cryptopanic: 'CP',
  finnhub: 'FH',
  coindesk: 'CD',
  cointelegraph: 'CT',
  decrypt: 'DE',
  theblock: 'TB',
  cnbc: 'CN',
  marketwatch: 'MW',
  yahoo: 'YF',
  forexfactory: 'FF',
  alternative_me: 'FG',
}

/** Sources share their provider's monogram (FH on all three Finnhub sources). */
export function monogram(s: Pick<ApiSource, 'provider' | 'name'>): string {
  return MONOGRAMS[s.provider] ?? (s.name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '··')
}

export const providerName = (p: string) => PROVIDER_NAMES[p] ?? p

/** Providers whose keyed sources have no key, in first-seen order. */
export function missingKeyProviders(rows: ApiSource[]): string[] {
  const out: string[] = []
  for (const s of rows) if (s.requires_key && !s.has_key && !out.includes(s.provider)) out.push(s.provider)
  return out
}

/* ---- summary ---- */

export type SummaryState = 'healthy' | 'trouble' | 'noneWorking' | 'firstRun' | 'stalled' | 'allOff'

export interface Row {
  s: ApiSource
  st: RowStatus
}

export interface Summary {
  rows: Row[]
  state: SummaryState
  trouble: Row[]
  down: NoticeCategory[]
  /** The newest check among the fast-polling working feeds (stalled minutes count from it). */
  newestCheck: number | null
  /** The latest last_item_at. */
  newest: number | null
  missingKeys: string[]
}

/** Feeds poll every 2–5 min, so a stopped collector shows there first (design §7.2). */
const FAST_POLL_SECONDS = 300

export function summary(report: SourcesReport, now: number, startedAt: StartedAt = {}): Summary {
  const rows = report.data.map((s) => ({ s, st: rowStatus(s, now, startedAt) }))
  const usable = rows.filter((x) => x.st !== 'off' && x.st !== 'needs_key')
  const trouble = usable.filter((x) => x.st === 'retrying' || x.st === 'failing')
  const fine = usable.filter((x) => x.st === 'working' || x.st === 'standby')
  const pending = usable.filter((x) => x.st === 'never_run' || x.st === 'starting')
  const fast = usable.filter((x) => x.st === 'working' && x.s.poll_seconds <= FAST_POLL_SECONDS)
  const stalled =
    fast.length > 0 && fast.every((x) => now - (lastSuccess(x.s) ?? 0) > 3 * x.s.poll_seconds * 1000 + 60_000)
  let state: SummaryState = 'healthy'
  if (usable.length === 0) state = 'allOff'
  else if (stalled) state = 'stalled'
  else if (pending.length === usable.length) state = 'firstRun'
  else if (trouble.length > 0 && fine.length === 0 && pending.length === 0) state = 'noneWorking'
  else if (trouble.length > 0) state = 'trouble'
  const down =
    state === 'allOff' || state === 'stalled'
      ? []
      : NOTICE_CATEGORIES.filter((cat) => report.summary[cat]?.all_down === true && rows.some((x) => x.s.category === cat))
  const checks = fast.map((x) => lastSuccess(x.s)!).filter((v) => v !== null)
  const items = report.data.map(lastItem).filter((v): v is number => v !== null)
  return {
    rows,
    state,
    trouble,
    down,
    newestCheck: checks.length ? Math.max(...checks) : null,
    newest: items.length ? Math.max(...items) : null,
    missingKeys: missingKeyProviders(report.data),
  }
}

/** Counts of each cause behind a down kind's notice, and the earliest retry. */
export function downCauses(rows: Row[], cat: string, now: number) {
  const list = rows.filter((x) => x.s.category === cat)
  const count = (st: RowStatus) => list.filter((x) => x.st === st).length
  const retries = list
    .filter((x) => x.st === 'retrying')
    .map((x) => disabledUntil(x.s))
    .filter((v): v is number => v !== null && v > now)
  return {
    retrying: count('retrying'),
    needs_key: count('needs_key'),
    off: count('off'),
    nextTry: retries.length ? Math.min(...retries) : null,
  }
}

/* ---- the wire ---- */

/** Start of the oldest hourly bucket: the API's last bucket is the current UTC hour. */
export const firstBucket = (now: number) => Math.floor(now / HOUR) * HOUR - 23 * HOUR
export const bucketStart = (now: number, i: number) => firstBucket(now) + i * HOUR

export interface WireHour {
  total: number
  byCat: Partial<Record<Category, number>>
}

export interface Wire {
  hours: WireHour[]
  total: number
  cats: { cat: Category; n: number }[]
  max: number
}

const NICE = [4, 5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 120, 150, 200, 250, 300, 400, 500]

export function niceMax(v: number): number {
  for (const m of NICE) if (v <= m) return m
  return Math.ceil(v / 100) * 100
}

export function wire(rows: ApiSource[]): Wire {
  const feeds = rows.filter(isFeed)
  const hours: WireHour[] = Array.from({ length: 24 }, (_, i) => {
    const byCat: Partial<Record<Category, number>> = {}
    for (const s of feeds) {
      const v = Number(s.hourly_24h![i] ?? 0) || 0
      const cat = s.category as Category
      byCat[cat] = (byCat[cat] ?? 0) + v
    }
    return { total: Object.values(byCat).reduce((a, v) => a + (v ?? 0), 0), byCat }
  })
  const total = hours.reduce((a, h) => a + h.total, 0)
  const cats = CATEGORIES.filter((c) => FEED_CATEGORIES.has(c) && feeds.some((s) => s.category === c)).map((cat) => ({
    cat,
    n: hours.reduce((a, h) => a + (h.byCat[cat] ?? 0), 0),
  }))
  return { hours, total, cats, max: niceMax(Math.max(0, ...hours.map((h) => h.total))) }
}

/** America/New_York's UTC offset in minutes at an instant (−240 in daylight time, −300 otherwise). */
function nyOffsetMinutes(ms: number): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'shortOffset' }).formatToParts(
    new Date(ms),
  )
  const m = /GMT([+-]\d+)(?::(\d+))?/.exec(parts.find((p) => p.type === 'timeZoneName')?.value ?? '')
  if (!m) return -300
  const h = Number(m[1])
  return h * 60 + Math.sign(h) * Number(m[2] ?? 0)
}

/**
 * NYSE regular sessions (09:30–16:00 New York, weekdays) inside the 24-hour window, clipped to now,
 * as fractional bucket positions 0–24. From the New York calendar, so it moves with US daylight time.
 */
export function usSessions(now: number): { start: number; end: number }[] {
  const from = firstBucket(now)
  const to = from + 24 * HOUR
  const out: { start: number; end: number }[] = []
  for (let d = -1; d <= 1; d++) {
    const day = new Date(from + d * 24 * HOUR + 12 * HOUR)
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
    }).formatToParts(day)
    const get = (k: string) => parts.find((p) => p.type === k)?.value ?? ''
    if (get('weekday') === 'Sat' || get('weekday') === 'Sun') continue
    const base = Date.UTC(Number(get('year')), Number(get('month')) - 1, Number(get('day')))
    const off = nyOffsetMinutes(base + 14 * HOUR)
    const a = base + (9 * 60 + 30 - off) * MIN
    const b = base + (16 * 60 - off) * MIN
    const s0 = Math.max(a, from)
    const s1 = Math.min(b, to, now)
    if (s1 > s0) out.push({ start: (s0 - from) / HOUR, end: (s1 - from) / HOUR })
  }
  return out
}
