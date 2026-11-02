import type { Outcome } from '../../journal/api'
import { outcomeOf, type TradeRecord } from '../../journal/trade'

/*
 * Readers for patch K's War Room cards (ATL-203): the Portfolio Manager's `post_mortem` card
 * (ATL-194, docs/handoff/ATL-194-post-mortems.md) and the Historian's `weekly_review` card
 * (ATL-199, docs/handoff/ATL-199-weekly-review.md). Every figure is a decimal string written by
 * code; strings stay strings for printing and Number() is used only to compare and to place marks.
 *
 * The close label, outcome, plan and TP1 rules are the Journal's (journal/trade.ts, read through
 * PostMortem.trade), so "Closed at TP2" reads the same on the card and in the Journal (design §2
 * "A label is used verbatim wherever it appears"). Tag words come from journal/format useTagLabel.
 */

export type { Outcome }
export type Side = 'long' | 'short'

export interface PmTarget {
  price: string
  pct: string | null
}

export interface PostMortem {
  journalId: string
  proposalId: string | null
  instId: string
  side: Side
  outcome: Outcome
  asOf: string
  lessonUpdate: boolean
  expected: {
    entry: string | null
    entryZone: [string, string] | null
    stop: string | null
    targets: PmTarget[]
    expectedR: string | null
    probability: string | null
    timeStopMinutes: number | null
  }
  actual: {
    avgEntry: string | null
    avgExit: string | null
    realisedUsd: string | null
    realisedR: string | null
    mfeR: string | null
    maeR: string | null
    holdingMinutes: number | null
    closeReason: string | null
  }
  lesson: string | null
  lessonPending: boolean
  tags: string[]
  /** The card's side / outcome / expected / actual, as the shared trade rules read them. */
  trade: TradeRecord
}

export interface ReviewGroup {
  key: string
  count: number
  wins: number
  totalR: string | null
  totalUsd: string | null
}

export interface WeeklyReview {
  asOf: string
  weekStart: string
  from: string
  to: string
  noTrades: boolean
  status: string
  totals: ReviewGroup
  bySetup: ReviewGroup[]
  byAssetClass: ReviewGroup[]
  byAgent: ReviewGroup[]
  paper: { filled: number; totalR: string | null } | null
  verdict: string | null
  suggestion: { change: string; reason: string | null; where: 'risk' | 'markets' | 'money' | null } | null
}

type Obj = Record<string, unknown>

const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : null)
const int = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  return Number.isFinite(n) ? Math.trunc(n) : null
}
export function readPostMortem(card: unknown): PostMortem | null {
  if (!isObj(card) || card.type !== 'post_mortem') return null
  const journalId = str(card.journal_id)
  const instId = str(card.inst_id)
  const side = card.side
  if (!journalId || !instId || (side !== 'long' && side !== 'short')) return null
  const x = isObj(card.expected) ? card.expected : {}
  const a = isObj(card.actual) ? card.actual : {}
  const zone = Array.isArray(x.entry_zone) ? x.entry_zone.map(str) : []
  const targets = (Array.isArray(x.take_profits) ? x.take_profits : [])
    .filter(isObj)
    .map((tp) => ({ price: str(tp.price), pct: str(tp.pct) }))
    .filter((tp): tp is PmTarget => tp.price !== null)
  const realisedR = str(a.realised_r) ?? str(card.realised_r)
  const realisedUsd = str(a.realised_pnl_usd)
  const tagList = Array.isArray(card.tag_list) ? card.tag_list.filter((v): v is string => typeof v === 'string') : []
  const trade: TradeRecord = {
    side,
    outcome: typeof card.outcome === 'string' ? card.outcome : null,
    tags: tagList,
    expected: {
      entry: str(x.entry),
      entry_zone: zone.length === 2 && zone[0] && zone[1] ? [zone[0], zone[1]] : null,
      stop: str(x.stop),
      take_profits: targets,
    },
    actual: { avg_entry: str(a.avg_entry), realised_r: realisedR, realised_pnl_usd: realisedUsd, mfe_r: str(a.mfe_r), close_reason: str(a.close_reason) },
  }
  return {
    journalId,
    proposalId: str(card.proposal_id),
    instId,
    side,
    outcome: outcomeOf(trade),
    asOf: str(card.as_of) ?? str(a.closed_at) ?? new Date(0).toISOString(),
    lessonUpdate: card.lesson_update === true,
    expected: {
      entry: str(x.entry),
      entryZone: zone.length === 2 && zone[0] && zone[1] ? [zone[0], zone[1]] : null,
      stop: str(x.stop),
      targets,
      expectedR: str(x.expected_r),
      probability: str(x.probability),
      timeStopMinutes: int(x.time_stop_minutes),
    },
    actual: {
      avgEntry: str(a.avg_entry),
      avgExit: str(a.avg_exit),
      realisedUsd,
      realisedR,
      mfeR: str(a.mfe_r),
      maeR: str(a.mae_r),
      holdingMinutes: int(a.holding_minutes),
      closeReason: str(a.close_reason),
    },
    lesson: str(card.lesson),
    lessonPending: card.lesson_pending === true,
    tags: tagList,
    trade,
  }
}

