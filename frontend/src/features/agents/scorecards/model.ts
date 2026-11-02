import type { AgentScores, ScoreDay, ScoreValue } from './api'

/*
 * What each scorecard shows, worked out from ATL-200's daily rows (design v16 "Scorecards", §5).
 * Pure, so vitest checks every rule without rendering.
 *
 * Window figures are the n-weighted mean of the daily values, each weighted by the metric's own
 * sample key; counts (hits, alerts, loops) sum. A metric whose window n is under its minimum is
 * "Not enough data" and shows the n instead of a value. The minimums, targets and references are
 * the design's table ("What each card shows"); the keys are the ones each scorer writes.
 */

export type Unit = 'pct' | 'sec' | 'atr' | 'r' | 'count' | 'cal' | 'mix'
export type Better = 'up' | 'down'

export interface MetricSpec {
  id: string
  unit: Unit
  /** The data key holding the day's value. */
  key: string
  /** The data key holding the day's sample; omitted = the row's n. */
  nKey?: string
  /** The data key holding the day's hits (k of n). */
  kKey?: string
  /** Minimum window n before a figure is shown. 0 = always shown. */
  min: number
  better?: Better
  /** A target the figure meets or misses ("45% or more", "under 90 s"). */
  target?: number
  /** A reference it is better or worse than (the shortlist, a coin flip, break-even). */
  ref?: number
}

const M = (id: string, unit: Unit, key: string, o: Omit<MetricSpec, 'id' | 'unit' | 'key'>): MetricSpec => ({ id, unit, key, ...o })

/** Every metric, by id. */
export const METRICS: Readonly<Record<string, MetricSpec>> = {
  selection_edge: M('selection_edge', 'atr', 'selection_edge_atr', { min: 20, better: 'up', ref: 0 }),
  direction: M('direction', 'pct', 'directional_accuracy', { nKey: 'directional_n', kKey: 'directional_correct', min: 20, better: 'up', ref: 0.5 }),
  latency: M('latency', 'sec', 'latency_p90_s', { min: 10, better: 'down', target: 90 }),
  alerts: M('alerts', 'count', 'n', { min: 0 }),
  calHist: M('calHist', 'cal', 'brier_score', { nKey: 'brier_n', min: 30, ref: 0.25 }),
  mix: M('mix', 'mix', 'verdict_mix', { nKey: 'verdicts_n', min: 10 }),
  tbs: M('tbs', 'pct', 'target_before_stop_rate', { nKey: 'target_before_stop_n', kKey: 'target_hits', min: 20, better: 'up', target: 0.45 }),
  levels: M('levels', 'pct', 'level_hold_rate', { nKey: 'level_hold_n', min: 20, better: 'up' }),
  rr: M('rr', 'r', 'avg_realised_r', { nKey: 'realised_r_n', min: 20, better: 'up' }),
  nosetup: M('nosetup', 'pct', 'no_setup_accuracy', { nKey: 'no_setup_n', min: 20, better: 'up' }),
  supports: M('supports', 'pct', 'supports_accuracy', { nKey: 'supports_n', kKey: 'supports_correct', min: 20, better: 'up', ref: 0.5 }),
  squeeze: M('squeeze', 'pct', 'squeeze_call_hit_rate', { nKey: 'squeeze_calls_n', kKey: 'squeeze_call_hits', min: 10, better: 'up' }),
  calSqueeze: M('calSqueeze', 'cal', 'brier_score', { nKey: 'brier_n', min: 30, ref: 0.25 }),
  avgR: M('avgR', 'r', 'avg_realised_r', { nKey: 'taken_n', min: 20, better: 'up', ref: 0 }),
  reject: M('reject', 'pct', 'rejection_rate', { nKey: 'submitted_n', kKey: 'risk_rejected_n', min: 20, better: 'down', target: 0.1 }),
  calStrat: M('calStrat', 'cal', 'brier_score', { nKey: 'brier_n', min: 30, ref: 0.25 }),
  regret: M('regret', 'r', 'regret_r', { nKey: 'regret_n', min: 10, better: 'down' }),
  falseAlarm: M('falseAlarm', 'pct', 'false_alarm_rate', { nKey: 'high_n', kKey: 'high_won', min: 10, better: 'down' }),
  miss: M('miss', 'pct', 'miss_rate', { nKey: 'low_n', kKey: 'low_lost', min: 10, better: 'down' }),
  captured: M('captured', 'pct', 'r_captured', { nKey: 'r_captured_n', min: 20, better: 'up' }),
  losers: M('losers', 'pct', 'losers_within_rate', { nKey: 'losers_n', kKey: 'losers_within_n', min: 10, better: 'up' }),
  rules: M('rules', 'pct', 'compliance_rate', { nKey: 'compliance_n', kKey: 'compliance_ok', min: 1, better: 'up', target: 1 }),
  onTime: M('onTime', 'pct', 'on_time_rate', { nKey: 'cycles_n', kKey: 'cycles_on_time', min: 20, better: 'up', target: 0.95 }),
  ack: M('ack', 'sec', 'ack_p90_seconds', { nKey: 'ack_n', min: 10, better: 'down', target: 10 }),
  loops: M('loops', 'count', 'loops_stopped', { min: 0 }),
}

