import { useEffect, useMemo, useRef } from 'react'
import { useLocation } from 'react-router'
import type { WarroomMessage } from './api'
import { readProposalCard, useProposal } from './cards/proposal'

/** The War Room query parameter the approval pill (ATL-180) opens with: /warroom?proposal=<id>. */
export const PROPOSAL_PARAM = 'proposal'

export function proposalParam(search: string): string | null {
  const id = new URLSearchParams(search).get(PROPOSAL_PARAM)
  return id && id.trim() !== '' ? id : null
}

/**
 * The War Room message whose proposal card shows `?proposal=<id>`. An owner's edit has no message
 * of its own (ATL-164: the card for version N renders the newest owner version), so the proposal's
 * version chain is read to find the card that shows it. The newest matching message wins.
 */
export function proposalMessageId(messages: WarroomMessage[], ids: Set<string>): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const ref = readProposalCard(messages[i]!.card)
    if (ref && ids.has(ref.proposalId)) return messages[i]!.id
  }
  return null
}

/**
 * Calls `onFocus(messageId)` once per navigation to /warroom?proposal=<id>, as soon as the card's
 * message is among the loaded ones. A second click on the pill is a new navigation, so it jumps again.
 */
export function useProposalFocus(messages: WarroomMessage[], onFocus: (messageId: string) => void) {
  const location = useLocation()
  const target = proposalParam(location.search)
  const detail = useProposal(target ?? '', Boolean(target))
  const ids = useMemo(() => {
    const set = new Set<string>()
    if (!target) return set
    set.add(target)
    for (const v of detail.data?.versions ?? []) set.add(v.id)
    if (detail.data?.parent_id) set.add(detail.data.parent_id)
    return set
  }, [target, detail.data])
  const handled = useRef<string | null>(null)
  const messageId = target ? proposalMessageId(messages, ids) : null

  useEffect(() => {
    if (!messageId || handled.current === location.key) return
    handled.current = location.key
    onFocus(messageId)
  }, [messageId, location.key, onFocus])
}
