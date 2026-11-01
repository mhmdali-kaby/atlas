import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useApiQuery } from '../../lib/api'
import { getEcho } from '../../lib/echo'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * Proposals waiting for the owner (ATL-180, brief §17): the toolbar pill and the toast on every
 * page. GET /api/proposals?status=awaiting_owner (docs/handoff/ATL-162-proposal-approvals.md) is
 * polled every 15 s; the atlas.proposals events atlas:bridge broadcasts on private-warroom
 * (docs/handoff/ATL-164-proposal-card.md) refetch it at once and carry a new proposal straight to
 * the toast.
 */

export const AWAITING_POLL_MS = 15_000
/** Under this, the pill switches to its warning style (design v11 killswitch.js). */
export const WARN_MS = 60_000

export const awaitingKey = (env: TradingEnv) => ['proposals', 'awaiting', env] as const

/** The fields of the ATL-162 summary the pill and the toast read. */
export interface AwaitingProposal {
  id: string
  env: string
  version: number
  parent_id: string | null
  /** `strategist`, or `owner` for the owner's own edit (never toasted back at them). */
  author?: string
  inst_id: string
  side: 'buy' | 'sell'
  status: string
  approval_expires_at: string | null
}

export function useAwaitingProposals(env: TradingEnv) {
  return useApiQuery<{ data: AwaitingProposal[] }>(awaitingKey(env), `/api/proposals?env=${env}&status=awaiting_owner&limit=50`, {
    refetchInterval: AWAITING_POLL_MS,
    retry: false,
  })
}

/** The common payload of every atlas.proposals event (ATL-162 handoff, "Events"). */
export interface ProposalEventPayload {
  proposal_id?: string
  parent_id?: string | null
  version?: number
  inst_id?: string
  side?: string
  status?: string
  approval_expires_at?: string | null
  /** proposal.awaiting_owner (ATL-160) carries the Risk Engine's decision rather than a status. */
  decision?: string
}

export interface ProposalEnvelope {
  type?: string
  env?: string
  payload?: ProposalEventPayload
}

/** Every atlas.proposals event type the list can change on. */
export const AWAITING_EVENTS = [
  'awaiting_owner',
  'approved',
  'rejected',
  'revised',
  'advise_only',
  'withdrawn',
  'edited',
  'status_changed',
  'cancelled',
  'expired',
] as const

/**
 * The proposal a `proposal.awaiting_owner` event (ATL-160: the Orchestrator handing a risk-approved
 * proposal to the owner) puts in front of the owner. Other events only refetch the list: an owner's
 * own edit also waits, but it is not news to them.
 */
export function awaitingFromEvent(e: ProposalEnvelope | undefined, env: TradingEnv): AwaitingProposal | null {
  const p = e?.payload
  if (!p || (e?.env && e.env !== env)) return null
  if (e?.type !== 'proposal.awaiting_owner') return null
  if ((p.decision ?? p.status) !== 'awaiting_owner' || !p.proposal_id || !p.inst_id) return null
  if (p.side !== 'buy' && p.side !== 'sell') return null
  return {
    id: p.proposal_id,
    env: e?.env ?? env,
    version: typeof p.version === 'number' ? p.version : 1,
    parent_id: p.parent_id ?? null,
    inst_id: p.inst_id,
    side: p.side,
    status: 'awaiting_owner',
    approval_expires_at: p.approval_expires_at ?? null,
  }
}

/**
 * Listens to the proposal events on private-warroom: each one refetches the waiting list, and one
 * that says a proposal now waits is handed to `onAwaiting` (the toast) without waiting for the fetch.
 */
export function useProposalEvents(env: TradingEnv, onAwaiting: (p: AwaitingProposal) => void) {
  const qc = useQueryClient()
  useEffect(() => {
    let channel: ReturnType<ReturnType<typeof getEcho>['private']>
    try {
      channel = getEcho().private('warroom')
    } catch {
      return undefined
    }
    const handlers = AWAITING_EVENTS.map((name) => {
      const handler = (e: ProposalEnvelope | undefined) => {
        if (e?.env && e.env !== env) return
        const waiting = awaitingFromEvent({ ...e, type: e?.type ?? `proposal.${name}` }, env)
        if (waiting) onAwaiting(waiting)
        void qc.invalidateQueries({ queryKey: awaitingKey(env) })
      }
      channel.listen(`.proposal.${name}`, handler)
      return [name, handler] as const
    })
    return () => {
      for (const [name, handler] of handlers) channel.stopListening(`.proposal.${name}`, handler)
    }
  }, [qc, env, onAwaiting])
}

/** Still open for approval at `now`: awaiting, with a deadline in the future. */
export function openForApproval(list: AwaitingProposal[] | undefined, now: number): AwaitingProposal[] {
  return (list ?? []).filter((p) => p.status === 'awaiting_owner' && p.approval_expires_at && Date.parse(p.approval_expires_at) > now)
}

/** The waiting proposal whose deadline comes first. */
export function nearest(list: AwaitingProposal[]): AwaitingProposal | null {
  let best: AwaitingProposal | null = null
  for (const p of list) {
    if (!best || Date.parse(p.approval_expires_at!) < Date.parse(best.approval_expires_at!)) best = p
  }
  return best
}

/** The War Room URL that opens with this proposal's card in view and focused (see warroom/useProposalFocus). */
export const proposalHref = (id: string) => `/warroom?proposal=${encodeURIComponent(id)}`

/** "5:54": minutes and seconds left, rounded up so 0:00 is never shown while time remains. */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
