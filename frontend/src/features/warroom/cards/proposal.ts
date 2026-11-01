import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../../lib/api'
import { abs, cmp, dec, decStr, div, fromInt, maxDecimals, mul } from './dec'

/*
 * The proposal card's data (ATL-164). The War Room message carries the Strategist's card
 * (docs/handoff/ATL-153-proposals-and-votes.md); the card then reads the live resource from
 * GET /api/proposals/{id} (docs/handoff/ATL-162-proposal-approvals.md), which adds the risk check,
 * the Devil's Advocate review, the version chain, the orders and the owner's actions. Decimals stay
 * strings; dec.ts does the arithmetic.
 */

export interface TakeProfit {
  price: string | null
  pct: string | null
}

export interface RiskCheckEntry {
  n?: number
  check?: string
  passed?: boolean | null
  code?: string | null
  detail?: string | null
  values?: Record<string, unknown>
}

export interface RiskCheckView {
  id: string
  result: 'approved' | 'resized' | 'rejected' | string
  decision: string | null
  reason: string | null
  final_contracts: string | null
  final_notional_usd: string | null
  leverage: number | null
  checks: RiskCheckEntry[] | null
  created_at: string | null
}

export interface ProposalActions {
  approve: boolean
  approve_blocked_reason: string | null
  edit: boolean
  reject: boolean
  ask_team: boolean
}

export interface ProposalVersion {
  id: string
  version: number
  author: string
  status: string
  status_reason: string | null
  stop: string | null
  size_suggestion_pct: string | null
  take_profits: TakeProfit[]
  created_at: string | null
}

export interface DaReview {
  message_id: string
  proposal_id: string | null
  risk_rating: number | null
  content_md: string
  card: Record<string, unknown> | null
  created_at: string | null
}

export interface ProposalOrder {
  id: string
  proposal_id: string
  purpose: string | null
  ord_type: string | null
  px: string | null
  sz: string | null
  filled_sz: string | null
  avg_px: string | null
  state: string | null
  created_at: string | null
}

export interface ProposalSummary {
  id: string
  env: string
  version: number
  parent_id: string | null
  kind: string
  author: string
  inst_id: string
  side: 'buy' | 'sell'
  order_type: string | null
  entry: string | null
  entry_zone: [string, string] | null
  stop: string | null
  take_profits: TakeProfit[]
  time_stop_minutes: number | null
  size_suggestion_pct: string | null
  leverage_suggestion: number | null
  expected_r: string | null
  probability: string | null
  rationale_md: string | null
  invalidation: string | null
  status: string
  status_reason: string | null
  owner_note: string | null
  decided_by: string | null
  decided_at: string | null
  approval_expires_at: string | null
  created_at: string | null
  updated_at: string | null
  risk_check: RiskCheckView | null
  actions: ProposalActions
}

export interface ProposalDetail extends ProposalSummary {
  versions?: ProposalVersion[]
  da_review?: DaReview | null
  orders?: ProposalOrder[]
}

/** The proposal card as the Strategist posted it in the War Room (kind `proposal`). */
export interface ProposalCardRef {
  proposalId: string
  version: number
  /** The posted snapshot, shown until (or if) the resource loads. */
  snapshot: ProposalDetail
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : null)
const int = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) ? v : null)

function readTps(v: unknown): TakeProfit[] {
  if (!Array.isArray(v)) return []
  return v.filter((x): x is Record<string, unknown> => typeof x === 'object' && x !== null).map((x) => ({ price: str(x.price), pct: str(x.pct) }))
}

function readZone(v: unknown): [string, string] | null {
  if (!Array.isArray(v) || v.length !== 2) return null
  const a = str(v[0])
  const b = str(v[1])
  return a && b ? [a, b] : null
}

/** A War Room `proposal` card → its proposal id and a snapshot; null when it is not one. */
export function readProposalCard(card: Record<string, unknown> | null | undefined): ProposalCardRef | null {
  if (!card || card.type !== 'proposal') return null
  const id = str(card.proposal_id)
  const inst = str(card.inst_id)
  const side = card.side === 'buy' || card.side === 'sell' ? card.side : null
  if (!id || !inst || !side) return null
  const version = int(card.version) ?? 1
  return {
    proposalId: id,
    version,
    snapshot: {
      id,
      env: str(card.env) ?? 'demo',
      version,
      parent_id: str(card.parent_id),
      kind: str(card.kind) ?? 'entry',
      author: str(card.author) ?? 'strategist',
      inst_id: inst,
      side,
      order_type: str(card.order_type),
      entry: str(card.entry),
      entry_zone: readZone(card.entry_zone),
      stop: str(card.stop),
      take_profits: readTps(card.take_profits),
      time_stop_minutes: int(card.time_stop_minutes),
      size_suggestion_pct: str(card.size_suggestion_pct_equity ?? card.size_suggestion_pct),
      leverage_suggestion: int(card.leverage_suggestion),
      expected_r: str(card.expected_r_multiple ?? card.expected_r),
      probability: str(card.probability_estimate ?? card.probability),
      rationale_md: str(card.rationale_md),
      invalidation: str(card.invalidation),
      status: str(card.status) ?? 'under_review',
      status_reason: null,
      owner_note: null,
      decided_by: null,
      decided_at: null,
      approval_expires_at: null,
      created_at: str(card.created_at),
      updated_at: null,
      risk_check: null,
      actions: { approve: false, approve_blocked_reason: 'not_awaiting_owner', edit: false, reject: false, ask_team: false },
    },
  }
}

