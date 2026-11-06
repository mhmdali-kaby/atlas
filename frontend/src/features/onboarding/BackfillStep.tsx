import { CircleArrowRight, CircleCheck, CloudOff, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Button, Skeleton, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import { useNow } from '../portfolio/hooks'
import { useBackfill } from '../settings-data/api'
import {
  buildModel,
  CLASS_ORDER,
  compact,
  etaSeconds,
  exact,
  pctText,
  stalledMinutes,
  summaryState,
  type Model,
  type Sample,
  type SummaryState,
} from '../settings-data/model'
import { Fig, NS as DATA_NS, Tr } from '../settings-data/parts'
import { useEtaText } from '../settings-data/Summary'
import '../settings-data/data.css'
import { Notice } from './Notice'
import { StepHead, StepMain, type StepProps } from './steps'
import './onboarding-n.css'

/** Polled the whole time the step is open (Settings → Data polls only while a job runs). */
const ALWAYS = Number.MAX_SAFE_INTEGER

/**
 * Backfill progress ("Market history", design onboarding.js backfill()): the same GET
 * /api/data/backfill summary Settings → Data polls every 5 s, as one status line, one bar, the
 * instruments done of total and the universe map. It never blocks: the page's Continue is
 * always enabled, at 0% too, and the collection carries on in the background.
 */
export function BackfillStep({ autoFocus }: StepProps) {
  const { t } = useTranslation('onboarding')
  const { t: td } = useTranslation(DATA_NS)
  const nowMs = useNow(5_000)
  const backfill = useBackfill(ALWAYS)
  const model = useMemo(() => (backfill.data ? buildModel(backfill.data) : null), [backfill.data])
  const serverNow = backfill.data ? Date.parse(backfill.data.as_of) + Math.max(0, nowMs - backfill.dataUpdatedAt) : nowMs
  const state: SummaryState | null = model ? summaryState(model, serverNow) : null
  const eta = useEta(model, backfill.dataUpdatedAt)
  const etaText = useEtaText()

  const head = <StepHead autoFocus={autoFocus} title={t('backfill.title')} lead={t('backfill.lead')} />
  const carry = (
    <p className="ob-carry" data-testid="backfill-carry">
      {state === 'done' ? <CircleCheck aria-hidden="true" strokeWidth={1.5} /> : <CircleArrowRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />}
      <span>{t(state === 'done' ? 'backfill.doneNote' : 'backfill.carryOn')}</span>
    </p>
  )

  if (backfill.isPending) {
    return (
      <>
        {head}
        <StepMain>
          <div aria-busy="true" aria-label={td('title.running')} className="grid gap-4" data-testid="backfill-loading">
            <Skeleton className="h-[220px] rounded-card" />
          </div>
          {carry}
        </StepMain>
      </>
    )
  }

  if (!model || (backfill.isError && !backfill.data)) {
    return (
      <>
        {head}
        <StepMain>
          <Notice
            tone="warning"
            role="alert"
            icon={CloudOff}
            title={t('backfill.error.title')}
            actions={
              <Button size="sm" icon={<RefreshCw />} onClick={() => void backfill.refetch()}>
                {t('retry')}
              </Button>
            }
          >
            {t('backfill.error.body')}
          </Notice>
          {carry}
        </StepMain>
      </>
    )
  }

  const st = state ?? 'notStarted'
  const fraction = st === 'done' ? 1 : st === 'notStarted' ? 0 : Math.min(model.pct, 0.99)
  const pct = pctText(fraction)
  const total = model.insts.length
  const done = model.counts.done

  let title: string
  let sub: ReactNode
  if (st === 'notStarted') {
    title = t('backfill.notStarted.title')
    sub = t('backfill.notStarted.body')
  } else {
    title = td(`title.${st}`)
    const vars = { pct, written: compact(model.written), expected: compact(model.expected) }
    if (st === 'running')
      sub = (
        <>
          <Tr k="sub.running" values={vars} />
          {eta !== null && <> {td('sub.eta', { eta: etaText(eta) })}</>}
        </>
      )
    else if (st === 'paused') sub = <Tr k="sub.paused" values={vars} />
    else if (st === 'stalled') sub = <Tr k="sub.stalled" values={{ mins: stalledMinutes(model, serverNow) }} />
    else sub = td('keeping')
  }
  const icon =
    st === 'done' ? <CircleCheck strokeWidth={1.75} /> : st === 'stalled' ? <TriangleAlert strokeWidth={1.75} /> : <Spinner size={16} />

  const failed = [...new Set(model.failedJobs.map((f) => f.instId))]

  return (
    <>
      {head}
      <StepMain>
        <section className="ob-bf" aria-labelledby="ob-bf-title" data-testid="backfill" data-state={st}>
          <div className={cx('ob-bf-status', `is-${st}`)} aria-live="polite">
            <span className="ob-bf-icon" aria-hidden="true">
              {icon}
            </span>
            <div>
              <p className="ob-bf-title" id="ob-bf-title">
                {title}
              </p>
              <p className="ob-bf-sub">{sub}</p>
            </div>
          </div>
          <div className="grid gap-2">
            <span
              className={cx('ob-bf-bar', st === 'stalled' && 'is-stalled')}
              role="progressbar"
              aria-label={t('backfill.progress')}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.floor(fraction * 100 + 1e-9)}
              aria-valuetext={pct}
              data-testid="backfill-bar"
            >
              <i style={{ '--p': fraction.toFixed(4) } as CSSProperties} />
            </span>
            <p className="ob-bf-counts">
              <span data-testid="backfill-insts">
                <Trans t={t} i18nKey="backfill.insts" count={total} values={{ done: exact(done), count: total }} components={{ f: <Fig /> }} />
              </span>
              <Fig className="ob-bf-pct">{pct}</Fig>
            </p>
          </div>
          {total > 0 && <UniverseMap model={model} stalled={st === 'stalled'} />}
        </section>
        {failed.length > 0 && (
          <Notice tone="warning" icon={TriangleAlert} title={t('backfill.failedN', { count: failed.length })}>
            {t('backfill.failedBody', { list: failed.slice(0, 3).join(', ') + (failed.length > 3 ? ' …' : '') })}
          </Notice>
        )}
        {carry}
      </StepMain>
    </>
  )
}

