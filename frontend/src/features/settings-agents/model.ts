import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, useApiQuery } from '../../lib/api'
import { formatFixed, type Lang } from '../../lib/format'
import { settingsKey, type SettingsDoc } from '../onboarding'
import { historyKey } from '../settings-risk/model'

/*
 * Settings → Agents (brief §14.7, decision 247 (7); design v9 js/pages/settings-agents.js).
 * Agent settings are flat dotted keys in settings.data (ATL-113):
 *   agents.<slug>.enabled | model | daily_tokens | daily_turns, agents.web.allow_domains | deny_domains
 * written through the versioned PATCH /api/settings. The page keeps only the owner's edits, keyed
 * by those names, on top of the saved values, so a 409 can take the newer version and keep them.
 */

export const ROSTER = [
  'orchestrator',
  'market_scout',
  'news_intelligence',
  'historian',
  'technical_analyst',
  'derivatives_sentiment',
  'strategist',
  'devils_advocate',
  'portfolio_manager',
] as const
export type AgentSlug = (typeof ROSTER)[number]

export const TIERS = ['strong', 'standard', 'routine'] as const
export type Tier = (typeof TIERS)[number]
/** Tier → the CLI model it runs on (config/defaults.yaml agents.models). */
export const ALIAS: Record<Tier, string> = { strong: 'Opus', standard: 'Sonnet', routine: 'Haiku' }
const STRONG: ReadonlySet<string> = new Set(['market_scout', 'news_intelligence', 'historian', 'strategist', 'devils_advocate'])

export type NumField = 'daily_tokens' | 'daily_turns'
/** config/defaults.yaml agents.settings_bounds; the step is the stepper's. */
export const BOUNDS: Record<NumField, { min: number; max: number; step: number }> = {
  daily_tokens: { min: 1, max: 1_000_000_000, step: 100_000 },
  daily_turns: { min: 1, max: 100_000, step: 10 },
}

export type ListWhich = 'allow' | 'deny'
export const LISTS: Record<ListWhich, string> = { allow: 'agents.web.allow_domains', deny: 'agents.web.deny_domains' }

export const key = (slug: string, field: 'enabled' | 'model' | NumField) => `agents.${slug}.${field}`

/** A field's value: a switch, a tier, a stepper's text, or a site list. */
export type Value = boolean | string | string[]
/** What the server holds for every agents.* field, with numbers as JSON numbers. */
export type Saved = Record<string, boolean | string | number | string[]>
export type Edits = Record<string, Value>

/** The saved agents.* values; a field missing from an old row falls back to the defaults.yaml default. */
export function savedFrom(doc: SettingsDoc): Saved {
  const s = doc.settings as unknown as Record<string, unknown>
  const out: Saved = {}
  for (const slug of ROSTER) {
    const e = s[key(slug, 'enabled')]
    out[key(slug, 'enabled')] = typeof e === 'boolean' ? e : slug === 'historian'
    const m = s[key(slug, 'model')]
    out[key(slug, 'model')] = typeof m === 'string' ? m : STRONG.has(slug) ? 'strong' : 'standard'
    for (const f of ['daily_tokens', 'daily_turns'] as const) {
      const v = s[key(slug, f)]
      out[key(slug, f)] = typeof v === 'number' ? v : f === 'daily_tokens' ? 3_000_000 : 300
    }
  }
  for (const k of Object.values(LISTS)) {
    const v = s[k]
    out[k] = Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  }
  return out
}

export const isNumKey = (k: string) => k.endsWith('.daily_tokens') || k.endsWith('.daily_turns')
const numFieldOf = (k: string): NumField => (k.endsWith('.daily_tokens') ? 'daily_tokens' : 'daily_turns')

/** Digits only: "3,000,000" and "3 000 000" read as 3000000; anything else is not a number. */
export function parseCount(text: string): number | null {
  const raw = text.replace(/[,\s٬_]/g, '').trim()
  return /^\d+$/.test(raw) ? Number(raw) : null
}

export const countText = (v: number, lang: Lang) => formatFixed(v, 0, lang)

/** The draft value of a field: the edit if there is one, otherwise the saved value (numbers as text). */
export function current(saved: Saved, edits: Edits, k: string, lang: Lang): Value {
  if (k in edits) return edits[k] as Value
  const v = saved[k]
  return typeof v === 'number' ? countText(v, lang) : (v as Value)
}

function same(a: Value | number | undefined, b: Value | number | undefined): boolean {
  if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i])
  if (typeof b === 'number' && typeof a === 'string') return parseCount(a) === b
  return a === b
}

/** Sets one field; an edit back to the saved value is dropped, so "changed" stays exact. */
export function withEdit(saved: Saved, edits: Edits, k: string, v: Value): Edits {
  const { [k]: _old, ...rest } = edits
  return same(v, saved[k]) ? rest : { ...rest, [k]: v }
}

/** Edits that still differ from the (possibly newer) saved values. */
export function pruned(saved: Saved, edits: Edits): Edits {
  return Object.fromEntries(Object.entries(edits).filter(([k, v]) => !same(v, saved[k])))
}

export const isChanged = (saved: Saved, edits: Edits, k: string) => k in edits && !same(edits[k], saved[k])

/** The client's own check on a stepper's text: empty or out of range. */
export function numError(k: string, text: string): 'empty' | 'range' | null {
  if (text.trim() === '') return 'empty'
  const n = parseCount(text)
  const b = BOUNDS[numFieldOf(k)]
  return n === null || n < b.min || n > b.max ? 'range' : null
}