/** Secondary figures a metric's sub-line uses: the median beside a p90, the stated R beside the realised. */
const COMPANION: Readonly<Record<string, { key: string; nKey?: string }>> = {
  latency: { key: 'latency_median_s' },
  ack: { key: 'ack_median_seconds', nKey: 'ack_n' },
  rr: { key: 'avg_expected_r', nKey: 'expected_r_n' },
}

/** The brief's +1.8R-or-more target on the Technical Analyst's stated R (§7.4). */
export const STATED_R_TARGET = 1.8
/** A coin flip's Brier score: the calibration reference. */
export const COIN_BRIER = 0.25
/** Points of gap a calibration may have and still read "well calibrated"; over WIDE it warns. */
export const CAL_OK_POINTS = 3
export const CAL_WIDE_POINTS = 10

export interface CardSpec {
  slug: string
  head: string
  rows: string[]
}

/** The nine cards, in the order work flows through the team. */
export const CARDS: readonly CardSpec[] = [
  { slug: 'market_scout', head: 'selection_edge', rows: ['direction'] },
  { slug: 'news_intelligence', head: 'latency', rows: ['alerts'] },
  { slug: 'historian', head: 'calHist', rows: ['mix'] },
  { slug: 'technical_analyst', head: 'tbs', rows: ['levels', 'rr', 'nosetup'] },
  { slug: 'derivatives_sentiment', head: 'supports', rows: ['squeeze', 'calSqueeze'] },
  { slug: 'strategist', head: 'avgR', rows: ['reject', 'calStrat', 'regret'] },
  { slug: 'devils_advocate', head: 'falseAlarm', rows: ['miss'] },
  { slug: 'portfolio_manager', head: 'captured', rows: ['losers', 'rules'] },
  { slug: 'orchestrator', head: 'onTime', rows: ['ack', 'loops'] },
]

/** The five voters, in the weight panel's order (brief §7.9). */
export const VOTERS: readonly string[] = ['technical_analyst', 'derivatives_sentiment', 'news_intelligence', 'historian', 'devils_advocate']
/** Scored votes a weight needs before it moves off 1.0× (agents.scoring.reliability.min_n). */
export const WEIGHT_MIN_N = 10
export const WEIGHT_LO = 0.5
export const WEIGHT_HI = 1.5

export const VERDICTS = ['supports', 'weakly_supports', 'does_not_support', 'contradicts'] as const
export type Verdict = (typeof VERDICTS)[number]

/** A finite number from a scorer value, or null. Display maths only: none of these are money. */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const x = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
  return Number.isFinite(x) ? x : null
}

const field = (row: ScoreDay, key: string): ScoreValue => {
  if (key === 'n') return row.n
  const d = row.data?.[key]
  if (d !== undefined) return d as ScoreValue
  if (key === 'brier_score') return row.brier_score
  return undefined
}

