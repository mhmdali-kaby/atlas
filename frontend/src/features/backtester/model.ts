/*
 * The Backtester form (ATL-209; design v18 js/pages/backtester.js) and the request it sends:
 * exactly ATL-208's POST /api/backtests body, which is trading_core's /internal/backtest request
 * (docs/handoff/ATL-208-backtests-api.md). The checks below mirror StoreBacktestRequest so an
 * obviously wrong rule never costs a throttled request; the server stays the judge and its 422
 * reasons are mapped back onto the same field keys (serverFieldKey).
 */

export const BARS = ['1m', '5m', '15m', '1H', '4H', '1D'] as const
export type Bar = (typeof BARS)[number]
export const OPS = ['crosses_above', 'crosses_below', 'above', 'below'] as const
export type Op = (typeof OPS)[number]
export const KINDS = ['sma', 'ema', 'rsi'] as const
export type Kind = (typeof KINDS)[number]
export type Side = 'long' | 'short'

/** The price series a condition may name besides an indicator (atlas_quant CLOSE). */
export const CLOSE = 'close'

/** Limits from StoreBacktestRequest and config/defaults.yaml `backtest` (max_inst_ids, max_bars). */
export const LIMITS = {
  maxInsts: 5,
  maxIndicators: 20,
  maxEntry: 10,
  maxExit: 10,
  minWindow: 1,
  maxWindow: 5000,
  minSplits: 1,
  maxSplits: 10,
  minSlip: 0,
  maxSlip: 1000,
  maxName: 120,
  /** trading_core refuses more bars than this per instrument (too_many_bars); shown as a hint. */
  maxBars: 50_000,
} as const

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const NUMBER_RE = /^-?\d+(\.\d+)?$/
const INST_RE = /^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$/

export interface IndicatorDraft {
  name: string
  kind: Kind
  /** Text, so a half-typed window can be shown and refused. */
  window: string
}

export interface ConditionDraft {
  left: string
  op: Op
  /** An indicator name, `close`, or (with `num`) the number's text. */
  right: string
  num: boolean
}

export interface FormState {
  name: string
  side: Side
  insts: string[]
  bar: Bar
  /** Baghdad calendar days, YYYY-MM-DD; the period runs from 00:00 Baghdad on `from` to 00:00 on `to`. */
  from: string
  to: string
  indicators: IndicatorDraft[]
  entry: ConditionDraft[]
  exit: ConditionDraft[]
  stop: string
  tp: string
  splits: number
  slip: number
}

/** The design's starting rule: a 10/30 SMA cross on BTC with a 3% stop. */
export function initialForm(today: string): FormState {
  return {
    name: 'sma_10_30_cross',
    side: 'long',
    insts: ['BTC-USDT-SWAP'],
    bar: '1H',
    from: shiftDay(today, -90),
    to: today,
    indicators: [
      { name: 'sma10', kind: 'sma', window: '10' },
      { name: 'sma30', kind: 'sma', window: '30' },
    ],
    entry: [{ left: 'sma10', op: 'crosses_above', right: 'sma30', num: false }],
    exit: [{ left: 'sma10', op: 'crosses_below', right: 'sma30', num: false }],
    stop: '3',
    tp: '',
    splits: 3,
    slip: 2,
  }
}

const DAY = 86_400_000

/** YYYY-MM-DD plus `days` calendar days. */
export function shiftDay(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10)
}