// ---- API ---------------------------------------------------------------------------------------

export const proposalKey = (id: string) => ['proposals', id] as const

export function useProposal(id: string) {
  return useQuery<ProposalDetail, ApiError>({
    queryKey: proposalKey(id),
    queryFn: ({ signal }) => api.get<{ data: ProposalDetail }>(`/api/proposals/${encodeURIComponent(id)}`, { signal }).then((r) => r.data),
    staleTime: 10_000,
    retry: (count, err) => err.status !== 404 && err.status !== 401 && count < 2,
  })
}

export interface ApproveBody {
  ok: boolean
  error: string | null
  message: string | null
  execution?: { status?: string | null; reason?: string | null; detail?: string | null } | null
  proposal?: ProposalSummary
}

export interface EditBody {
  ok: boolean
  proposal: ProposalDetail
  risk_check?: { result?: string; decision?: string | null; reason?: string | null } | null
}

const path = (id: string, action = '') => `/api/proposals/${encodeURIComponent(id)}${action}`

export const proposalApi = {
  approve: (id: string) => api.post<ApproveBody>(path(id, '/approve')),
  reject: (id: string, reason: string) => api.post<{ ok: boolean; proposal: ProposalSummary }>(path(id, '/reject'), { reason }),
  edit: (id: string, body: { size?: string; stop?: string; targets?: Array<{ price: string; pct: string }> }) => api.patch<EditBody>(path(id), body),
  askTeam: (id: string, question: string) => api.post<{ ok: boolean; message_id: string; thread_id: string }>(path(id, '/ask-team'), { question }),
}

/** Every cached proposal query in this chain is stale now: the card refetches. */
export function invalidateProposal(qc: QueryClient, ...ids: Array<string | null | undefined>): Promise<void> {
  const keys = ids.filter((x): x is string => Boolean(x))
  return Promise.all(keys.map((id) => qc.invalidateQueries({ queryKey: proposalKey(id) }))).then(() => undefined)
}

export function useInvalidateProposal() {
  const qc = useQueryClient()
  return (...ids: Array<string | null | undefined>) => invalidateProposal(qc, ...ids)
}

/** The server's reason for a refused action: the code the locale translates, and its own words. */
export function refusalOf(err: unknown): { code: string | null; message: string | null } {
  if (!(err instanceof ApiError)) return { code: null, message: null }
  const b = (err.body ?? {}) as Partial<ApproveBody>
  const exec = b.execution ?? null
  if (b.error === 'execution_refused' && exec) return { code: exec.reason ?? null, message: exec.detail ?? b.message ?? null }
  return { code: b.error ?? null, message: b.message ?? exec?.detail ?? null }
}

// ---- What the card shows -----------------------------------------------------------------------

/** The status as the card names it (design v11 "pr.st.*"). */
export type ShownStatus =
  | 'awaiting_owner'
  | 'advice'
  | 'sent'
  | 'partially_filled'
  | 'filled'
  | 'closed'
  | 'rejected'
  | 'expired'
  | 'withdrawn'
  | 'replaced'
  | 'under_review'
  | 'voting'
  | 'passed'
  | 'risk_checked'

export function shownStatus(p: Pick<ProposalSummary, 'status' | 'status_reason' | 'approval_expires_at'>, now: number): ShownStatus {
  switch (p.status) {
    case 'awaiting_owner':
      return p.approval_expires_at && Date.parse(p.approval_expires_at) <= now ? 'expired' : 'awaiting_owner'
    case 'advise_only':
      return 'advice'
    case 'rejected':
      return p.status_reason === 'approval_timeout' ? 'expired' : 'rejected'
    case 'cancelled':
      return p.status_reason === 'revised' ? 'replaced' : 'withdrawn'
    case 'expired':
      return 'expired'
    case 'sent':
    case 'partially_filled':
    case 'filled':
    case 'closed':
    case 'voting':
    case 'passed':
    case 'risk_checked':
      return p.status
    default:
      return 'under_review'
  }
}

export type Direction = 'long' | 'short'
export const directionOf = (side: 'buy' | 'sell'): Direction => (side === 'sell' ? 'short' : 'long')

/** The entry zone as [low, high]; a single entry price is a zone of one price. */
export function zoneOf(p: Pick<ProposalSummary, 'entry' | 'entry_zone'>): [string, string] | null {
  if (p.entry_zone) {
    const [a, b] = p.entry_zone
    const da = dec(a)
    const db = dec(b)
    if (da !== null && db !== null) return cmp(da, db) <= 0 ? [a, b] : [b, a]
  }
  return p.entry ? [p.entry, p.entry] : null
}

/** Tick precision: the most decimals on any price of the plan (at least 2). */
export function decimalsOf(p: ProposalSummary): number {
  return Math.max(2, maxDecimals([p.entry, ...(p.entry_zone ?? []), p.stop, ...p.take_profits.map((x) => x.price)]))
}

