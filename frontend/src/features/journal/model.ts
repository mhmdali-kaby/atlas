import type { JournalEntry, Outcome } from './api'

/*
 * What the Journal draws, derived from one journal_entries row. Decimal strings stay strings for
 * printing; Number() is used only to compare and to place marks on the mini ladder.
 */

/** ATL-194 microsecond timestamps → ms (Date.parse reads only milliseconds). */
export function isoMs(ts: string): number {
  return Date.parse(ts.replace(/(\.\d{3})\d+/, '$1'))
}

const num = (v: unknown): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v !== 'string' || v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** |R| under this is a scratch (design §7 "Scratch means |R| < 0.1"; ATL-194 scratch_r). */
const SCRATCH_R = 0.1

export function outcomeOf(e: JournalEntry): Outcome {
  const o = e.data.outcome
  if (o === 'win' || o === 'loss' || o === 'scratch') return o
  for (const t of ['win', 'loss', 'scratch'] as const) if (e.tags.includes(t) || e.tags.includes(`outcome:${t}`)) return t
  const r = num(realisedR(e))
  if (r !== null) return Math.abs(r) < SCRATCH_R ? 'scratch' : r > 0 ? 'win' : 'loss'
  const pnl = num(e.data.actual?.realised_pnl_usd)
  return pnl === null || pnl === 0 ? 'scratch' : pnl > 0 ? 'win' : 'loss'
}

