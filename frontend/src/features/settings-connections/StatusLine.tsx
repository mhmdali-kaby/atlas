import { CircleAlert, CircleCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import type { Connection } from '../onboarding'
import { formatAgo } from '../portfolio/time'
import { SERVICE_ERRORS, type Failure } from './shared'

/** The line under the account name: testing, failed (OKX or service), connected N ago, or untested. */
export function StatusLine({
  connection,
  testing,
  failure,
  now,
  lang,
}: {
  connection: Connection
  testing: boolean
  failure: Failure | null
  now: number
  lang: Lang
}) {
  const { t } = useTranslation('settings')
  const verified = connection.last_verified_at
  const ms = verified ? Math.max(0, now - Date.parse(verified)) : 0
  const ago = verified ? (ms < 60_000 ? t('conn.justNow') : formatAgo(ms, lang)) : null
  const base = 'inline-flex items-center gap-1.5 text-subheadline text-label-secondary [&_svg]:size-4 [&_svg]:shrink-0'

  if (testing) {
    return (
      <p className={base} data-testid="conn-status" data-state="testing">
        <Spinner />
        <span>{t('conn.testingLine')}</span>
      </p>
    )
  }
  if (failure) {
    const service = SERVICE_ERRORS.has(failure.error)
    const text = service
      ? ago
        ? t('conn.cantTest', { ago })
        : t('conn.cantTestNever')
      : ago
        ? t('conn.failedLineAgo', { ago })
        : t('conn.failedLine')
    return (
      <p className={cx(base, 'text-loss-ink')} data-testid="conn-status" data-state="failed">
        <CircleAlert aria-hidden="true" strokeWidth={2} />
        <span>{text}</span>
      </p>
    )
  }
  if (ago) {
    return (
      <p className={base} data-testid="conn-status" data-state="ok">
        <CircleCheck aria-hidden="true" strokeWidth={2} className="text-profit-ink" />
        <span>{t('conn.connected', { ago })}</span>
      </p>
    )
  }
  return (
    <p className={base} data-testid="conn-status" data-state="untested">
      <span>{t('conn.untested')}</span>
    </p>
  )
}