function sizingValues(rc: RiskCheckView | null): Record<string, unknown> {
  const c = rc?.checks?.find((x) => x?.check === 'position_size')
  return (c?.values as Record<string, unknown> | undefined) ?? {}
}

export interface Stake {
  contracts: string | null
  notional: string | null
  leverage: number | null
  margin: string | null
  /** USD lost at the stop on the risk-checked size, fees both ways included. */
  risk: string | null
  /** `risk` as % of sizing equity (min(equity, bot capital cap), decision 250). */
  riskPct: string | null
  /** The per-trade USDT cap from the owner's money limits, when the check recorded it. */
  perTradeCap: string | null
  sizingEquity: string | null
}

/** The money at stake after the Risk Engine's check. Nulls when the check has not run. */
export function stakeOf(p: ProposalSummary): Stake {
  const rc = p.risk_check
  const v = sizingValues(rc)
  const notional = rc?.final_notional_usd ?? null
  const caps = (v.caps as Record<string, unknown> | undefined) ?? {}
  const sizingEquity = str(v.sizing_equity) ?? str(v.equity)
  const out: Stake = {
    contracts: rc?.final_contracts ?? null,
    notional,
    leverage: rc?.leverage ?? null,
    margin: str(v.margin_usd),
    risk: null,
    riskPct: null,
    perTradeCap: str(caps.max_usdt_per_trade),
    sizingEquity,
  }
  const n = dec(notional)
  if (n === null) return out
  if (out.margin === null && rc?.leverage) out.margin = decStr(div(n, fromInt(rc.leverage)), 2)
  // Distance to the stop as a fraction of the entry the check used, plus the taker fee both ways.
  let dist = dec(str(v.stop_dist_pct))
  if (dist === null) {
    const zone = zoneOf(p)
    const e = zone ? div((dec(zone[0])! + dec(zone[1])!), fromInt(2)) : null
    const s = dec(p.stop)
    if (e !== null && s !== null && e !== 0n) dist = div(abs(e - s), e)
  }
  if (dist === null) return out
  const fee = dec(str(v.fee_rate))
  const risk = mul(n, dist + (fee === null ? 0n : abs(fee) * 2n))
  out.risk = decStr(risk, 2)
  const eq = dec(sizingEquity)
  if (eq !== null && eq > 0n) out.riskPct = decStr(mul(div(risk, eq), fromInt(100)), 2)
  return out
}

/** The size the Edit sheet starts from, in USDT: the checked notional, else the suggestion × equity. */
export function sizeUsdOf(p: ProposalSummary, stake: Stake): string | null {
  if (stake.notional) return decStr(dec(stake.notional)!, 0)
  const pct = dec(p.size_suggestion_pct)
  const eq = dec(stake.sizingEquity)
  return pct !== null && eq !== null ? decStr(div(mul(eq, pct), fromInt(100)), 0) : null
}

/** USDT → % of sizing equity, the unit PATCH /api/proposals takes (`size`). */
export function sizePctOf(usdt: string, sizingEquity: string | null): string | null {
  const u = dec(usdt)
  const eq = dec(sizingEquity)
  if (u === null || eq === null || eq <= 0n) return null
  return decStr(mul(div(u, eq), fromInt(100)), 8)
}

/**
 * The version the card shows. An owner's edit has no War Room message of its own, so the card for
 * the version it replaced follows the chain to the newest owner edit (design: "Version 3 is your
 * edit"). A revision by an agent has its own message, so the older card shows "Replaced".
 */
export function followOwnerEdits(d: ProposalDetail): { id: string; edited: boolean } {
  const chain = d.versions ?? []
  let at = chain.findIndex((v) => v.id === d.id)
  let id = d.id
  let edited = false
  while (at >= 0 && at + 1 < chain.length && chain[at + 1]!.author === 'owner') {
    at += 1
    id = chain[at]!.id
    edited = true
  }
  return { id, edited }
}

/** The agent revision that replaced this version, when there is one. */
export function replacedBy(d: ProposalDetail): ProposalVersion | null {
  const chain = d.versions ?? []
  const at = chain.findIndex((v) => v.id === d.id)
  return at >= 0 && at + 1 < chain.length ? chain[at + 1]! : null
}

export interface DaSummary {
  rating: number | null
  objection: string | null
  version: number | null
  messageId: string | null
}

export function daOf(d: ProposalDetail): DaSummary | null {
  const r = d.da_review
  if (!r) return null
  const card = r.card ?? {}
  const rating = typeof r.risk_rating === 'number' ? r.risk_rating : int(card.risk_rating)
  return { rating, objection: str(card.strongest_objection), version: int(card.version), messageId: r.message_id ?? null }
}

/** The newest entry order (the one the approval sent). */
export function entryOrderOf(d: ProposalDetail): ProposalOrder | null {
  const orders = (d.orders ?? []).filter((o) => o.proposal_id === d.id && (o.purpose === null || o.purpose === 'entry'))
  return orders.length ? orders[orders.length - 1]! : null
}
