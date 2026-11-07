import type { WarroomMessage } from './api'
import { httpUrl } from './url'

/*
 * Reads News Intelligence's `news_brief` card (research_report + card.type "news_brief"):
 * atlas_agents.schemas.NewsBrief as ATL-106/107/120 store it (docs/handoff/ATL-106-news-status.md,
 * docs/handoff/ATL-107-news-intelligence.md). Status, status_sources, first_seen, the price moves,
 * form, top_instruments, upcoming_events[].importance, degraded_asset_classes and degraded_since
 * are written by code. Headlines, source names and event names are untrusted internet text: this
 * reader only checks their type; the card renders them as plain React text.
 */

export type NewsForm = 'morning_brief' | 'instrument_brief' | 'alert_analysis'
export type NewsStatus = 'CONFIRMED' | 'REPORTED' | 'RUMOUR'
export type PricedIn = 'yes' | 'partly' | 'no'
export const NEWS_FORMS: readonly NewsForm[] = ['morning_brief', 'instrument_brief', 'alert_analysis']
const STATUSES: readonly NewsStatus[] = ['CONFIRMED', 'REPORTED', 'RUMOUR']

export interface NewsSource {
  name: string
  /** Only http(s) URLs; anything else is null and the source shows without a link. */
  url: URL | null
  publishedAt: string | null
}

export interface PriceMove {
  instId: string
  /** Signed ATR multiple, or null when code could not compute it (then `reason` says why). */
  value: number | null
  reason: string | null
}

export interface NewsCluster {
  clusterId: string
  headline: string
  status: NewsStatus
  /** The source keys that decided the status (e.g. "rss.coindesk"). */
  statusSources: string[]
  sources: NewsSource[]
  eventType: string | null
  importance: number
  sentiment: number | null
  firstSeen: string | null
  moves: PriceMove[]
}

export interface NewsEvent {
  time: string
  name: string
  forecast: string | null
  previous: string | null
  sensitive: string[]
  importance: number | null
}

export interface FlowRow {
  instId: string
  stories: number
  netSentiment: number | null
}

export interface NewsBrief {
  form: NewsForm
  instIds: string[]
  clusters: NewsCluster[]
  events: NewsEvent[]
  net24: number | null
  net72: number | null
  pricedIn: PricedIn | null
  whatToWatch: string | null
  dataAsOf: string
  topInstruments: FlowRow[]
  degraded: string[]
  /** One entry per degraded class: the ISO time its news stopped, or null when no source ever worked. */
  degradedSince: Record<string, string | null>
}

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const ts = (v: unknown): string | null => {
  const s = str(v)
  return s && !Number.isNaN(Date.parse(s)) ? s : null
}
const unit = (v: unknown): number | null => {
  const n = num(v)
  return n === null ? null : Math.max(-1, Math.min(1, n))
}
const strings = (v: unknown): string[] => (Array.isArray(v) ? [...new Set(v.filter((x): x is string => typeof x === 'string' && x.trim() !== ''))] : [])
const importance = (v: unknown): number | null => {
  const n = num(v)
  return n === null ? null : Math.max(1, Math.min(5, Math.round(n)))
}
/** A calendar value as written ("+0.3%", "226K", "EPS 0.62"); a number is shown as its digits. */
const value = (v: unknown): string | null => (typeof v === 'number' && Number.isFinite(v) ? String(v) : str(v))

function source(v: unknown): NewsSource | null {
  if (!isObj(v)) return null
  const name = str(v.name)
  if (!name) return null
  return { name, url: httpUrl(v.url), publishedAt: ts(v.published_at) }
}

/** price_move_since_first_seen_atr: ATL-106's list of {inst_id, value, reason}; an old {inst: value} map is read too. */
function moves(v: unknown): PriceMove[] {
  if (Array.isArray(v)) {
    return v.filter(isObj).flatMap((m): PriceMove[] => {
      const instId = str(m.inst_id)
      return instId ? [{ instId, value: num(m.value), reason: str(m.reason) }] : []
    })
  }
  if (isObj(v)) return Object.entries(v).map(([instId, x]) => ({ instId, value: num(x), reason: null }))
  return []
}