/** One day's sample for a metric: its own n key, else the row's n. */
function dayN(spec: { nKey?: string }, row: ScoreDay): number {
  return spec.nKey ? (num(field(row, spec.nKey)) ?? 0) : row.n
}

export interface Figure {
  /** The window value, or null when no day had one. */
  value: number | null
  n: number
  /** Hits, summed, when the metric has them; else derived from value × n. */
  k: number | null
  /** The metric's minimum is met and the value exists. */
  enough: boolean
  /** Calibration: mean stated probability and observed frequency over the same sample. */
  said?: number | null
  happened?: number | null
  /** The companion figure (median, stated R). */
  companion?: number | null
  /** Verdict mix shares, 0–1. */
  mix?: Record<Verdict, number>
}

function weighted(rows: readonly ScoreDay[], key: string, nOf: (r: ScoreDay) => number): { v: number | null; n: number } {
  let s = 0
  let w = 0
  for (const r of rows) {
    const v = num(field(r, key))
    const n = nOf(r)
    if (v === null || n <= 0) continue
    s += v * n
    w += n
  }
  return { v: w > 0 ? s / w : null, n: w }
}

/** A metric over a set of daily rows (the period, or a 7-day trend window). */
export function figure(spec: MetricSpec, rows: readonly ScoreDay[]): Figure {
  if (spec.unit === 'count') {
    const total = rows.reduce((s, r) => s + (num(field(r, spec.key)) ?? 0), 0)
    return { value: total, n: total, k: null, enough: true }
  }
  if (spec.unit === 'mix') {
    const counts = Object.fromEntries(VERDICTS.map((k) => [k, 0])) as Record<Verdict, number>
    for (const r of rows) {
      const m = r.data?.[spec.key]
      if (!m || typeof m !== 'object') continue
      for (const k of VERDICTS) counts[k] += num((m as Record<string, unknown>)[k]) ?? 0
    }
    const n = VERDICTS.reduce((s, k) => s + counts[k], 0)
    const mix = Object.fromEntries(VERDICTS.map((k) => [k, n > 0 ? counts[k] / n : 0])) as Record<Verdict, number>
    return { value: n > 0 ? n : null, n, k: null, enough: n >= spec.min && n > 0, mix }
  }
  const nOf = (r: ScoreDay) => dayN(spec, r)
  const { v, n } = weighted(rows, spec.key, nOf)
  let k: number | null = null
  if (spec.kKey) {
    const sumK = rows.reduce((s, r) => (num(field(r, spec.key)) !== null && nOf(r) > 0 ? s + (num(field(r, spec.kKey!)) ?? 0) : s), 0)
    k = sumK
  } else if (v !== null && spec.unit === 'pct') {
    k = Math.round(v * n)
  }
  const out: Figure = { value: v, n, k, enough: v !== null && n >= spec.min }
  if (spec.unit === 'cal') {
    out.said = weighted(rows, 'mean_p', nOf).v
    out.happened = weighted(rows, 'observed', nOf).v
  }
  const comp = COMPANION[spec.id]
  if (comp) out.companion = weighted(rows, comp.key, comp.nKey ? (r) => dayN(comp, r) : nOf).v
  return out
}

/** Whether a figure meets its target (or beats its reference); null when it has neither. */
export function verdictOf(spec: MetricSpec, value: number | null): { ok: boolean; kind: 'target' | 'ref' } | null {
  if (value === null) return null
  const down = spec.better === 'down'
  if (spec.target !== undefined) return { ok: down ? value < spec.target : value >= spec.target, kind: 'target' }
  if (spec.ref !== undefined) return { ok: down ? value < spec.ref : value > spec.ref, kind: 'ref' }
  return null
}

export type CalVerdict = { kind: 'ok' | 'over' | 'under'; points: number; wide: boolean }

