import { Hourglass, Timer } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { useNow } from '../portfolio/hooks'
import { mmss } from '../warroom/time'
import { nearest, openForApproval, proposalHref, useAwaitingProposals, useProposalEvents, WARN_MS } from './api'
import './approvals.css'

/*
 * The approval pill (design v11 killswitch.js `pill()`, css `.ks-pill`): rendered by the shell in
 * the toolbar's actions slot, before Stop, on every signed-in page. "N to approve" and the
 * countdown to the nearest deadline; under a minute it turns orange and its words say so too.
 * Hidden when nothing waits. Clicking opens the War Room on that proposal's card.
 *
 * The "trade waiting for your approval" toast is the notifications path's now (ATL-217).
 */
export function ApprovalPill() {
  const { t } = useTranslation('approvals')
  const env = useTradingEnv()
  const navigate = useNavigate()
  const query = useAwaitingProposals(env)
  const now = useNow(1000)

  // The "trade waiting for your approval" toast and browser banner now come from the
  // proposal_awaiting_approval notification (ATL-217), so one event never shows two toasts.
  // The proposal events still refetch the list at once.
  useProposalEvents(env, ignore)

  const data = query.data?.data
  const open = openForApproval(data, now)
  const first = nearest(open)
  if (!first) return null

  const left = Date.parse(first.approval_expires_at!) - now
  const warn = left < WARN_MS
  const time = mmss(left)
  const count = open.length
  const Icon = warn ? Hourglass : Timer

  return (
    <button
      type="button"
      className={`ks-pill${warn ? ' is-warn' : ''}`}
      data-testid="approval-pill"
      data-warn={warn || undefined}
      aria-label={t(warn ? 'pill.ariaWarn' : 'pill.aria', { count, time })}
      onClick={() => navigate(proposalHref(first.id))}
    >
      <Icon aria-hidden="true" />
      <span className="ks-pill-label" data-testid="approval-pill-label">
        {t(warn ? 'pill.labelWarn' : 'pill.label', { count })}
      </span>
      <bdi dir="ltr" className="ks-pill-time" data-testid="approval-pill-time">
        {time}
      </bdi>
    </button>
  )
}

function ignore() {}
