import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { toast } from '../../ui'
import type { WarroomMessage, WarroomThread } from './api'
import { readProposalCard, useProposal } from './cards/proposal'
import { MESSAGE_PARAM, messageParam, useMessageById } from './deepLink'

/*
 * Landing the War Room on a notification's `?message=<id>` or `?proposal=<id>` (ATL-180, ATL-220;
 * design v18 "Autonomy and notifications" §5.6). Both share one shape: find the target among the
 * thread's loaded messages; if it is not there yet, load older pages (the Finding capsule) until
 * it turns up or the thread runs out; then centre it, flash it once and drop the query parameter.
 * An id that does not exist (or belongs to another env) ends in a "couldn't find it" toast instead.
 */

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

/** Drops one query parameter from the current URL without leaving a history entry. */
function useDropParam() {
  const location = useLocation()
  const navigate = useNavigate()
  return (key: string) => {
    const params = new URLSearchParams(location.search)
    if (!params.has(key)) return
    params.delete(key)
    const search = params.toString()
    navigate({ pathname: location.pathname, search: search ? `?${search}` : '' }, { replace: true })
  }
}

interface MessagesQuery {
  hasNextPage?: boolean
  isFetchingNextPage: boolean
  fetchNextPage: () => unknown
}

/**
 * Drives both `?message=` and `?proposal=` (ATL-220). `thread` is the Feed's own thread: a
 * `?message=` whose target belongs to another thread is left alone here — the War Room page
 * switches threads first (`deepLink.ts`'s `useMessageById`, shared by its cache key), so by the
 * time this thread is current the target is either already loaded or just needs older pages.
 */
export function useDeepLinkFocus(
  env: TradingEnv,
  thread: WarroomThread,
  messages: WarroomMessage[],
  query: MessagesQuery,
  onFocus: (messageId: string) => void,
): { finding: 'message' | 'proposal' | null } {
  const { t } = useTranslation('warroom')
  const location = useLocation()
  const dropParam = useDropParam()
  const byId = useMemo(() => new Set(messages.map((m) => m.id)), [messages])
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  const [finding, setFinding] = useState<'message' | 'proposal' | null>(null)

  const notFound = (kind: 'message' | 'proposal', param: string) => {
    setFinding(null)
    toast({ tone: 'info', title: t(`land.notFound.${kind}`), body: t('land.notFound.body') })
    dropParam(param)
  }

  // ---- ?message=<id> --------------------------------------------------------------------------
  const msgTarget = messageParam(location.search)
  const msgFetch = useMessageById(env, msgTarget && !byId.has(msgTarget) ? msgTarget : null)
  const handledMsg = useRef<string | null>(null)

  useEffect(() => {
    if (!msgTarget || handledMsg.current === location.key) return
    if (byId.has(msgTarget)) {
      handledMsg.current = location.key
      setFinding(null)
      onFocus(msgTarget)
      dropParam(MESSAGE_PARAM)
      return
    }
    if (msgFetch.isError) {
      handledMsg.current = location.key
      notFound('message', MESSAGE_PARAM)
      return
    }
    if (!msgFetch.data || msgFetch.data.thread_id !== thread.id) return // still resolving, or the page-level redirect is switching threads
    setFinding('message')
    if (hasNextPage) {
      if (!isFetchingNextPage) void fetchNextPage()
    } else {
      // The message exists and this is its thread, yet it is not in any page: give up gracefully.
      handledMsg.current = location.key
      notFound('message', MESSAGE_PARAM)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msgTarget, byId, msgFetch.data, msgFetch.isError, thread.id, hasNextPage, isFetchingNextPage, fetchNextPage, location.key])

  // ---- ?proposal=<id> --------------------------------------------------------------------------
  const propTarget = proposalParam(location.search)
  const proposalDetail = useProposal(propTarget ?? '', Boolean(propTarget) && !msgTarget)
  const proposalIds = useMemo(() => {
    const set = new Set<string>()
    if (!propTarget) return set
    set.add(propTarget)
    for (const v of proposalDetail.data?.versions ?? []) set.add(v.id)
    if (proposalDetail.data?.parent_id) set.add(proposalDetail.data.parent_id)
    return set
  }, [propTarget, proposalDetail.data])
  const proposalMsgId = propTarget ? proposalMessageId(messages, proposalIds) : null
  const handledProposal = useRef<string | null>(null)

  useEffect(() => {
    if (!propTarget || msgTarget || handledProposal.current === location.key) return
    if (proposalMsgId) {
      handledProposal.current = location.key
      setFinding(null)
      onFocus(proposalMsgId)
      dropParam(PROPOSAL_PARAM)
      return
    }
    if (proposalDetail.isError) {
      handledProposal.current = location.key
      notFound('proposal', PROPOSAL_PARAM)
      return
    }
    if (!proposalDetail.data) return
    setFinding('proposal')
    if (hasNextPage) {
      if (!isFetchingNextPage) void fetchNextPage()
    } else {
      handledProposal.current = location.key
      notFound('proposal', PROPOSAL_PARAM)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [propTarget, msgTarget, proposalMsgId, proposalDetail.data, proposalDetail.isError, hasNextPage, isFetchingNextPage, fetchNextPage, location.key])

  return { finding: msgTarget ? finding : propTarget ? finding : null }
}

// Re-exported so Feed only needs to import from this one module for both parameters.
export { MESSAGE_PARAM, messageParam } from './deepLink'
