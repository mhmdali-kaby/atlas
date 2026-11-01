/*
 * The debate cards' readers (ATL-165, design v11 Phase 2 §6). Each takes a message's `card` as the
 * services write it and returns what its renderer draws, or null when the card lacks what the
 * renderer needs (the GenericCard draws it instead). Decimals stay strings; nothing a figure shows
 * passes through a float except the 0–1 bar widths.
 *
 * - da_review: docs/handoff/ATL-159-devils-advocate.md (card.type "da_review").
 * - vote_summary: docs/handoff/ATL-154-weighted-vote.md.
 * - risk_check: docs/handoff/ATL-127-risk-engine.md and ATL-160 (the decision body + type).
 */
import { Check, Minus, Pencil, type LucideIcon } from 'lucide-react'
import { formatFixed, type Lang } from '../../../lib/format'

/** The Devil's Advocate's checklist, in the brief's fixed order (§7.7). */
export const DA_ITEMS = [
  'event_risk',
  'crowding',
  'history',
  'stop_quality',
  'chasing',
  'correlation',
  'liquidity',
  'priced_in',
  'data_quality',
  'overconfidence',
] as const
export type DaItem = (typeof DA_ITEMS)[number]

/** ok, or a problem by severity. `loss_gt_1r` (could lose more than 1R) is drawn as serious. */
export type DaRowStatus = 'ok' | 'minor' | 'serious'
export type Vote = 'take' | 'change' | 'skip'

export interface DaRow {
  item: DaItem
  status: DaRowStatus
  /** A problem that could lose more than 1R (the rating-5 rule). */
  lossGt1r: boolean
  detail: string | null
}

export interface DevilsReview {
  proposalId: string
  version: number
  round: 1 | 2
  rating: number
  strongestObjection: string | null
  rows: DaRow[]
  fix: string | null
  fixApplied: boolean
  vote: Vote
  confidence: number | null
}

const text = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const intIn = (v: unknown, lo: number, hi: number): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi ? v : null)

/** A decimal string or number as a decimal string; null for anything else. */
export function decStr(v: unknown): string | null {
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  if (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim())) return v.trim()
  return null
}

function unit(v: unknown): number | null {
  const s = decStr(v)
  if (s === null) return null
  const n = Number(s)
  return n >= 0 && n <= 1 ? n : null
}

export const VOTE_ICON: Record<Vote, LucideIcon> = { take: Check, change: Pencil, skip: Minus }

/** "70%" from a 0–1 confidence, the unit inside the isolate. */
export const pctOf = (v: number, lang: Lang) => `${formatFixed(Math.round(v * 100), 0, lang)}%`

const asVote = (v: unknown): Vote | null => (v === 'take' || v === 'change' || v === 'skip' ? v : null)

export function readDevilsReview(card: unknown): DevilsReview | null {
  if (!isObj(card) || card.type !== 'da_review') return null
  const proposalId = text(card.proposal_id)
  const rating = intIn(card.risk_rating, 1, 5)
  const vote = asVote(card.vote)
  if (!proposalId || rating === null || !vote) return null
  const byItem = new Map<number, Record<string, unknown>>()
  if (Array.isArray(card.items)) for (const x of card.items) if (isObj(x) && typeof x.item === 'number') byItem.set(x.item, x)
  const rows: DaRow[] = DA_ITEMS.map((item, i) => {
    const x = byItem.get(i + 1)
    const sev = x?.severity
    const problem = x?.status === 'problem'
    const status: DaRowStatus = !problem ? 'ok' : sev === 'minor' ? 'minor' : 'serious'
    const detail = text(x?.detail)
    return { item, status, lossGt1r: problem && sev === 'loss_gt_1r', detail: detail === 'OK' ? null : detail }
  })
  return {
    proposalId,
    version: intIn(card.version, 1, 10_000) ?? 1,
    round: card.round === 2 ? 2 : 1,
    rating,
    strongestObjection: text(card.strongest_objection),
    rows,
    fix: text(card.fix),
    fixApplied: card.fix_applied === true,
    vote,
    confidence: unit(card.confidence),
  }
}

/** The re-rate's "now OK" items: problems in the round-1 review that are OK in the re-rate. */
export function resolvedItems(previous: DevilsReview, now: DevilsReview): DaItem[] {
  return now.rows.filter((r, i) => r.status === 'ok' && previous.rows[i]?.status !== 'ok').map((r) => r.item)
}

/* ------------------------------------------------------------ vote_summary */

export interface Voter {
  agent: string
  vote: Vote
  missing: boolean
  fixApplied: boolean
  confidence: string
  reliability: string
  weight: string
  /** take 1, change with its fix applied 0.5, skip 0. */
  points: string
  /** points × weight */
  contribution: string
}

export type VoteReason = 'ratio_below_threshold' | 'da_rating_missing' | 'da_rating_too_high'

export interface VoteSummary {
  proposalId: string
  version: number
  passed: boolean
  ratio: string
  threshold: string
  weightedScore: string
  totalWeight: string
  daRating: number | null
  daMaxRating: number
  reasons: VoteReason[]
  voters: Voter[]
}

