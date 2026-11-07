import type { WarroomMessage } from './api'

/*
 * The Historian's two cards (ATL-93, docs/handoff/ATL-93-historian-agent.md): a research_report
 * whose card is a HistoryVerdict (atlas_agents.schemas.HistoryVerdict + runtime meta), and the
 * 07:30 regime_table (RegimeTable). Each reader returns null when the card lacks what the
 * renderer needs, and the GenericCard draws the message instead.
 */

export type Verdict = 'supports' | 'weakly_supports' | 'does_not_support' | 'contradicts'
export const VERDICTS: readonly Verdict[] = ['supports', 'weakly_supports', 'does_not_support', 'contradicts']

export interface Dist {
  median: number
  p10: number
  p90: number
  pct_positive: number
}

export interface Correction {
  from: Verdict
  to: Verdict
  rule: string
  n: number | null
}

export interface HistoryVerdictCard {
  question: string
  instId: string | null
  bar: string | null
  period: string | null
  n: number
  /** Horizons sorted by length ("1h", "4h", "24h", "72h"). */
  horizons: Array<{ key: string; hours: number | null; cases: Dist; base: Dist | null }>
  regime: { label: string; probability: number | null; days: number | null } | null
  stop: { stopAtr: number; rangeAtr: number; verdict: 'ok' | 'inside_noise' } | null
  overfit: boolean
  verdict: Verdict
  keyStat: string
  notes: string[]
  dataAsOf: string
  corrections: Correction[]
  /** The card said status no_data, or no case matched (n = 0). */
  noData: boolean
}

export interface RegimeRow {
  rank: number
  instId: string
  label: string
  probability: number | null
  days: number | null
}

export interface RegimeTableCard {
  rows: RegimeRow[]
  dataAsOf: string | null
}

/** One number the number guard could not trace (ATL-91 to_meta()). */
export interface Unmatched {
  raw: string
  location: 'text' | 'card'
  path: string | null
}

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const isVerdict = (v: unknown): v is Verdict => typeof v === 'string' && (VERDICTS as readonly string[]).includes(v)
const validTs = (v: unknown): string | null => {
  const s = str(v)
  return s && !Number.isNaN(Date.parse(s)) ? s : null
}

/** "24h" → 24, "3d" → 72; anything else null (it sorts last and shows as written). */
export function hoursOf(key: string): number | null {
  const m = /^(\d+(?:\.\d+)?)\s*([mhd])$/i.exec(key.trim())
  if (!m) return null
  const v = Number(m[1])
  const unit = m[2]!.toLowerCase()
  return unit === 'd' ? v * 24 : unit === 'm' ? v / 60 : v
}

function dist(v: unknown): Dist | null {
  if (!isObj(v)) return null
  const median = num(v.median)
  const p10 = num(v.p10)
  const p90 = num(v.p90)
  const pct = num(v.pct_positive)
  if (median === null || p10 === null || p90 === null || pct === null) return null
  return { median, p10, p90, pct_positive: pct }
}

function metaOf(card: Obj): Obj {
  return isObj(card.meta) ? card.meta : {}
}

