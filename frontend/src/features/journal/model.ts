import type { JournalEntry, Outcome } from './api'
import {
  closeLabel as tradeCloseLabel,
  num,
  outcomeOf as tradeOutcome,
  planOf as tradePlan,
  realisedR as tradeRealisedR,
  sideOf as tradeSide,
  targetReached as tradeTargetReached,
  tp1Hit as tradeTp1Hit,
  type CloseLabel,
  type Plan,
  type TradeRecord,
} from './trade'

/*
 * What the Journal draws, derived from one journal_entries row. Decimal strings stay strings for
 * printing; Number() is used only to compare and to place marks on the mini ladder.
 */

/** ATL-194 microsecond timestamps → ms (Date.parse reads only milliseconds). */
export function isoMs(ts: string): number {
  return Date.parse(ts.replace(/(\.\d{3})\d+/, '$1'))
}

/** The trade fields ATL-194 writes on the row, in the shape the shared rules read (trade.ts). */
export function tradeOf(e: JournalEntry): TradeRecord {
  return { ...e.data, tags: e.tags }
}

export type { CloseLabel, Plan }

export const outcomeOf = (e: JournalEntry): Outcome => tradeOutcome(tradeOf(e))
export const realisedR = (e: JournalEntry): string | null => tradeRealisedR(tradeOf(e))
export const sideOf = (e: JournalEntry): 'long' | 'short' | null => tradeSide(tradeOf(e))
export const closeLabel = (e: JournalEntry): CloseLabel | null => tradeCloseLabel(tradeOf(e))
export const planOf = (e: JournalEntry): Plan | null => tradePlan(tradeOf(e))
export const targetReached = (e: JournalEntry): number => tradeTargetReached(tradeOf(e))
export const tp1Hit = (e: JournalEntry): boolean | null => tradeTp1Hit(tradeOf(e))

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
