import { useQueryClient } from '@tanstack/react-query'
import { CirclePause, CloudOff, OctagonX, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { LiveTag } from '../../app/shell/LiveTag'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Button, Sheet, SheetClose, Spinner, type SheetTone } from '../../ui'
import { cx } from '../../ui/cx'
import { formatClock } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import { HEALTH_KEY, postKill, useKillPreview, useSystemEvents, useSystemHealth, type KillReport as KillReportData } from './api'
import { ConfirmActions, ConfirmBody } from './KillConfirm'
import { KillReport } from './KillReport'
import { NS, Tr } from './parts'
import './killswitch.css'

type View = 'confirm' | 'working' | 'report' | 'fail'

/**
 * The red Stop trading button on every page's toolbar (brief §12, design §8, decision 251).
 * One press stops: entry orders and take-profits are cancelled, every stop stays on OKX and
 * trading pauses. Closing all positions too is a 1.5 s press-and-hold, never typed.
 */
export function KillSwitchButton() {
  const { t } = useTranslation(NS)
  const health = useSystemHealth()
  useSystemEvents()
  const stopped = health.data?.data.trading.paused ?? false
  const [open, setOpen] = useState(false)
  const label = t(stopped ? 'stoppedAria' : 'stopAria')

  return (
    <KillSheet
      open={open}
      onOpenChange={setOpen}
      trigger={
        <button
          type="button"
          className={cx('ks-stop', stopped && 'is-stopped')}
          aria-haspopup="dialog"
          aria-label={label}
          title={label}
          data-testid="kill-switch"
        >
          <OctagonX aria-hidden="true" />
          <span className="ks-stop-label">{t(stopped ? 'stopped' : 'stop')}</span>
          <span className="ks-stop-short" aria-hidden="true">
            {t(stopped ? 'stopped' : 'stopShort')}
          </span>
        </button>
      }
    />
  )
}

/**
 * `initialFlatten` (ATL-163, `/kill flatten`) preselects close-all on opening; the owner still has
 * to hold the confirm button for 1.5 s, exactly as when they turn it on by hand.
 */
export function KillSheet({
  open,
  onOpenChange,
  trigger,
  initialFlatten = false,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  trigger?: ReactNode
  initialFlatten?: boolean
}) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const env = useTradingEnv()
  const qc = useQueryClient()
  const preview = useKillPreview(open, env)
  const [view, setView] = useState<View>('confirm')
  const [flatten, setFlatten] = useState(initialFlatten)
  const [report, setReport] = useState<KillReportData | null>(null)
  const [retrying, setRetrying] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Each opening starts over at the confirmation, with close-all off (on for /kill flatten):
  // through the trigger or through a controlled `open` with no trigger (/kill). Adjusted during render, so the
  // previous report never paints for a frame.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setView('confirm')
      setFlatten(initialFlatten)
      setReport(null)
      setRetrying(false)
    }
  }

  async function send(withFlatten: boolean, retry = false) {
    if (retry) setRetrying(true)
    else setView('working')
    try {
      const res = await postKill(withFlatten)
      if (!alive.current) return
      setReport(res)
      setView('report')
    } catch {
      if (!alive.current) return
      // A retry that fails keeps the report it had: the pause already happened then.
      if (!retry) setView('fail')
    } finally {
      if (alive.current) setRetrying(false)
      void qc.invalidateQueries({ queryKey: HEALTH_KEY })
    }
  }

  const account = t(`account.${env}`)

  let title: ReactNode
  let icon: ReactNode
  let tone: SheetTone = 'destructive'
  let description: ReactNode
  let body: ReactNode = null
  let actions: ReactNode

  if (view === 'working') {
    title = t('working')
    icon = <Spinner />
    tone = 'muted'
    description = t(flatten ? 'workingSubAll' : 'workingSub')
    actions = null
  } else if (view === 'fail') {
    title = t('fail.title')
    icon = <CloudOff strokeWidth={1.5} />
    description = t('fail.text')
    actions = (
      <>
        <SheetClose>
          <Button variant="secondary">{t('close')}</Button>
        </SheetClose>
        <Button variant="destructive" icon={<RefreshCw strokeWidth={1.75} />} onClick={() => void send(flatten)}>
          {t('r.retry')}
        </Button>
      </>
    )
  } else if (view === 'report' && report) {
    title = t('done.title')
    icon = <CirclePause strokeWidth={1.5} />
    tone = 'warning'
    description = <Tr k="done.text" values={{ time: formatClock(report.at, lang, false) }} />
    body = <KillReport report={report} retrying={retrying} onRetry={() => void send(report.flatten, true)} />
    actions = (
      <SheetClose>
        <Button variant="primary">{t('r.done')}</Button>
      </SheetClose>
    )
  } else {
    // Live (ATL-240, decision #251): the LIVE tag on the title and a real-money line; still one press.
    title =
      env === 'live' ? (
        <>
          {t('title')}
          <LiveTag />
        </>
      ) : (
        t('title')
      )
    icon = <OctagonX strokeWidth={1.5} />
    description = t(env === 'live' ? 'introLive' : 'intro', { account })
    body = <ConfirmBody preview={preview.data} flatten={flatten} onFlatten={setFlatten} account={account} lang={lang} />
    actions = <ConfirmActions flatten={flatten} onGo={() => void send(flatten)} />
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      trigger={trigger}
      title={title}
      description={description}
      icon={icon}
      tone={tone}
      actions={actions}
      className={cx('ks-sheet', view === 'report' && 'is-report', view === 'working' && 'is-working')}
      onOpenAutoFocus={(e) => {
        // Focus starts on Cancel, so a stray Enter never stops trading.
        const cancel = document.querySelector<HTMLElement>('[data-sheet] [data-ks-cancel]')
        if (cancel) {
          e.preventDefault()
          cancel.focus()
        }
      }}
    >
      <div
        aria-live={view === 'fail' ? 'assertive' : 'polite'}
        aria-busy={view === 'working'}
        className="grid gap-4"
        data-testid={`kill-view-${view}`}
      >
        {body}
      </div>
    </Sheet>
  )
}