const isDay = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`))

/** 00:00 Baghdad on a calendar day, with its offset (the API refuses a naive datetime). */
export const baghdadMidnight = (day: string) => `${day}T00:00:00+03:00`

const BAR_MINUTES: Record<Bar, number> = { '1m': 1, '5m': 5, '15m': 15, '1H': 60, '4H': 240, '1D': 1440 }

/** Bars per instrument in the period, as trading_core counts them ((end − start) / bar). */
export function barCount(f: Pick<FormState, 'from' | 'to' | 'bar'>): number {
  if (!isDay(f.from) || !isDay(f.to)) return 0
  const ms = Date.parse(`${f.to}T00:00:00Z`) - Date.parse(`${f.from}T00:00:00Z`)
  return Math.max(0, Math.floor(ms / (BAR_MINUTES[f.bar] * 60_000)))
}

// ------------------------------------------------------------------------------------ the request

export interface Condition {
  left: string
  op: Op
  right: string | number
}

export interface RuleSpec {
  name: string
  side: Side
  indicators: Record<string, { kind: Kind; window: number }>
  entry: Condition[]
  exit: Condition[]
  stop_pct: number | null
  take_profit_pct: number | null
}

export interface BacktestRequest {
  rule_spec: RuleSpec
  inst_ids: string[]
  bar: Bar
  period: { start: string; end: string }
  splits: number
  slippage_bps: number
}

const optionalNumber = (s: string) => (s.trim() === '' ? null : Number(s.trim()))
const toCondition = (c: ConditionDraft): Condition => ({ left: c.left, op: c.op, right: c.num ? Number(c.right.trim()) : c.right })

/** The POST /api/backtests body for a form that passed validate(). */
export function toRequest(f: FormState): BacktestRequest {
  return {
    rule_spec: {
      name: f.name.trim(),
      side: f.side,
      indicators: Object.fromEntries(f.indicators.map((i) => [i.name, { kind: i.kind, window: Number(i.window) }])),
      entry: f.entry.map(toCondition),
      exit: f.exit.map(toCondition),
      stop_pct: optionalNumber(f.stop),
      take_profit_pct: optionalNumber(f.tp),
    },
    inst_ids: [...f.insts],
    bar: f.bar,
    period: { start: baghdadMidnight(f.from), end: baghdadMidnight(f.to) },
    splits: f.splits,
    slippage_bps: f.slip,
  }
}

/** Baghdad calendar day of an ISO datetime (the API returns UTC `Z`). */
export function baghdadDayOf(iso: string): string {
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? '' : new Date(ms + 3 * 3_600_000).toISOString().slice(0, 10)
}

/** A stored run's request back into the form ("Edit and run again"). */
export function fromRequest(r: BacktestRequest): FormState {
  const fromCond = (c: Condition): ConditionDraft =>
    typeof c.right === 'number' ? { left: c.left, op: c.op, right: String(c.right), num: true } : { left: c.left, op: c.op, right: c.right, num: false }
  const bar = (BARS as readonly string[]).includes(r.bar) ? r.bar : '1H'
  return {
    name: r.rule_spec.name ?? '',
    side: r.rule_spec.side === 'short' ? 'short' : 'long',
    insts: [...(r.inst_ids ?? [])],
    bar,
    from: baghdadDayOf(r.period?.start ?? ''),
    to: baghdadDayOf(r.period?.end ?? ''),
    indicators: Object.entries(r.rule_spec.indicators ?? {}).map(([name, v]) => ({ name, kind: v.kind, window: String(v.window) })),
    entry: (r.rule_spec.entry ?? []).map(fromCond),
    exit: (r.rule_spec.exit ?? []).map(fromCond),
    stop: r.rule_spec.stop_pct === null || r.rule_spec.stop_pct === undefined ? '' : String(r.rule_spec.stop_pct),
    tp: r.rule_spec.take_profit_pct === null || r.rule_spec.take_profit_pct === undefined ? '' : String(r.rule_spec.take_profit_pct),
    splits: r.splits ?? 3,
    slip: r.slippage_bps ?? 2,
  }
}

// ------------------------------------------------------------------------------------- validation

/**
 * Where an error shows: `name`, `side`, `insts`, `bar`, `period`, `splits`, `slip`, `stop`, `tp`,
 * `indicators` (the list), `ind.<i>` (one indicator row), `entry` / `exit` (the list) and
 * `entry.<i>` / `exit.<i>` (one condition row).
 */
export type FieldKey = string

/** A client-side reason: an i18n key under `backtester:err.` plus its values. */
export interface ClientError {
  key: string
  values?: Record<string, string | number>
}

export function validate(f: FormState): Record<FieldKey, ClientError> {
  const out: Record<FieldKey, ClientError> = {}
  const name = f.name.trim()
  if (name === '') out.name = { key: 'nameRequired' }
  else if (name.length > LIMITS.maxName) out.name = { key: 'nameLong', values: { max: LIMITS.maxName } }

  if (f.insts.length < 1 || f.insts.length > LIMITS.maxInsts) out.insts = { key: 'insts' }
  else if (new Set(f.insts).size !== f.insts.length || f.insts.some((i) => !INST_RE.test(i))) out.insts = { key: 'insts' }

  if (!isDay(f.from) || !isDay(f.to)) out.period = { key: 'periodMissing' }
  else if (f.from >= f.to) out.period = { key: 'period' }

  if (f.indicators.length > LIMITS.maxIndicators) out.indicators = { key: 'indMany', values: { max: LIMITS.maxIndicators } }
  const seen = new Set<string>()
  f.indicators.forEach((ind, i) => {
    const w = ind.window.trim()
    if (ind.name === CLOSE) out[`ind.${i}`] = { key: 'indClose' }
    else if (!NAME_RE.test(ind.name)) out[`ind.${i}`] = { key: 'indName' }
    else if (seen.has(ind.name)) out[`ind.${i}`] = { key: 'indDup' }
    else if (!/^\d+$/.test(w) || Number(w) < LIMITS.minWindow || Number(w) > LIMITS.maxWindow) {
      out[`ind.${i}`] = { key: 'window', values: { min: LIMITS.minWindow, max: LIMITS.maxWindow } }
    }
    seen.add(ind.name)
  })
  const known = new Set([...f.indicators.map((i) => i.name), CLOSE])

  for (const list of ['entry', 'exit'] as const) {
    const max = list === 'entry' ? LIMITS.maxEntry : LIMITS.maxExit
    if (list === 'entry' && f.entry.length < 1) out.entry = { key: 'entryNone' }
    if (f[list].length > max) out[list] = { key: 'condMany', values: { max } }
    f[list].forEach((c, i) => {
      if (!known.has(c.left)) out[`${list}.${i}`] = { key: 'unknown', values: { name: c.left } }
      else if (c.num && !NUMBER_RE.test(c.right.trim())) out[`${list}.${i}`] = { key: 'number' }
      else if (!c.num && !known.has(c.right)) out[`${list}.${i}`] = { key: 'unknown', values: { name: c.right } }
    })
  }

  const stop = f.stop.trim()
  if (stop !== '' && (!NUMBER_RE.test(stop) || Number(stop) <= 0 || Number(stop) >= 100)) out.stop = { key: 'stop' }
  const tp = f.tp.trim()
  if (tp !== '' && (!NUMBER_RE.test(tp) || Number(tp) <= 0)) out.tp = { key: 'tp' }

  if (!Number.isInteger(f.splits) || f.splits < LIMITS.minSplits || f.splits > LIMITS.maxSplits) out.splits = { key: 'splits' }
  if (!Number.isFinite(f.slip) || f.slip < LIMITS.minSlip || f.slip > LIMITS.maxSlip) out.slip = { key: 'slip' }
  return out
}

/**
 * The form field a Laravel 422 error path belongs to, e.g. `rule_spec.entry.0.right` → `entry.0`,
 * `rule_spec.indicators.sma10.window` → the row of indicator sma10, `period.end` → `period`,
 * `inst_ids.2` → `insts`. Null for a path the form has no field for (it goes in the banner).
 */
export function serverFieldKey(path: string, f: FormState): FieldKey | null {
  const p = path.split('.')
  if (p[0] === 'rule_spec') {
    const k = p[1]
    if (k === undefined) return null
    if (k === 'name') return 'name'
    if (k === 'side') return 'side'
    if (k === 'stop_pct') return 'stop'
    if (k === 'take_profit_pct') return 'tp'
    if (k === 'indicators') {
      const i = p[2] === undefined ? -1 : f.indicators.findIndex((x) => x.name === p[2])
      return i >= 0 ? `ind.${i}` : 'indicators'
    }
    if (k === 'entry' || k === 'exit') {
      const i = p[2] === undefined ? NaN : Number(p[2])
      return Number.isInteger(i) && i >= 0 && i < f[k].length ? `${k}.${i}` : k
    }
    return null
  }
  if (p[0] === 'inst_ids') return 'insts'
  if (p[0] === 'bar') return 'bar'
  if (p[0] === 'period') return 'period'
  if (p[0] === 'splits') return 'splits'
  if (p[0] === 'slippage_bps') return 'slip'
  return null
}

/** Laravel's 422 `errors` split into reasons per form field and the ones with no field. */
export function mapServerErrors(errors: Record<string, string[]>, f: FormState): { fields: Record<FieldKey, string>; other: string[] } {
  const fields: Record<FieldKey, string> = {}
  const other: string[] = []
  for (const [path, reasons] of Object.entries(errors)) {
    const reason = reasons[0]
    if (!reason) continue
    const key = serverFieldKey(path, f)
    if (key === null) other.push(reason)
    else fields[key] ??= reason
  }
  return { fields, other }
}

/** The design's base symbol: `BTC` of BTC-USDT-SWAP. */
export const baseOf = (instId: string) => instId.split('-')[0] ?? instId

/** Renames an indicator and every condition that named it (design: the name field's change). */
export function renameIndicator(f: FormState, index: number, next: string): FormState {
  const old = f.indicators[index]?.name
  if (old === undefined) return f
  const swap = (c: ConditionDraft): ConditionDraft => ({
    ...c,
    left: c.left === old ? next : c.left,
    right: !c.num && c.right === old ? next : c.right,
  })
  return {
    ...f,
    indicators: f.indicators.map((x, i) => (i === index ? { ...x, name: next } : x)),
    entry: f.entry.map(swap),
    exit: f.exit.map(swap),
  }
}

/** A new indicator the design adds: an EMA whose window grows with the list. */
export function nextIndicator(f: FormState): IndicatorDraft {
  let w = 20 + f.indicators.length * 10
  const names = new Set(f.indicators.map((i) => i.name))
  while (names.has(`ema${w}`)) w += 10
  return { name: `ema${w}`, kind: 'ema', window: String(w) }
}

/** A new condition the design adds: close above the first indicator. */
export function nextCondition(f: FormState): ConditionDraft {
  return { left: CLOSE, op: 'above', right: f.indicators[0]?.name ?? CLOSE, num: false }
}
