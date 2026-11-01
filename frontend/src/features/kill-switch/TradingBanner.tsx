import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CirclePause, CirclePlay, OctagonAlert, Play, ServerCrash, TriangleAlert, WifiOff } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Sheet, SheetClose, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { formatClock } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import { HEALTH_KEY, postResume, useSystemHealth, type Breaker, type SystemHealth } from './api'
import { NS, Tr } from './parts'
import './killswitch.css'

/** Which breaker the banner leads with when several are active: money first, stale data last. */
const PRIORITY = [
  'daily_loss',
  'weekly_loss',
  'consecutive_losses',
  'volatility_halt',
  'okx_errors',
  'reconciliation_mismatch',
  'agent_budget',
  'data_stale',
]
const KNOWN = new Set(PRIORITY)
/**
 * trading_core's reason when the owner paused without one; the banner already says it.
 * Coupled to that English text in trading_core (the health API has no flag for it): if the
 * server wording changes, the banner only shows the reason once more, nothing breaks.
 */
const OWNER_DEFAULT_REASON = 'Paused by the owner.'
const LOSS_BREAKERS = new Set(['daily_loss', 'weekly_loss', 'consecutive_losses'])

function primaryBreaker(breakers: readonly Breaker[]): Breaker | undefined {
  const rank = (b: Breaker) => {
    const i = PRIORITY.indexOf(b.name)
    return i === -1 ? PRIORITY.length : i
  }
  return [...breakers].sort((a, b) => rank(a) - rank(b))[0]
}

function breakerIcon(name: string): ReactNode {
  if (name === 'okx_errors') return <ServerCrash />
  if (name === 'data_stale') return <WifiOff />
  if (LOSS_BREAKERS.has(name)) return <OctagonAlert />
  return <CircleAlert />
}

function warnKey(name: string | undefined): string | null {
  if (!name) return null
  if (LOSS_BREAKERS.has(name)) return 'res.warn.loss'
  if (name === 'okx_errors' || name === 'data_stale') return `res.warn.${name}`
  return 'res.warn.other'
}

/**
 * Under the toolbar on every page (design §8): paused by the owner, or an active circuit
 * breaker, with its reason, its time and Resume trading. Nothing when trading runs.
 */
export function TradingBanner() {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const health = useSystemHealth()
  const [resumeOpen, setResumeOpen] = useState(false)
  const data = health.data?.data
  if (!data) return null
  const { trading, breakers } = data
  if (!trading.paused && breakers.length === 0) return null

  const lead = primaryBreaker(breakers)
  const others = breakers.length - (lead ? 1 : 0)
  // Resume does nothing for a self-clearing breaker alone (data_stale clears after fresh data).
  const canResume = trading.paused || breakers.some((b) => !b.self_clearing)
  const since = lead?.since ?? trading.paused_at
  const tone = lead ? 'loss' : 'warn'

  let title: ReactNode
  let body: ReactNode
  if (lead) {
    title = KNOWN.has(lead.name) ? t(`b.breaker.${lead.name}`) : t('b.breaker.other', { name: lead.name })
    body = (
      <>
        <bdi className="ks-reason">{lead.reason}</bdi>
        {others > 0 && (
          <>
            {' '}
            <Tr k="b.more" count={others} />
          </>
        )}
        {!canResume && <> {t('b.staleAfterResume')}</>}
      </>
    )
  } else {
    title = trading.paused_at ? (
      <Tr k="b.paused.title" values={{ time: formatClock(trading.paused_at, lang, false) }} />
    ) : (
      t('b.paused.titleNoTime')
    )
    body = (
      <>
        {t('b.paused.body')}
        {trading.reason && trading.reason !== OWNER_DEFAULT_REASON && (
          <>
            {' '}
            <bdi className="ks-reason">{trading.reason}</bdi>
          </>
        )}
      </>
    )
  }

  return (
    <div className={cx('ks-banner', `is-${tone}`)} role="region" aria-label={t('b.label')} data-testid="trading-banner">
      <span className="ks-banner-ic" aria-hidden="true">
        {lead ? breakerIcon(lead.name) : <CirclePause />}
      </span>
      <div className="ks-banner-text" aria-live="polite">
        <p className="ks-banner-title">{title}</p>
        <p className="ks-banner-body">{body}</p>
      </div>
      {lead && since && (
        <span className="ks-banner-time">
          <Tr k="b.since" values={{ time: formatClock(since, lang, false) }} />
        </span>
      )}
      {canResume && (
        <ResumeSheet
          open={resumeOpen}
          onOpenChange={setResumeOpen}
          health={data}
          trigger={
            <Button size="sm" className="ks-resume" icon={<Play aria-hidden="true" />} data-testid="resume-open">
              {t('b.resume')}
            </Button>
          }
        />
      )}
    </div>
  )
}

function ResumeSheet({
  open,
  onOpenChange,
  health,
  trigger,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  health: SystemHealth
  trigger: ReactNode
}) {
  const { t } = useTranslation(NS)
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const warn = warnKey(primaryBreaker(health.breakers)?.name)

  async function resume() {
    setBusy(true)
    setError(false)
    try {
      await postResume()
      onOpenChange(false)
      toast({
        title: t('res.toast'),
        body: t('res.toastBody'),
        tone: 'success',
      })
    } catch {
      setError(true)
    } finally {
      setBusy(false)
      void qc.invalidateQueries({ queryKey: HEALTH_KEY })
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (next) setError(false)
        onOpenChange(next)
      }}
      trigger={trigger}
      title={t('res.title')}
      description={t('res.text')}
      icon={<CirclePlay strokeWidth={1.5} />}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary">{t('cancel')}</Button>
          </SheetClose>
          <Button variant="primary" icon={<Play aria-hidden="true" />} loading={busy} onClick={() => void resume()} data-testid="resume-go">
            {t('res.go')}
          </Button>
        </>
      }
    >
      {warn && (
        <div className="ks-notice is-warning">
          <TriangleAlert aria-hidden="true" />
          <p>{t(warn)}</p>
        </div>
      )}
      {error && (
        <div className="ks-notice is-error" role="alert">
          <CircleAlert aria-hidden="true" />
          <p>{t('res.failed')}</p>
        </div>
      )}
    </Sheet>
  )
}
