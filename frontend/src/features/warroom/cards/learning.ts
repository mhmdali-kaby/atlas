import { useTranslation } from 'react-i18next'

/*
 * Readers for patch K's War Room cards (ATL-203): the Portfolio Manager's `post_mortem` card
 * (ATL-194, docs/handoff/ATL-194-post-mortems.md) and the Historian's `weekly_review` card
 * (ATL-199, docs/handoff/ATL-199-weekly-review.md). Every figure is a decimal string written by
 * code; strings stay strings for printing and Number() is used only to compare and to place marks.
 *
 * The close label and outcome rules mirror the Journal's (ATL-202 journal/model.ts closeLabel,
 * outcomeOf, tp1Hit), so "Closed at TP2" reads the same on the card and in the Journal (design
 * §2 "A label is used verbatim wherever it appears").
 */

export type Outcome = 'win' | 'loss' | 'scratch'
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
export const num = (v: string | null | undefined): number | null => {
  if (v === null || v === undefined || v.trim() === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** |R| under this is a scratch (ATL-194 scratch_r, design §7). */
const SCRATCH_R = 0.1

function outcomeOf(o: unknown, r: string | null, usd: string | null): Outcome {
  if (o === 'win' || o === 'loss' || o === 'scratch') return o
  const rv = num(r)
  if (rv !== null) return Math.abs(rv) < SCRATCH_R ? 'scratch' : rv > 0 ? 'win' : 'loss'
  const p = num(usd)
  return p === null || p === 0 ? 'scratch' : p > 0 ? 'win' : 'loss'
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
  return {
    journalId,
    proposalId: str(card.proposal_id),
    instId,
    side,
    outcome: outcomeOf(card.outcome, realisedR, realisedUsd),
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
  }
}

/** The plan on the ladder: the entry zone (a single entry as a zero-width zone), the stop, the targets. */
export interface PlanGeometry {
  zone: [string, string]
  stop: string
  targets: PmTarget[]
  ref: number
  unit: number
  dir: 1 | -1
  targetsR: number[]
}

export function planOf(pm: PostMortem): PlanGeometry | null {
  const x = pm.expected
  const zone: [string, string] | null = x.entryZone ?? (x.entry ? [x.entry, x.entry] : pm.actual.avgEntry ? [pm.actual.avgEntry, pm.actual.avgEntry] : null)
  const stop = x.stop
  if (!zone || !stop) return null
  const ref = (Number(zone[0]) + Number(zone[1])) / 2
  const unit = Math.abs(ref - Number(stop))
  if (!Number.isFinite(ref) || !unit) return null
  const dir = pm.side === 'long' ? 1 : -1
  const targetsR = x.targets.map((tp) => (dir * (Number(tp.price) - ref)) / unit)
  return { zone, stop, targets: x.targets, ref, unit, dir, targetsR }
}

/** The furthest target the price reached at its best (1-based), or 0. */
function targetReached(pm: PostMortem): number {
  const plan = planOf(pm)
  const mfe = num(pm.actual.mfeR)
  if (!plan || mfe === null) return 0
  let n = 0
  plan.targetsR.forEach((r, i) => {
    if (mfe >= r - 1e-9) n = i + 1
  })
  return n
}

/** TP1 before the stop: the best point reached TP1 and the stop did not close it (Journal rule). */
export function tp1Hit(pm: PostMortem): boolean | null {
  const plan = planOf(pm)
  const mfe = num(pm.actual.mfeR)
  if (!plan || mfe === null || plan.targetsR.length === 0) return null
  return mfe >= plan.targetsR[0]! - 1e-9 && pm.actual.closeReason !== 'stop_loss'
}

export type CloseLabel = 'stop' | 'tp1' | 'tp2' | 'tp3' | 'tp' | 'time_stop' | 'invalidation' | 'owner' | 'kill' | 'liquidation' | 'adl' | 'external' | 'pm'

/** How the trade closed, as one label key (pmo.rule.*, the same words as journal:rule.*). */
export function closeLabel(pm: PostMortem): CloseLabel | null {
  switch (pm.actual.closeReason) {
    case null:
      return null
    case 'stop_loss':
      return 'stop'
    case 'take_profit': {
      const n = targetReached(pm)
      return n === 1 ? 'tp1' : n === 2 ? 'tp2' : n === 3 ? 'tp3' : 'tp'
    }
    case 'time_stop':
    case 'invalidation':
    case 'kill':
    case 'liquidation':
    case 'adl':
    case 'external':
      return pm.actual.closeReason
    case 'manual':
      return 'owner'
    default:
      return 'pm'
  }
}

export const TAG_GROUPS = ['setup', 'asset', 'regime', 'catalyst'] as const
export type TagGroup = (typeof TAG_GROUPS)[number]

export function splitTag(tag: string): { group: TagGroup | null; value: string } {
  const i = tag.indexOf(':')
  if (i < 0) return { group: null, value: tag }
  const g = tag.slice(0, i)
  return { group: (TAG_GROUPS as readonly string[]).includes(g) ? (g as TagGroup) : null, value: tag.slice(i + 1) }
}

/** The chips: setup, asset, regime, catalyst in that order; never the outcome pair or `unknown`. */
export function displayTags(tags: readonly string[]): string[] {
  const rank = (t: string) => {
    const g = splitTag(t).group
    return g ? TAG_GROUPS.indexOf(g) : TAG_GROUPS.length
  }
  return tags.filter((t) => !['win', 'loss', 'scratch'].includes(t) && !t.startsWith('outcome:') && !t.endsWith(':unknown')).sort((a, b) => rank(a) - rank(b))
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

/** A tag's words (pmo.tag.<group>.<value>, the Journal's vocabulary), or the raw value when unknown. */
export function useTagLabel() {
  const { t, i18n } = useTranslation('warroom')
  return (tag: string) => {
    const { group, value } = splitTag(tag)
    const key = group ? `pmo.tag.${group}.${value}` : ''
    return group && i18n.exists(`warroom:${key}`) ? t(key) : value.replace(/_/g, ' ')
  }
}