export function realisedR(e: JournalEntry): string | null {
  const v = e.data.actual?.realised_r ?? e.data.realised_r
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

export function sideOf(e: JournalEntry): 'long' | 'short' | null {
  const s = e.data.side
  return s === 'long' || s === 'short' ? s : null
}

/** When the trade closed (the row's own time when the post-mortem carries none). */
export function closedAt(e: JournalEntry): string {
  return e.data.actual?.closed_at ?? e.created_at
}

export function lessonPending(e: JournalEntry): boolean {
  return e.data.lesson_pending === true
}

export function lessonText(e: JournalEntry): string | null {
  const v = e.lessons_md ?? e.data.lesson
  return typeof v === 'string' && v.trim() !== '' ? v : null
}

/*
 * How the trade closed, as one verbatim label key (design §2 "A label is used verbatim wherever it
 * appears"). ATL-194 close_reason values: liquidation, adl, kill, a PM rule (time_stop,
 * invalidation, pm_close, …), stop_loss, take_profit, manual, external.
 */
export type CloseLabel =
  | 'stop'
  | 'tp1'
  | 'tp2'
  | 'tp3'
  | 'tp'
  | 'time_stop'
  | 'invalidation'
  | 'owner'
  | 'kill'
  | 'liquidation'
  | 'adl'
  | 'external'
  | 'pm'

export function closeLabel(e: JournalEntry): CloseLabel | null {
  const reason = e.data.actual?.close_reason
  switch (reason) {
    case null:
    case undefined:
    case '':
      return null
    case 'stop_loss':
      return 'stop'
    case 'take_profit': {
      const n = targetReached(e)
      return n === 1 ? 'tp1' : n === 2 ? 'tp2' : n === 3 ? 'tp3' : 'tp'
    }
    case 'time_stop':
      return 'time_stop'
    case 'invalidation':
      return 'invalidation'
    case 'manual':
      return 'owner'
    case 'kill':
      return 'kill'
    case 'liquidation':
      return 'liquidation'
    case 'adl':
      return 'adl'
    case 'external':
      return 'external'
    default:
      return 'pm'
  }
}

/** The geometry of the plan in R: the entry the risk is measured from, 1R in price, and the targets. */
export interface Plan {
  ref: number
  unit: number
  dir: 1 | -1
  targetsR: number[]
}

export function planOf(e: JournalEntry): Plan | null {
  const x = e.data.expected
  const side = sideOf(e)
  if (!x || !side) return null
  const zone = (x.entry_zone ?? []).map(num).filter((v): v is number => v !== null)
  const ref = num(x.entry) ?? (zone.length === 2 ? (zone[0]! + zone[1]!) / 2 : num(e.data.actual?.avg_entry))
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
export function targetReached(e: JournalEntry): number {
  const plan = planOf(e)
  const mfe = num(e.data.actual?.mfe_r)
  if (!plan || mfe === null) return 0
  let n = 0
  plan.targetsR.forEach((r, i) => {
    if (mfe >= r - 1e-9) n = i + 1
  })
  return n
}

/** TP1 before the stop: the best point reached TP1 and the stop did not close it (prototype rule). */
export function tp1Hit(e: JournalEntry): boolean | null {
  const plan = planOf(e)
  const mfe = num(e.data.actual?.mfe_r)
  if (!plan || mfe === null || plan.targetsR.length === 0) return null
  return mfe >= plan.targetsR[0]! - 1e-9 && e.data.actual?.close_reason !== 'stop_loss'
}

/** Where the mini ladder puts each mark, in percent of its width (left to right in both languages). */
export interface MiniGeometry {
  loss: [number, number]
  gain: [number, number]
  band: [number, number] | null
  zero: number
  exit: number
}

export function miniGeometry(e: JournalEntry): MiniGeometry | null {
  const r = num(realisedR(e))
  if (r === null) return null
  const mfe = num(e.data.actual?.mfe_r)
  const mae = num(e.data.actual?.mae_r)
  const plan = planOf(e)
  const lastTp = plan && plan.targetsR.length ? Math.max(...plan.targetsR) : Math.max(1, mfe ?? 0, r)
  const lo = Math.min(-1.15, (mae ?? 0) - 0.1, r - 0.1)
  const hi = Math.max(lastTp, mfe ?? 0, r) + 0.15
  const X = (v: number) => Math.round(((v - lo) / (hi - lo)) * 1000) / 10
  return {
    loss: [X(-1), X(0)],
    gain: [X(0), X(lastTp)],
    band: mfe !== null && mae !== null ? [X(Math.min(mae, mfe)), X(Math.max(mae, mfe))] : null,
    zero: X(0),
    exit: X(r),
  }
}

/*
 * Tags (ATL-194): `setup:<v>`, `asset:<v>`, `regime:<v>`, `catalyst:<v>`, `outcome:<v>` and the bare
 * outcome. The outcome pair is shown by the badge, so only the four groups are drawn as chips.
 */
export const TAG_GROUPS = ['setup', 'asset', 'regime', 'catalyst'] as const
export type TagGroup = (typeof TAG_GROUPS)[number]

/** The vocabularies in config/defaults.yaml agents.portfolio_manager.postmortem (and asset classes). */
export const TAG_VALUES: Record<TagGroup, readonly string[]> = {
  setup: ['breakout', 'pullback_to_support', 'breakdown', 'rally_to_resistance', 'range_fade', 'squeeze_expansion', 'reversal_divergence', 'other'],
  asset: ['crypto', 'stock', 'etf', 'index', 'commodity', 'other'],
  regime: ['trend', 'range', 'high_vol', 'low_vol'],
  catalyst: [
    'none',
    'earnings',
    'guidance',
    'macro_release',
    'central_bank',
    'regulation',
    'hack_exploit',
    'listing',
    'delisting',
    'etf_flow',
    'm_and_a',
    'lawsuit',
    'product',
    'partnership',
    'token_unlock',
    'geopolitics',
    'supply_shock',
    'funding',
    'other',
  ],
}

export function splitTag(tag: string): { group: TagGroup | null; value: string } {
  const i = tag.indexOf(':')
  if (i < 0) return { group: null, value: tag }
  const g = tag.slice(0, i)
  return { group: (TAG_GROUPS as readonly string[]).includes(g) ? (g as TagGroup) : null, value: tag.slice(i + 1) }
}

/** The chips to draw: setup, asset, regime, catalyst first (in that order), then any other tag; never the outcome pair or `unknown`. */
export function displayTags(tags: readonly string[]): string[] {
  const rank = (t: string) => {
    const g = splitTag(t).group
    return g ? TAG_GROUPS.indexOf(g) : TAG_GROUPS.length
  }
  return tags
    .filter((t) => !['win', 'loss', 'scratch'].includes(t) && !t.startsWith('outcome:') && !t.endsWith(':unknown'))
    .sort((a, b) => rank(a) - rank(b))
}

/** "This week" ends on the coming Sunday 18:00 Baghdad (15:00 UTC); each group is one review week. */
const WEEK = 7 * 86_400_000
const ANCHOR = Date.parse('2026-08-30T15:00:00Z') // a Sunday, 18:00 Baghdad

export function weekEnd(ms: number): number {
  return ANCHOR + (Math.floor((ms - ANCHOR) / WEEK) + 1) * WEEK
}

/** The voters' result: right when "take" (or "change") came before a win or "skip" before a loss; none for a scratch. */
export function voteRight(vote: string, outcome: Outcome): boolean | null {
  if (outcome === 'scratch') return null
  return vote === 'skip' ? outcome === 'loss' : outcome === 'win'
}