/** "Overconfident by 8 points": said minus happened, in whole percentage points. */
export function calVerdict(said: number, happened: number): CalVerdict {
  const gap = Math.round((said - happened) * 100)
  const points = Math.abs(gap)
  return { kind: points <= CAL_OK_POINTS ? 'ok' : gap > 0 ? 'over' : 'under', points, wide: points > CAL_WIDE_POINTS }
}

/* ------------------------------------------------------------------ days */

const DAY_MS = 86_400_000
const toMs = (day: string) => Date.parse(`${day}T00:00:00Z`)
const toDay = (ms: number) => new Date(ms).toISOString().slice(0, 10)

/** Every Baghdad day from `from` to `to`, inclusive. */
export function dayRange(from: string, to: string): string[] {
  const out: string[] = []
  for (let t = toMs(from); t <= toMs(to); t += DAY_MS) out.push(toDay(t))
  return out
}

/** Rows that carry a score, not only the 00:30 reliability keys (ATL-198: those rows have n 0). */
export function isScored(row: ScoreDay): boolean {
  if (row.n > 0) return true
  return Object.keys(row.data ?? {}).some((k) => !k.startsWith('reliability'))
}

export interface TrendPoint {
  day: string
  value: number | null
}

/**
 * The headline's trend: the 7-day value on each day of the period, n-weighted from the daily
 * rows, with no point on a day whose 7 days hold fewer cases than the minimum.
 */
export function trend(spec: MetricSpec, rows: readonly ScoreDay[], from: string, to: string, span = 7): TrendPoint[] {
  return dayRange(from, to).map((day) => {
    const end = toMs(day)
    const start = end - (span - 1) * DAY_MS
    const inWin = rows.filter((r) => {
      const t = toMs(r.day)
      return t >= start && t <= end
    })
    const f = figure(spec, inWin)
    return { day, value: f.enough ? f.value : null }
  })
}

/* --------------------------------------------------------------- weights */

export interface Weight {
  weight: number
  /** Scored votes behind it, and how many were right (ATL-198). */
  n: number | null
  right: number | null
  /** Under the minimum, the weight stays at the default 1.0× (drawn hollow and grey). */
  few: boolean
}

/** A voter's reliability weight: the newest day in the window that carries one. */
export function weightOf(a: AgentScores | undefined): Weight {
  const rows = [...(a?.days ?? [])].sort((x, y) => (x.day < y.day ? 1 : -1))
  const row = rows.find((r) => num(r.data?.reliability_weight) !== null)
  const weight = num(row?.data?.reliability_weight) ?? num(a?.summary.reliability_weight) ?? 1
  const n = row ? num(row.data?.reliability_n) : null
  const right = row ? num(row.data?.reliability_correct) : null
  return { weight, n, right, few: n === null || n < WEIGHT_MIN_N }
}

/** A weight's position on the 0.5×–1.5× line, 0–100. */
export const weightX = (w: number) => Math.max(0, Math.min(100, ((w - WEIGHT_LO) / (WEIGHT_HI - WEIGHT_LO)) * 100))

/* ------------------------------------------------------------------ page */

/** The newest scored day across every agent ("Scored through …"), or null with no scores. */
export function scoredThrough(agents: readonly AgentScores[]): string | null {
  let best: string | null = null
  for (const a of agents) for (const r of a.days) if (isScored(r) && (best === null || r.day > best)) best = r.day
  return best
}

/** The earliest scored day across every agent. */
export function firstScored(agents: readonly AgentScores[]): string | null {
  let best: string | null = null
  for (const a of agents) for (const r of a.days) if (isScored(r) && (best === null || r.day < best)) best = r.day
  return best
}

/** Days between two Baghdad days (to − from). */
export const daysBetween = (from: string, to: string) => Math.round((toMs(to) - toMs(from)) / DAY_MS)

/**
 * Days old the newest scores are. The nightly job scores Market Scout, Strategist and others for
 * the day before yesterday and Derivatives for three days ago (ATL-110 / ATL-195), so scores are
 * normally 1–2 days behind; STALE_DAYS or more means a night's scoring did not run.
 */
export const STALE_DAYS = 4
