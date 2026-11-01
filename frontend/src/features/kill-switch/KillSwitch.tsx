import { useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronUp,
  CircleAlert,
  CircleCheck,
  CirclePause,
  CircleX,
  CloudOff,
  OctagonX,
  RefreshCw,
  ShieldCheck,
  Shrink,
} from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatSigned, signOf } from '../../lib/format'
import { Button, Sheet, SheetClose, Spinner, Toggle } from '../../ui'
import { cx } from '../../ui/cx'
import { formatClock } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import {
  HEALTH_KEY,
  HOLD_MS,
  postKill,
  useKillPreview,
  useSystemEvents,
  useSystemHealth,
  type KillItem,
  type KillPreview,
  type KillReport,
} from './api'
import { Fig, Inst, NS, sumDecimals, Tr } from './parts'
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
        </button>
      }
    />
  )
}

export function KillSheet({
  open,
  onOpenChange,
  trigger,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  trigger?: ReactNode
}) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const env = useTradingEnv()
  const qc = useQueryClient()
  const preview = useKillPreview(open, env)
  const [view, setView] = useState<View>('confirm')
  const [flatten, setFlatten] = useState(false)
  const [report, setReport] = useState<KillReport | null>(null)
  const [retrying, setRetrying] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  // Each opening starts over at the confirmation, with close-all off.
  useEffect(() => {
    if (open) {
      setView('confirm')
      setFlatten(false)
      setReport(null)
      setRetrying(false)
    }
  }, [open])

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

  let title: string
  let icon: ReactNode
  let tone: 'default' | 'destructive' = 'destructive'
  let description: ReactNode
  let body: ReactNode = null
  let actions: ReactNode

  if (view === 'working') {
    title = t('working')
    icon = <Spinner />
    tone = 'default'
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
    tone = 'default'
    description = <Tr k="done.text" values={{ time: formatClock(report.at, lang, false) }} />
    body = <Report report={report} retrying={retrying} onRetry={() => void send(report.flatten, true)} />
    actions = (
      <SheetClose>
        <Button variant="primary">{t('r.done')}</Button>
      </SheetClose>
    )
  } else {
    title = t('title')
    icon = <OctagonX strokeWidth={1.5} />
    description = t('intro', { account })
    body = (
      <ConfirmBody
        preview={preview.data}
        flatten={flatten}
        onFlatten={setFlatten}
        account={account}
        lang={lang}
      />
    )
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

function ConfirmBody({
  preview,
  flatten,
  onFlatten,
  account,
  lang,
}: {
  preview: KillPreview | undefined
  flatten: boolean
  onFlatten: (on: boolean) => void
  account: string
  lang: 'en' | 'ar'
}) {
  const { t } = useTranslation(NS)
  const labelId = useId()
  const subId = useId()
  const n = preview?.positions.length
  const upl = preview ? sumDecimals(preview.positions.map((p) => p.upl_usd)) : null
  const what: [ReactNode, ReactNode][] = [
    [
      <CircleX key="i" />,
      preview ? (
        flatten ? (
          <Tr k="w.cancelAll" values={{ open: preview.openOrders, algo: preview.takeProfits + preview.stops }} />
        ) : (
          <Tr k="w.cancelKeep" values={{ open: preview.openOrders, tp: preview.takeProfits, account }} />
        )
      ) : (
        t(flatten ? 'w.cancelAllAny' : 'w.cancelKeepAny', { account })
      ),
    ],
    [
      flatten ? <Shrink key="i" /> : <ShieldCheck key="i" />,
      preview ? (
        flatten ? (
          <Tr k="w.close" values={{ n }} />
        ) : (
          <Tr k="w.keep" values={{ n: preview.stops }} />
        )
      ) : (
        t(flatten ? 'w.closeAny' : 'w.keepAny')
      ),
    ],
    [<CirclePause key="i" />, t('w.pause')],
  ]

  return (
    <>
      <section className="ks-what" aria-labelledby={`${labelId}-what`}>
        <h3 className="ks-h" id={`${labelId}-what`}>
          {t('what')}
        </h3>
        <ul className="ks-list" role="list">
          {what.map(([ic, text], i) => (
            <li key={i}>
              <span className="ks-li-ic" aria-hidden="true">
                {ic}
              </span>
              <span>{text}</span>
            </li>
          ))}
        </ul>
      </section>
      <div className={cx('ks-group ks-flatten', flatten && 'is-on')}>
        <div className="ks-row">
          <div className="ks-row-main">
            <span className="ks-row-label" id={labelId}>
              {n === undefined ? t('flattenAny') : <Tr k="flatten" values={{ n }} />}
            </span>
            <span className="ks-row-sub" id={subId}>
              {upl === null ? t('flattenSubAny') : <Tr k="flattenSub" values={{ upl: formatSigned(upl, 'USD', lang) }} />}
            </span>
          </div>
          <Toggle
            checked={flatten}
            onCheckedChange={onFlatten}
            aria-labelledby={labelId}
            aria-describedby={subId}
            data-testid="kill-flatten"
          />
        </div>
        {flatten && preview && preview.positions.length > 0 && (
          <ul className="ks-pos" role="list">
            {preview.positions.map((p) => (
              <li key={p.id}>
                <Inst id={p.inst_id} />
                <span className="ks-side">{t(p.side === 'short' ? 'short' : 'long')}</span>
                <Fig className={cx('ks-upl', signClass(p.upl_usd))}>{formatSigned(p.upl_usd, 'USD', lang)}</Fig>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

function signClass(v: string | null | undefined) {
  const s = signOf(v)
  return s === 1 ? 'is-gain' : s === -1 ? 'is-loss' : undefined
}

function ConfirmActions({ flatten, onGo }: { flatten: boolean; onGo: () => void }) {
  const { t } = useTranslation(NS)
  return (
    <>
      <SheetClose>
        <Button variant="secondary" data-ks-cancel="">
          {t('cancel')}
        </Button>
      </SheetClose>
      {flatten ? (
        <HoldButton onComplete={onGo} />
      ) : (
        <Button variant="destructive" icon={<OctagonX strokeWidth={1.75} />} onClick={onGo} data-testid="kill-go">
          {t('go')}
        </Button>
      )}
    </>
  )
}

type Hint = 'idle' | 'holding' | 'early'

/**
 * Press and hold for 1.5 s (decision 251): pointer, or Space/Enter held. Released early,
 * nothing is sent and the hint says so. Clicks alone never fire it.
 */
export function HoldButton({ onComplete }: { onComplete: () => void }) {
  const { t } = useTranslation(NS)
  const [hint, setHint] = useState<Hint>('idle')
  const [done, setDone] = useState(false)
  const timer = useRef<number | null>(null)
  const hintId = useId()

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  function start() {
    if (timer.current !== null || done) return
    setHint('holding')
    timer.current = window.setTimeout(() => {
      timer.current = null
      setDone(true)
      onComplete()
    }, HOLD_MS)
  }

  function stop() {
    if (timer.current === null) return
    window.clearTimeout(timer.current)
    timer.current = null
    setHint('early')
  }

  const isKey = (e: KeyboardEvent) => e.key === ' ' || e.key === 'Enter'

  return (
    <>
      <p className={cx('ks-hold-hint', hint === 'early' && 'is-early')} id={hintId} aria-live="polite">
        {t(hint === 'early' ? 'holdEarly' : hint === 'holding' ? 'holdKeep' : 'holdHint')}
      </p>
      <button
        type="button"
        className={cx('ks-hold', hint === 'holding' && 'is-holding', done && 'is-done')}
        aria-describedby={hintId}
        data-testid="kill-hold"
        onPointerDown={(e: PointerEvent<HTMLButtonElement>) => {
          if (e.button > 0) return
          try {
            e.currentTarget.setPointerCapture(e.pointerId)
          } catch {
            /* not capturable (tests, some browsers) */
          }
          start()
        }}
        onPointerUp={stop}
        onPointerCancel={stop}
        onLostPointerCapture={stop}
        onKeyDown={(e) => {
          if (!isKey(e)) return
          e.preventDefault()
          if (!e.repeat) start()
        }}
        onKeyUp={(e) => {
          if (!isKey(e)) return
          e.preventDefault()
          stop()
        }}
        onBlur={stop}
        onClick={(e) => e.preventDefault()}
        onContextMenu={(e) => e.preventDefault()}
      >
        <span className="ks-hold-fill" aria-hidden="true" />
        <span className="ks-hold-label">
          <OctagonX aria-hidden="true" strokeWidth={1.75} />
          <span>{t('hold')}</span>
        </span>
      </button>
    </>
  )
}

function itemKind(item: KillItem): string {
  if (item.kind === 'algo') return `kind.${item.algo_kind ?? 'trigger'}`
  return `kind.${item.kind}`
}

function Report({ report, retrying, onRetry }: { report: KillReport; retrying: boolean; onRetry: () => void }) {
  const { t } = useTranslation(NS)
  const [showCancelled, setShowCancelled] = useState(false)
  const listId = useId()
  const cancelled = report.items.filter((i) => i.action === 'cancelled')
  const kept = report.items.filter((i) => i.action === 'kept')
  const closed = report.items.filter((i) => i.kind === 'position' && i.action === 'closed')
  const failed = report.failed.length > 0 ? report.failed : report.items.filter((i) => i.action === 'failed')
  const cancelledCount = Math.max(report.cancelled, cancelled.length)
  const keptCount = Math.max(report.stops_kept, kept.length)
  const closedCount = Math.max(report.closed, closed.length)

  return (
    <div className="ks-group ks-res" data-testid="kill-report">
      <div className="ks-row ks-res-row" data-testid="kill-cancelled">
        <span className="ks-res-ic is-ok" aria-hidden="true">
          <CircleCheck />
        </span>
        <span className="ks-row-main">
          <span className="ks-row-label">{t('r.cancelled')}</span>
          <span className="ks-row-sub">
            <Tr k="n.orders" count={cancelledCount} />
            {report.already_done > 0 && (
              <>
                {' '}
                <Tr k="r.alreadyDone" values={{ n: report.already_done }} />
              </>
            )}
          </span>
        </span>
        {cancelled.length > 0 && (
          <Button
            variant="plain"
            size="sm"
            aria-expanded={showCancelled}
            aria-controls={listId}
            onClick={() => setShowCancelled((s) => !s)}
          >
            {t(showCancelled ? 'r.hide' : 'r.show')}
            {showCancelled ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
          </Button>
        )}
      </div>
      {cancelled.length > 0 && (
        <ul className="ks-ords" id={listId} role="list" hidden={!showCancelled}>
          {cancelled.map((i) => (
            <li key={`${i.kind}-${i.ref}`} className="ks-ord">
              <Inst id={i.inst_id} />
              <span className="ks-ord-what">{t(itemKind(i))}</span>
            </li>
          ))}
        </ul>
      )}

      {keptCount > 0 && (
        <div className="ks-row ks-res-row" data-testid="kill-kept">
          <span className="ks-res-ic is-kept" aria-hidden="true">
            <ShieldCheck />
          </span>
          <span className="ks-row-main">
            <span className="ks-row-label">{t('r.kept')}</span>
            <span className="ks-row-sub">
              <Tr k="n.stops" count={keptCount} />. {t('r.keptNote')}
            </span>
          </span>
        </div>
      )}

      {closedCount > 0 && (
        <>
          <div className="ks-row ks-res-row" data-testid="kill-closed">
            <span className="ks-res-ic is-ok" aria-hidden="true">
              <CircleCheck />
            </span>
            <span className="ks-row-main">
              <span className="ks-row-label">{t('r.closed')}</span>
              <span className="ks-row-sub">
                <Tr k="n.positions" count={closedCount} />
              </span>
            </span>
          </div>
          {closed.length > 0 && (
            <ul className="ks-closed" role="list">
              {closed.map((i) => (
                <li key={`${i.inst_id}-${i.pos_side ?? ''}-${i.ref}`}>
                  <Inst id={i.inst_id} />
                  <span className="ks-side">{i.pos_side && i.pos_side !== 'net' ? t(i.pos_side === 'short' ? 'short' : 'long') : null}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {failed.length > 0 && (
        <>
          <div className="ks-row ks-res-row is-failed" role="alert" data-testid="kill-failed">
            <span className="ks-res-ic is-fail" aria-hidden="true">
              <CircleAlert />
            </span>
            <span className="ks-row-main">
              <span className="ks-row-label">{t('r.failed')}</span>
              <span className="ks-row-sub">
                <Tr k="n.items" count={failed.length} />
              </span>
            </span>
          </div>
          <ul className="ks-fails" role="list">
            {failed.map((i, n) => (
              <li key={`${i.kind}-${i.ref}-${n}`} data-testid="kill-failure">
                <p className="ks-fail-msg">
                  <span className="ks-fail-what">
                    {t(itemKind(i))} · <Inst id={i.inst_id} />
                    {i.code ? (
                      <>
                        {' '}
                        · <Fig>{i.code}</Fig>
                      </>
                    ) : null}
                  </span>
                  <bdi className="ks-fail-text">{i.message || t('r.noMessage')}</bdi>
                </p>
              </li>
            ))}
          </ul>
          <div className="ks-retry">
            <Button
              variant="secondary"
              size="sm"
              loading={retrying}
              icon={retrying ? undefined : <RefreshCw aria-hidden="true" />}
              onClick={onRetry}
            >
              {t(retrying ? 'r.retrying' : 'r.retry')}
            </Button>
          </div>
        </>
      )}
    </div>
  )
}
