import { CircleDashed, Timer } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { cx } from '../../ui/cx'
import { openForApproval, useAwaitingProposals } from '../approvals/api'
import { useNow } from '../portfolio/hooks'
import { mmss } from '../warroom/time'
import type { NotificationRow } from './api'
import { iso } from './events'

/** A waiting trade's live line: "5:54 left" (orange in the last minute), then "No longer waiting". */
export function LiveLine({ row }: { row: NotificationRow }) {
  const { t } = useTranslation('notifications')
  const env = useTradingEnv()
  const now = useNow(1000)
  const awaiting = useAwaitingProposals(env)
  const id = typeof row.params.proposal_id === 'string' ? row.params.proposal_id : ''
  const end = typeof row.params.approval_expires_at === 'string' ? Date.parse(row.params.approval_expires_at) : NaN
  const open = useMemo(() => openForApproval(awaiting.data?.data, now), [awaiting.data, now])
  const left = end - now
  const waiting = Number.isFinite(end) && left > 0 && (!awaiting.data || open.some((p) => p.id === id))
  if (!waiting) {
    return (
      <span className="nt-live is-over">
        <CircleDashed aria-hidden="true" strokeWidth={1.75} />
        <span>{t('noLonger')}</span>
      </span>
    )
  }
  return (
    <span className={cx('nt-live', left < 60_000 && 'is-last')}>
      <Timer aria-hidden="true" strokeWidth={1.75} />
      <span>{t('left', { t: iso(mmss(left)) })}</span>
    </span>
  )
}
