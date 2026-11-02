import type { JournalExpected, JournalActual, Outcome } from './api'

/*
 * The rules that read one closed trade, shared by the Journal (journal_entries.data) and the War
 * Room's post_mortem card (ATL-194 writes both with the same side / outcome / expected / actual
 * fields). One copy, so "Closed at TP2" and "Won" read the same everywhere (design §2 "A label is
 * used verbatim wherever it appears"). Decimal strings stay strings; Number() only compares.
 */

export interface TradeRecord {
  side?: 'long' | 'short' | string | null
  outcome?: Outcome | string | null
  realised_r?: string | null
  tags?: readonly string[]
  expected?: JournalExpected | null
  actual?: JournalActual | null
}

export const num = (v: unknown): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v !== 'string' || v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** |R| under this is a scratch (design §7 "Scratch means |R| < 0.1"; ATL-194 scratch_r). */
export const SCRATCH_R = 0.1

export function realisedR(t: TradeRecord): string | null {
  const v = t.actual?.realised_r ?? t.realised_r
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

export function sideOf(t: TradeRecord): 'long' | 'short' | null {
  return t.side === 'long' || t.side === 'short' ? t.side : null
}

export function outcomeOf(t: TradeRecord): Outcome {
  const o = t.outcome
  if (o === 'win' || o === 'loss' || o === 'scratch') return o
  const tags = t.tags ?? []
  for (const x of ['win', 'loss', 'scratch'] as const) if (tags.includes(x) || tags.includes(`outcome:${x}`)) return x
  const r = num(realisedR(t))
  if (r !== null) return Math.abs(r) < SCRATCH_R ? 'scratch' : r > 0 ? 'win' : 'loss'
  const pnl = num(t.actual?.realised_pnl_usd)
  return pnl === null || pnl === 0 ? 'scratch' : pnl > 0 ? 'win' : 'loss'
}

/** The geometry of the plan in R: the entry the risk is measured from, 1R in price, and the targets. */
export interface Plan {
  ref: number
  unit: number
  dir: 1 | -1
  targetsR: number[]
}

export function planOf(t: TradeRecord): Plan | null {
  const x = t.expected
  const side = sideOf(t)
  if (!x || !side) return null
  const zone = (x.entry_zone ?? []).map(num).filter((v): v is number => v !== null)
  const ref = num(x.entry) ?? (zone.length === 2 ? (zone[0]! + zone[1]!) / 2 : num(t.actual?.avg_entry))
  const stop = num(x.stop)
  if (ref === null || stop === null || ref === stop) return null
  const dir = side === 'long' ? 1 : -1
  const unit = Math.abs(ref - stop)
  const targetsR = (x.take_profits ?? [])
    .map((tp) => num(tp.price))
    .filter((v): v is number => v !== null)
    .map((p) => (dir * (p - ref)) / unit)
  return { ref, unit, dir, targetsR }
}

/** The furthest target the price reached at its best (1-based), or 0. */
export function targetReached(t: TradeRecord): number {
  const plan = planOf(t)
  const mfe = num(t.actual?.mfe_r)
  if (!plan || mfe === null) return 0
  let n = 0
  plan.targetsR.forEach((r, i) => {
    if (mfe >= r - 1e-9) n = i + 1
  })
  return n
}

/** TP1 before the stop: the best point reached TP1 and the stop did not close it (prototype rule). */
export function tp1Hit(t: TradeRecord): boolean | null {
  const plan = planOf(t)
  const mfe = num(t.actual?.mfe_r)
  if (!plan || mfe === null || plan.targetsR.length === 0) return null
  return mfe >= plan.targetsR[0]! - 1e-9 && t.actual?.close_reason !== 'stop_loss'
}

/*
 * How the trade closed, as one verbatim label key (journal:rule.*). ATL-194 close_reason values:
 * liquidation, adl, kill, a PM rule (time_stop, invalidation, pm_close, …), stop_loss,
 * take_profit, manual, external.
 */
export type CloseLabel = 'stop' | 'tp1' | 'tp2' | 'tp3' | 'tp' | 'time_stop' | 'invalidation' | 'owner' | 'kill' | 'liquidation' | 'adl' | 'external' | 'pm'

export function closeLabel(t: TradeRecord): CloseLabel | null {
  const reason = t.actual?.close_reason
  switch (reason) {
    case null:
    case undefined:
    case '':
      return null
    case 'stop_loss':
      return 'stop'
    case 'take_profit': {
      const n = targetReached(t)
      return n === 1 ? 'tp1' : n === 2 ? 'tp2' : n === 3 ? 'tp3' : 'tp'
    }
    case 'time_stop':
    case 'invalidation':
    case 'kill':
    case 'liquidation':
    case 'adl':
    case 'external':
      return reason
    case 'manual':
      return 'owner'
    default:
      return 'pm'
  }
}