/** One square per instrument by asset class, as on Settings → Data (read-only here). */
function UniverseMap({ model: m, stalled }: { model: Model; stalled: boolean }) {
  const { t } = useTranslation('onboarding')
  const { t: td } = useTranslation(DATA_NS)
  let idx = 0
  const label = t('backfill.mapLabel', {
    n: m.insts.length,
    done: m.counts.done,
    fetching: m.counts.fetching,
    waiting: m.counts.waiting + m.counts.paused,
    failed: m.counts.failed,
  })
  return (
    <div className={cx('dt-map ob-bf-map', stalled && 'is-stalled')} role="img" aria-label={label} data-testid="backfill-map">
      {CLASS_ORDER.map((cls) => {
        const list = m.insts.filter((i) => i.assetClass === cls)
        if (list.length === 0) return null
        return (
          <div className="dt-map-group" key={cls}>
            <span className="dt-map-label" aria-hidden="true">
              {td(`class.${cls}`)}
              <Fig className="num">{exact(list.length)}</Fig>
            </span>
            <div className="dt-cells">
              {list.map((i) => (
                <i
                  key={i.instId}
                  className={`cell is-${i.status}`}
                  style={{ '--p': i.pct.toFixed(4), '--i': idx++ } as CSSProperties}
                  title={`${i.instId}: ${pctText(i.pct)}, ${td(`st.${i.status}`)}`}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Time left from the rows written over the last two minutes of polls (as Settings → Data does). */
function useEta(model: Model | null, updatedAt: number): number | null {
  const samples = useRef<Sample[]>([])
  const [eta, setEta] = useState<number | null>(null)
  useEffect(() => {
    if (!model || !updatedAt) return
    const list = samples.current
    if (list.length === 0 || list[list.length - 1]!.at !== updatedAt) list.push({ at: updatedAt, written: model.written })
    while (list.length > 0 && updatedAt - list[0]!.at > 150_000) list.shift()
    setEta(etaSeconds(list, model.expected - model.written))
  }, [model, updatedAt])
  return eta
}