/** The plan as the ladder draws it: the entry zone (a single entry as a zero-width zone), the stop, the targets. */
export function ladderOf(pm: PostMortem): { zone: [string, string]; stop: string; targets: PmTarget[] } | null {
  const x = pm.expected
  const zone: [string, string] | null = x.entryZone ?? (x.entry ? [x.entry, x.entry] : pm.actual.avgEntry ? [pm.actual.avgEntry, pm.actual.avgEntry] : null)
  return zone && x.stop ? { zone, stop: x.stop, targets: x.targets } : null
}

function readGroup(v: unknown): ReviewGroup | null {
  if (!isObj(v)) return null
  return {
    key: str(v.key) ?? 'unknown',
    count: int(v.count) ?? 0,
    wins: int(v.wins) ?? 0,
    totalR: str(v.total_r),
    totalUsd: str(v.total_usd),
  }
}

const groups = (v: unknown): ReviewGroup[] => (Array.isArray(v) ? v.map(readGroup).filter((g): g is ReviewGroup => g !== null) : [])

const WHERE = ['risk', 'markets', 'money'] as const

export function readWeeklyReview(card: unknown): WeeklyReview | null {
  if (!isObj(card) || (card.type !== 'weekly_review' && card.kind !== 'weekly_review')) return null
  const w = isObj(card.window) ? card.window : {}
  const from = str(w.from)
  const to = str(w.to)
  const asOf = str(card.as_of) ?? to
  if (!from || !to || !asOf) return null
  const count = int(card.trade_count) ?? 0
  const noTrades = card.no_trades === true || card.status === 'no_trades' || count === 0
  const totals = readGroup({ key: 'all', ...(isObj(card.totals) ? card.totals : {}) })!
  const pt = isObj(card.paper_trades) ? card.paper_trades : null
  const s = isObj(card.suggestion) ? card.suggestion : null
  const change = s ? str(s.change) : null
  const where = s && typeof s.where === 'string' && (WHERE as readonly string[]).includes(s.where) ? (s.where as (typeof WHERE)[number]) : null
  return {
    asOf,
    weekStart: str(card.week_start) ?? '',
    from,
    to,
    noTrades,
    status: str(card.status) ?? (noTrades ? 'no_trades' : 'ok'),
    totals,
    bySetup: groups(card.by_setup),
    byAssetClass: groups(card.by_asset_class),
    byAgent: groups(card.by_agent),
    paper: pt ? { filled: int(pt.filled) ?? 0, totalR: str(pt.total_r) } : null,
    verdict: str(card.verdict),
    suggestion: change ? { change, reason: s ? str(s.reason) : null, where } : null,
  }
}