export function readHistoryVerdict(card: WarroomMessage['card']): HistoryVerdictCard | null {
  if (!card || card.type !== 'history_verdict') return null
  const question = str(card.question)
  const n = num(card.n)
  const dataAsOf = validTs(card.data_as_of)
  if (!question || n === null || !isVerdict(card.verdict) || !dataAsOf) return null

  const distribution = isObj(card.distribution) ? card.distribution : {}
  const baseRate = isObj(card.base_rate) ? card.base_rate : {}
  const horizons = Object.entries(distribution)
    .map(([key, v]) => ({ key, hours: hoursOf(key), cases: dist(v), base: dist(baseRate[key]) }))
    .filter((h): h is { key: string; hours: number | null; cases: Dist; base: Dist | null } => h.cases !== null)
    .sort((a, b) => (a.hours ?? Infinity) - (b.hours ?? Infinity))

  const r = isObj(card.regime) ? card.regime : null
  const regimeLabel = r ? str(r.label) : null
  const s = isObj(card.stop_noise_check) ? card.stop_noise_check : null
  const stopAtr = s ? num(s.stop_distance_atr) : null
  const rangeAtr = s ? num(s.median_4h_range_atr) : null

  const corrections = (Array.isArray(metaOf(card).verdict_corrections) ? (metaOf(card).verdict_corrections as unknown[]) : [])
    .filter(isObj)
    .flatMap((c): Correction[] => {
      const rule = str(c.rule)
      if (!rule || !isVerdict(c.from) || !isVerdict(c.to)) return []
      return [{ from: c.from, to: c.to, rule, n: num(c.n) }]
    })

  return {
    question,
    instId: str(card.inst_id),
    bar: str(card.bar),
    period: str(card.period),
    n: Math.max(0, Math.round(n)),
    horizons,
    regime: regimeLabel ? { label: regimeLabel, probability: num(r!.probability), days: num(r!.days_in_regime) } : null,
    stop:
      stopAtr !== null && rangeAtr !== null && (s!.verdict === 'ok' || s!.verdict === 'inside_noise')
        ? { stopAtr, rangeAtr, verdict: s!.verdict }
        : null,
    overfit: card.overfit_warning === true,
    verdict: card.verdict,
    keyStat: str(card.key_stat) ?? '',
    notes: Array.isArray(card.data_quality_notes) ? card.data_quality_notes.filter((x): x is string => typeof x === 'string' && x.trim() !== '') : [],
    dataAsOf,
    corrections,
    noData: card.status === 'no_data' || Math.round(n) === 0,
  }
}

export function readRegimeTable(card: WarroomMessage['card']): RegimeTableCard | null {
  if (!card || card.type !== 'regime_table' || !Array.isArray(card.rows)) return null
  const rows = card.rows.filter(isObj).flatMap((r, i): RegimeRow[] => {
    const instId = str(r.inst_id)
    const label = str(r.label)
    if (!instId || !label) return []
    return [{ rank: i + 1, instId, label, probability: num(r.probability), days: num(r.days_in_regime) }]
  })
  if (rows.length === 0) return null
  // The card's data_as_of, else the newest per-row data_as_of (ATL-88's older shape).
  let dataAsOf = validTs(card.data_as_of)
  if (!dataAsOf) {
    const times = card.rows.filter(isObj).map((r) => validTs(r.data_as_of)).filter((t): t is string => t !== null)
    dataAsOf = times.sort((a, b) => Date.parse(b) - Date.parse(a))[0] ?? null
  }
  return { rows, dataAsOf }
}

/**
 * The message's untraced numbers: the resource's `unmatched_numbers` (ATL-89), else the card's
 * meta (`meta.numguard.unmatched_numbers` or `meta.unmatched_numbers`).
 */
export function unmatchedOf(m: WarroomMessage): Unmatched[] {
  let list: unknown = m.unmatched_numbers
  if (!Array.isArray(list) && m.card) {
    const meta = metaOf(m.card)
    list = isObj(meta.numguard) && Array.isArray(meta.numguard.unmatched_numbers) ? meta.numguard.unmatched_numbers : meta.unmatched_numbers
  }
  if (!Array.isArray(list)) return []
  return list.filter(isObj).flatMap((u): Unmatched[] => {
    const raw = str(u.raw)
    if (!raw || (u.location !== 'text' && u.location !== 'card')) return []
    return [{ raw, location: u.location, path: str(u.path) }]
  })
}

/** The raw figure as the bidi islands draw it: hyphen-minus becomes − (tokens.ts typographicSign). */
export const normaliseRaw = (raw: string) => raw.trim().replace(/-/g, '−')

/** The order the regime table's chips and its Regime column sort in; unknown labels after. */
export const REGIME_ORDER = ['trend_up', 'trend_down', 'range', 'high_vol', 'low_vol'] as const
export const KNOWN_REGIMES: ReadonlySet<string> = new Set(REGIME_ORDER)

/** Rules that cap a verdict on a small sample: ATL-87's "small_sample" or ATL-93's "n_below_<min_n>". */
export const isSmallSampleRule = (rule: string) => rule === 'small_sample' || /^n_below_\d+$/.test(rule)

/** The Historian's minimum sample for a strong verdict (config/defaults.yaml agents.history.verdict.min_n). */
export const MIN_N = 20