export function readVoteSummary(card: unknown): VoteSummary | null {
  if (!isObj(card) || card.type !== 'vote_summary') return null
  const proposalId = text(card.proposal_id)
  const ratio = decStr(card.ratio)
  const threshold = decStr(card.threshold)
  if (!proposalId || ratio === null || threshold === null || !Array.isArray(card.voters)) return null
  const voters: Voter[] = card.voters.flatMap((v) => {
    if (!isObj(v) || !text(v.agent)) return []
    const missing = v.missing === true
    const vote = asVote(v.vote) ?? 'skip'
    return [
      {
        agent: v.agent as string,
        vote: missing ? 'skip' : vote,
        missing,
        fixApplied: v.fix_applied === true,
        confidence: decStr(v.confidence) ?? '0',
        reliability: decStr(v.reliability) ?? '1',
        weight: decStr(v.weight) ?? '0',
        points: decStr(v.points) ?? '0',
        contribution: decStr(v.score) ?? '0',
      },
    ]
  })
  if (voters.length === 0) return null
  const reasons = Array.isArray(card.reasons)
    ? card.reasons.filter((r): r is VoteReason => r === 'ratio_below_threshold' || r === 'da_rating_missing' || r === 'da_rating_too_high')
    : []
  return {
    proposalId,
    version: intIn(card.version, 1, 10_000) ?? 1,
    passed: card.result === 'passed',
    ratio,
    threshold,
    weightedScore: decStr(card.weighted_score) ?? '0',
    totalWeight: decStr(card.total_weight) ?? '0',
    daRating: intIn(card.da_rating, 1, 5),
    daMaxRating: intIn(card.da_max_rating, 1, 5) ?? 4,
    reasons,
    voters,
  }
}

/* -------------------------------------------------------------- risk_check */

export const RISK_CHECKS = [
  'env_and_kill_switch',
  'instrument_allowed',
  'proposal_valid',
  'position_size',
  'portfolio',
  'loss_limits',
  'liquidation_buffer',
  'event_blackout',
  'autonomy',
] as const

export type StepState = 'pass' | 'resize' | 'fail' | 'not_run' | 'awaiting_owner'
export type RiskResult = 'approved' | 'resized' | 'rejected'

export interface RiskStep {
  n: number
  state: StepState
  reason: string | null
}

export interface RiskCheck {
  proposalId: string
  result: RiskResult
  decision: string | null
  reason: string | null
  settingsVersion: number | null
  /** The number (1-9) of the check that rejected it. */
  failedCheck: number | null
  steps: RiskStep[]
  contracts: string | null
  notionalUsd: string | null
  leverage: number | null
  marginUsd: string | null
  /** The cap that set the size, and its value in USD (check 4). */
  bindingCap: string | null
  bindingValueUsd: string | null
  /** What the Strategist asked for, shown when the size was cut. */
  requestedUsd: string | null
}

/** Caps that come from the money limits (USDT); the others come from the risk profile (%). */
export const MONEY_CAPS = new Set(['max_usdt_per_trade', 'max_new_exposure_per_day'])

export function readRiskCheck(card: unknown): RiskCheck | null {
  if (!isObj(card) || card.type !== 'risk_check') return null
  const proposalId = text(card.proposal_id)
  const result = card.result === 'approved' || card.result === 'resized' || card.result === 'rejected' ? card.result : null
  if (!proposalId || !result || !Array.isArray(card.checks)) return null
  const byN = new Map<number, Record<string, unknown>>()
  for (const x of card.checks) if (isObj(x) && typeof x.n === 'number') byN.set(x.n, x)
  if (byN.size === 0) return null
  const bindingCap = text(card.binding_cap)
  const decision = text(card.decision)
  const sizing = byN.get(4)
  const values = isObj(sizing?.values) ? sizing.values : {}
  const caps = isObj(values.caps) ? values.caps : {}

  let failedCheck: number | null = null
  const steps: RiskStep[] = RISK_CHECKS.map((_, i) => {
    const n = i + 1
    const x = byN.get(n)
    const detail = text(x?.detail)
    let state: StepState
    if (!x || x.status === 'not_run' || x.status === 'not_applicable') state = 'not_run'
    else if (x.status === 'failed' || x.passed === false) state = 'fail'
    else if (n === 4 && result === 'resized') state = 'resize'
    else if (n === 9 && decision === 'awaiting_owner') state = 'awaiting_owner'
    else state = 'pass'
    if (state === 'fail' && failedCheck === null) failedCheck = n
    // A not-run check's reason is the card's own ("Not run: the checks stopped at 6.").
    return { n, state, reason: state === 'not_run' ? null : detail }
  })

  const rejected = result === 'rejected'
  return {
    proposalId,
    result,
    decision,
    reason: text(card.reason),
    settingsVersion: intIn(card.settings_version, 0, 1_000_000),
    failedCheck,
    steps,
    contracts: rejected ? null : decStr(card.final_contracts),
    notionalUsd: rejected ? null : decStr(card.final_notional_usd),
    leverage: rejected ? null : intIn(card.leverage, 1, 1000),
    marginUsd: rejected ? null : decStr(values.margin_usd),
    bindingCap: rejected ? null : bindingCap,
    bindingValueUsd: bindingCap ? decStr(caps[bindingCap]) : null,
    requestedUsd: result === 'resized' ? decStr(caps.strategist_suggestion) : null,
  }
}