/** One stepper press: snap to the step grid and stay inside the bounds. */
export function stepped(k: string, text: string, dir: 1 | -1): number {
  const b = BOUNDS[numFieldOf(k)]
  const v = parseCount(text) ?? b.min
  const next = dir > 0 ? Math.floor(v / b.step) * b.step + b.step : Math.ceil(v / b.step) * b.step - b.step
  return Math.min(b.max, Math.max(b.min, next))
}

/** The PATCH `changes`: numbers as JSON integers, lists replaced whole. */
export function changesFor(saved: Saved, edits: Edits): Record<string, boolean | string | number | string[]> {
  const out: Record<string, boolean | string | number | string[]> = {}
  for (const [k, v] of Object.entries(pruned(saved, edits))) {
    out[k] = isNumKey(k) && typeof v === 'string' ? (parseCount(v) ?? v) : v
  }
  return out
}

/* ---------------------------------------------------------------- site names */

const BAD = /[\s/:@?#*%\\[\]]/

export function isBareHost(h: string): boolean {
  if (!h || BAD.test(h) || h.length > 253) return false
  return h.split('.').every((label) => label.length > 0 && label.length <= 63)
}

/**
 * What the server will store (ATL-113): trimmed, lower-case, one trailing dot dropped, an IDN as
 * punycode. Anything that is not a bare hostname is kept as typed, for the server to refuse, so
 * the owner sees the server's own message under the list.
 */
export function normaliseHost(raw: string): string {
  const h = raw.trim()
  if (!h) return ''
  let n = h.toLowerCase().replace(/\.$/, '')
  if (!isBareHost(n)) return h
  if ([...n].some((c) => c.charCodeAt(0) > 0x7f)) {
    try {
      const puny = new URL(`http://${n}`).hostname
      TYPED.set(puny, n)
      n = puny
    } catch {
      return h
    }
  }
  return n
}

/** Punycode → what the owner typed this session; the browser has no punycode decoder. */
const TYPED = new Map<string, string>()

/** "xn--bcher-kva.example" shows as "bücher.example" when it was typed here; the stored form otherwise. */
export const displayHost = (h: string): string => TYPED.get(h) ?? h
export const isIdn = (h: string): boolean => TYPED.has(h)

/* ------------------------------------------------------------ server answers */

export interface ServerError {
  message: string
  /** For a list entry: the entry the server refused, so the error follows it when others are removed. */
  host?: string
}

/** A 422's `changes.<key>` errors. A list entry's error keeps the host it was about. */
export function serverErrors(err: unknown, sent: Record<string, unknown>): Record<string, ServerError> {
  if (!(err instanceof ApiError) || err.status !== 422) return {}
  const out: Record<string, ServerError> = {}
  for (const [k, messages] of Object.entries(err.fieldErrors)) {
    if (!k.startsWith('changes.') || !messages[0]) continue
    const name = k.slice('changes.'.length)
    const list = Object.values(LISTS).find((l) => name.startsWith(`${l}.`))
    if (list) {
      const i = Number(name.slice(list.length + 1))
      const hosts = sent[list]
      const host = Array.isArray(hosts) && typeof hosts[i] === 'string' ? hosts[i] : undefined
      out[name] = host === undefined ? { message: messages[0] } : { message: messages[0], host }
    } else {
      out[name] = { message: messages[0] }
    }
  }
  return out
}

/** The refused entries of a list that are still in it, and the message for the first. */
export function listProblems(errors: Record<string, ServerError>, which: ListWhich, list: readonly string[]) {
  const k = LISTS[which]
  const bad = new Set<string>()
  let message: string | null = null
  for (const [name, e] of Object.entries(errors)) {
    if (name === k) message ??= e.message
    if (!name.startsWith(`${k}.`) || e.host === undefined || !list.includes(e.host)) continue
    bad.add(e.host)
    message ??= e.message
  }
  return { bad, message }
}

/* --------------------------------------------------------------------- API */

/** PATCH /api/settings, no retry: a 409 makes the page take the newer version and keep the edits. */
export function useSaveAgentSettings(env: SettingsDoc['env'] = 'demo') {
  const client = useQueryClient()
  return useMutation<SettingsDoc, ApiError, { version: number; changes: Record<string, unknown> }>({
    mutationFn: async ({ version, changes }) => (await api.patch<{ data: SettingsDoc }>('/api/settings', { env, version, changes })).data,
    onSuccess: (doc) => {
      client.setQueryData(settingsKey(env), { data: doc })
      void client.invalidateQueries({ queryKey: historyKey(env) })
    },
  })
}

/** GET /api/agents (ATL-113): only today's use is read here, for the "Used today" lines. */
export interface AgentUsage {
  slug: string
  today: { tokens: number; turns: number }
}

export function useAgentUsage(env: SettingsDoc['env'] = 'demo') {
  return useApiQuery<{ data: AgentUsage[] }>(['agents', env], `/api/agents?env=${env}`, { staleTime: 60_000, retry: false })
}

/** "27M", "1.5M", "620K" — the summary's totals and the "Used today" figures. */
export function compact(v: number, lang: Lang): string {
  const trim = (s: string) => s.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
  if (v >= 1e9) return `${trim((v / 1e9).toFixed(2))}B`
  if (v >= 1e6) return `${trim((v / 1e6).toFixed(2))}M`
  if (v >= 1e4) return `${(v / 1e3).toFixed(0)}K`
  return countText(v, lang)
}