function cluster(v: unknown): NewsCluster | null {
  if (!isObj(v)) return null
  const headline = str(v.headline)
  if (!headline) return null
  const status = STATUSES.includes(v.status as NewsStatus) ? (v.status as NewsStatus) : 'RUMOUR'
  return {
    clusterId: str(v.cluster_id) ?? '',
    headline,
    status,
    statusSources: strings(v.status_sources),
    sources: Array.isArray(v.sources) ? v.sources.map(source).filter((s): s is NewsSource => s !== null) : [],
    eventType: str(v.event_type),
    importance: importance(v.importance) ?? 1,
    sentiment: unit(v.sentiment),
    firstSeen: ts(v.first_seen),
    moves: moves(v.price_move_since_first_seen_atr),
  }
}

function event(v: unknown): NewsEvent | null {
  if (!isObj(v)) return null
  const time = ts(v.time)
  const name = str(v.name)
  if (!time || !name) return null
  return { time, name, forecast: value(v.forecast), previous: value(v.previous), sensitive: strings(v.sensitive_instruments), importance: importance(v.importance) }
}

function flow(v: unknown): FlowRow | null {
  if (!isObj(v)) return null
  const instId = str(v.inst_id)
  const stories = num(v.stories)
  if (!instId || stories === null) return null
  return { instId, stories: Math.max(0, Math.round(stories)), netSentiment: unit(v.net_sentiment) }
}

/**
 * The card's fields, or null when it is not a news_brief or lacks data_as_of (the GenericCard draws
 * it then). A card with no `form` (written before ATL-107) is an instrument brief, or an alert
 * analysis when it replies to a news_alert.
 */
export function readNewsBrief(card: WarroomMessage['card'], parentKind?: string | null): NewsBrief | null {
  if (!card || card.type !== 'news_brief') return null
  const dataAsOf = ts(card.data_as_of)
  if (!dataAsOf) return null
  const form: NewsForm = NEWS_FORMS.includes(card.form as NewsForm)
    ? (card.form as NewsForm)
    : parentKind === 'news_alert'
      ? 'alert_analysis'
      : 'instrument_brief'
  const degraded = strings(card.degraded_asset_classes)
  const since = isObj(card.degraded_since) ? card.degraded_since : {}
  const pricedIn = card.priced_in === 'yes' || card.priced_in === 'partly' || card.priced_in === 'no' ? card.priced_in : null
  return {
    form,
    instIds: strings(card.inst_ids),
    clusters: Array.isArray(card.clusters) ? card.clusters.map(cluster).filter((c): c is NewsCluster => c !== null) : [],
    events: (Array.isArray(card.upcoming_events) ? card.upcoming_events.map(event).filter((e): e is NewsEvent => e !== null) : []).sort(
      (a, b) => Date.parse(a.time) - Date.parse(b.time) || (b.importance ?? 0) - (a.importance ?? 0),
    ),
    net24: unit(card.net_sentiment_24h),
    net72: unit(card.net_sentiment_72h),
    pricedIn,
    whatToWatch: str(card.what_to_watch),
    dataAsOf,
    topInstruments: Array.isArray(card.top_instruments) ? card.top_instruments.map(flow).filter((r): r is FlowRow => r !== null) : [],
    degraded,
    degradedSince: Object.fromEntries(degraded.map((k) => [k, ts(since[k])])),
  }
}

/**
 * Degraded classes grouped by outage: classes whose news stopped in the same minute share one
 * band and its time; classes with no known time share a band with no time. Sorted by time.
 */
export function outages(brief: Pick<NewsBrief, 'degraded' | 'degradedSince'>): Array<{ since: string | null; classes: string[] }> {
  const groups: Array<{ minute: number | null; since: string | null; classes: string[] }> = []
  for (const k of brief.degraded) {
    const s = brief.degradedSince[k] ?? null
    const minute = s === null ? null : Math.floor(Date.parse(s) / 60_000)
    const g = groups.find((x) => x.minute === minute)
    if (g) g.classes.push(k)
    else groups.push({ minute, since: s, classes: [k] })
  }
  return groups.sort((a, b) => (a.minute ?? Infinity) - (b.minute ?? Infinity)).map(({ since, classes }) => ({ since, classes }))
}

/**
 * A status source key ("rss.coindesk") as a name: the cluster source whose name matches the key's
 * last segment ("CoinDesk"), else the key itself.
 */
export function sourceLabel(key: string, sources: NewsSource[]): string {
  const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
  const tail = squash(key.split('.').pop() ?? key)
  const hit = tail
    ? sources.find((s) => {
        const name = squash(s.name)
        return name !== '' && (name.includes(tail) || tail.includes(name))
      })
    : undefined
  return hit?.name ?? key
}
