import { Hourglass, Timer } from 'lucide-react'
import { useCallback, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { toast } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { formatTime } from '../warroom/time'
import { mmss, nearest, openForApproval, proposalHref, useAwaitingProposals, useProposalEvents, WARN_MS, type AwaitingProposal } from './api'
import './approvals.css'

/*
 * The approval pill (design v11 killswitch.js `pill()`, css `.ks-pill`): rendered by the shell in
 * the toolbar's actions slot, before Stop, on every signed-in page. "N to approve" and the
 * countdown to the nearest deadline; under a minute it turns orange and its words say so too.
 * Hidden when nothing waits. Clicking opens the War Room on that proposal's card.
 *
 * It also owns the "trade waiting for your approval" toast (brief §17): a proposal that starts
 * waiting, seen through an atlas.proposals event or the 15 s poll, toasts once wherever the owner
 * is, plus a browser notification when permission was already granted (never asked for here).
 */
export function ApprovalPill() {
  const { t } = useTranslation('approvals')
  const lang = useLang()
  const env = useTradingEnv()
  const navigate = useNavigate()
  const query = useAwaitingProposals(env)
  const now = useNow(1000)

  // Toast each proposal once. What is already waiting when the app opens is not news: the first
  // load only marks it seen (the pill shows it).
  const seen = useRef(new Set<string>())
  const primed = useRef(false)
  const announce = useCallback(
    (p: AwaitingProposal) => {
      if (seen.current.has(p.id)) return
      seen.current.add(p.id)
      if (p.approval_expires_at && Date.parse(p.approval_expires_at) <= Date.now()) return
      const side = t(`side.${p.side}`)
      const time = p.approval_expires_at ? formatTime(p.approval_expires_at, lang) : null
      const title = t('toast.title')
      const body = time ? t('toast.body', { inst: p.inst_id, side, time }) : t('toast.bodyNoTime', { inst: p.inst_id, side })
      toast({ title, body, tone: 'info' })
      notify(title, body, p.id)
    },
    [t, lang],
  )
  useProposalEvents(env, announce)

  const data = query.data?.data
  useEffect(() => {
    if (!data) return
    if (!primed.current) {
      primed.current = true
      for (const p of data) seen.current.add(p.id)
      return
    }
    for (const p of data) {
      if (p.status === 'awaiting_owner' && p.author !== 'owner') announce(p)
      else seen.current.add(p.id)
    }
  }, [data, announce])

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

function notify(title: string, body: string, tag: string) {
  if (typeof window === 'undefined' || !('Notification' in window)) return
  if (window.Notification.permission !== 'granted') return
  try {
    new window.Notification(title, { body, tag })
  } catch {
    // Some browsers only allow notifications from a service worker; the toast already showed it.
  }
}
