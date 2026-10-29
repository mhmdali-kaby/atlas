import { CircleAlert, CircleCheck, CirclePause, Clock, Pause, Play, RefreshCw, RotateCcw, ServerCrash, TriangleAlert } from 'lucide-react'
import { Fragment, type CSSProperties, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Spinner } from '../../ui'
import type { ActionAnswer } from './api'
import {
  CLASS_ORDER,
  compact,
  etaParts,
  exact,
  jobFraction,
  pctText,
  runningJobs,
  STATUS_ORDER,
  stalledMinutes,
  type DatasetKey,
  type InstFilter,
  type Model,
  type Status,
  type SummaryState,
  DATASET, tKey } from './model'
import { Fig, InstId, NS, Tip, Tr, useTimeText } from './parts'
import { formatClock } from '../portfolio/time'
import type { ApiJob } from './api'

export type BusyKind = 'pause' | 'resume' | 'resync' | 'start' | 'retry'

/** The summary's notice slot: a throttled or failed action, else the failed-jobs notice. */
export function Notice({
  answer,
  onRetry,
  children,
}: {
  answer: Exclude<ActionAnswer, 'ok'>
  onRetry: () => void
  children?: ReactNode
}) {
  const { t } = useTranslation(NS)
  if (answer === 'limited')
    return (
      <div className="dt-notice is-warning" role="status" data-testid="dt-notice-limited">
        <Clock aria-hidden="true" strokeWidth={1.75} />
        <div>
          <p className="dt-notice-title">{t('limited.title')}</p>
          <p className="dt-notice-body">{t('limited.body')}</p>
        </div>
      </div>
    )
  return (
    <div className="dt-notice is-error" role="alert" data-testid="dt-notice-down">
      <ServerCrash aria-hidden="true" strokeWidth={1.75} />
      <div>
        <p className="dt-notice-title">{t('down.title')}</p>
        <p className="dt-notice-body">
          <Tr k="down.body" />
        </p>
        <div className="dt-notice-actions">
          <Button size="sm" variant="secondary" onClick={onRetry}>
            {t('retry')}
          </Button>
          {children}
        </div>
      </div>
    </div>
  )
}

export function Lane({
  job,
  dataset,
  stacked,
  nowMs,
  gaps,
}: {
  job: ApiJob
  dataset: DatasetKey
  stacked?: boolean
  nowMs: number
  gaps?: { at: number; repaired: boolean }[]
}) {
  const { t } = useTranslation(NS)
  const { when } = useTimeText()
  const open = DATASET[dataset].open
  const st = job.status === 'running' ? 'fetching' : job.status === 'pending' ? 'waiting' : job.status
  const p = open ? (job.status === 'done' ? 1 : 0) : (jobFraction(job) ?? 0)
  const fromTs = job.target_from ?? (job.status === 'done' ? job.cursor_ts : null)
  return (
    <span className={`dt-lane is-${st}${open ? ' is-open' : ''}${stacked ? ' is-stacked' : ''}`} aria-hidden="true">
      <span className="dt-lane-from">{fromTs ? when(fromTs, false, nowMs) : ''}</span>
      <span className="dt-lane-bar">
        <i className="dt-lane-fill" style={{ '--p': p.toFixed(4) } as CSSProperties} />
        {gaps?.map((g, k) => (
          <span key={k} className={`dt-gap${g.repaired ? ' is-repaired' : ''}`} style={{ '--at': g.at.toFixed(4) } as CSSProperties} />
        ))}
      </span>
      <span className="dt-lane-to">{t('now.lane')}</span>
    </span>
  )
}

function SummaryIcon({ state }: { state: SummaryState }) {
  if (state === 'done')
    return (
      <span className="dt-sum-icon is-done" aria-hidden="true">
        <CircleCheck strokeWidth={1.75} />
      </span>
    )
  if (state === 'paused')
    return (
      <span className="dt-sum-icon is-paused" aria-hidden="true">
        <Pause strokeWidth={1.75} />
      </span>
    )
  if (state === 'stalled')
    return (
      <span className="dt-sum-icon is-warning" aria-hidden="true">
        <TriangleAlert strokeWidth={1.75} />
      </span>
    )
  return (
    <span className="dt-sum-icon" aria-hidden="true">
      <Spinner />
    </span>
  )
}

function useEtaText() {
  const { t } = useTranslation(NS)
  return (sec: number) => {
    const parts = etaParts(sec)
    if (parts === 'lessThanMinute') return t('eta.lessThanMinute')
    return [parts.h ? t('eta.h', { count: parts.h }) : '', parts.m ? t('eta.m', { count: parts.m }) : '']
      .filter(Boolean)
      .join(t('eta.join'))
  }
}

const LEGEND_FILTER: Record<Status, InstFilter> = { done: 'done', failed: 'failed', fetching: 'progress', waiting: 'progress', paused: 'progress' }

export interface SummaryProps {
  model: Model
  state: Exclude<SummaryState, 'notStarted'>
  nowMs: number
  updatedAt: number
  etaSec: number | null
  busy: BusyKind | null
  notice: Exclude<ActionAnswer, 'ok'> | null
  celebrate: boolean
  storageTotal: number | null
  onAction: (kind: BusyKind) => void
  onFilter: (filter: InstFilter) => void
  onOpen: (instId: string) => void
  bytes: (n: number) => string
}

export function Summary(p: SummaryProps) {
  const { t } = useTranslation(NS)
  const { when, comma, lang } = useTimeText()
  const etaText = useEtaText()
  const { model: m, state } = p

  const vars = { pct: pctText(m.pct), written: compact(m.written), expected: compact(m.expected) }
  let sub: ReactNode = null
  if (state === 'running')
    sub = (
      <>
        <Tr k="sub.running" values={vars} />
        {p.etaSec !== null && <> {t('sub.eta', { eta: etaText(p.etaSec) })}</>}
      </>
    )
  if (state === 'paused') sub = <Tr k="sub.paused" values={vars} />
  if (state === 'stalled') sub = <Tr k="sub.stalled" values={{ mins: stalledMinutes(m, p.nowMs) }} />
  if (state === 'done')
    sub = (
      <Tr
        k="sub.done"
        values={{ n: exact(m.insts.length), bars: compact(m.written), size: p.storageTotal === null ? '—' : p.bytes(p.storageTotal) }}
      />
    )

  const busyBtn = (kind: BusyKind, label: string, busyLabel: string, icon: ReactNode, variant: 'primary' | 'secondary') => {
    const on = p.busy === kind
    const btn = (
      <Button
        variant={variant}
        icon={icon}
        loading={on}
        data-testid={`dt-${kind}`}
        onClick={() => {
          if (!p.busy) p.onAction(kind)
        }}
      >
        {t(on ? busyLabel : label)}
      </Button>
    )
    return kind === 'resync' ? <Tip label={t('resyncTip')}>{btn}</Tip> : btn
  }
  let action: ReactNode
  if (state === 'running' || state === 'stalled') action = busyBtn('pause', 'pause', 'pausing', <Pause />, 'secondary')
  else if (state === 'paused') action = busyBtn('resume', 'resume', 'resuming', <Play />, 'primary')
  else action = busyBtn('resync', 'resync', 'resyncing', <RefreshCw />, 'secondary')

  // Notice slot: an action's answer wins; otherwise failed jobs.
  let notice: ReactNode = null
  if (p.notice) notice = <Notice answer={p.notice} onRetry={() => p.onAction(state === 'paused' ? 'resume' : 'resync')} />
  else if (m.failedJobs.length > 0) {
    const fj = m.failedJobs
    const names = [...new Set(fj.map((f) => f.instId))]
    const shown = names.slice(0, 2).map((id) => <InstId key={id} id={id} />)
    const list =
      names.length > 2 ? (
        <>
          {shown[0]}
          {comma}
          {shown[1]} {t('now.more', { n: names.length - 2 })}
        </>
      ) : (
        shown.map((s, k) => (
          <Fragment key={k}>
            {k > 0 && t('failed.and')}
            {s}
          </Fragment>
        ))
      )
    notice = (
      <div className="dt-notice is-error" role="alert" data-testid="dt-notice-failed">
        <CircleAlert aria-hidden="true" strokeWidth={1.75} />
        <div>
          <p className="dt-notice-title">{t('failed.title', { count: fj.length })}</p>
          <p className="dt-notice-body">
            {t('failed.body', { list: '\u0000' })
              .split('\u0000')
              .map((part, k) => (
                <Fragment key={k}>
                  {k > 0 && list}
                  {part}
                </Fragment>
              ))}
          </p>
          <div className="dt-notice-actions">
            <Button
              size="sm"
              variant="secondary"
              icon={<RotateCcw />}
              loading={p.busy === 'retry'}
              onClick={() => {
                if (!p.busy) p.onAction('retry')
              }}
            >
              {t('failed.retry')}
            </Button>
            <Button size="sm" variant="plain" onClick={() => p.onFilter('failed')}>
              {t('failed.show')}
            </Button>
          </div>
        </div>
      </div>
    )
  }

  // The universe map: one square per instrument, grouped by asset class, in the table's order.
  let idx = 0
  const groups = CLASS_ORDER.map((cls) => {
    const list = m.insts.filter((i) => i.assetClass === cls)
    if (list.length === 0) return null
    return (
      <div className="dt-map-group" key={cls}>
        <span className="dt-map-label" aria-hidden="true">
          {t(`class.${cls}`)}
          <Fig className="num">{list.length}</Fig>
        </span>
        <div className="dt-cells">
          {list.map((i) => {
            const n = idx++
            return (
              <i
                key={i.instId}
                className={`cell is-${i.status}`}
                data-inst={i.instId}
                style={{ '--p': i.pct.toFixed(4), '--i': n } as CSSProperties}
                title={`${i.instId}: ${pctText(i.pct)}, ${t(`st.${i.status}`)}`}
                onClick={() => p.onOpen(i.instId)}
              />
            )
          })}
        </div>
      </div>
    )
  })
  const present = STATUS_ORDER.filter((s) => m.counts[s] > 0)
  const mapLabel = t('mapLabel', {
    n: m.insts.length,
    parts: present.map((s) => `${m.counts[s]} ${t(`st.${s}`)}`).join(comma),
  })

  // Fetching now (or the up-to-date / paused line).
  const updated = <Tr k="updated" values={{ time: formatClock(p.updatedAt, lang) }} />
  let now: ReactNode
  if (state === 'done') {
    now = (
      <p className="dt-keeping">
        <CircleCheck aria-hidden="true" strokeWidth={1.75} />
        <span>{t('keeping')}</span>
        <span className="dt-updated ms-auto">{updated}</span>
      </p>
    )
  } else if (state === 'paused') {
    now = (
      <p className="dt-keeping is-paused">
        <CirclePause aria-hidden="true" strokeWidth={1.75} />
        <span>{t('pausedNote')}</span>
      </p>
    )
  } else {
    const running = runningJobs(m)
    now = (
      <>
        <div className="dt-now-head">
          <h3 className="dt-now-title">{t('now.title')}</h3>
          <span className="dt-updated" data-testid="dt-updated">
            {updated}
          </span>
        </div>
        <ul className="dt-now-list">
          {running.slice(0, 3).map(({ instId, dataset, job }) => {
            const frac = jobFraction(job)
            const def = DATASET[dataset]
            return (
              <li className="dt-now-row" key={`${instId}|${dataset}`}>
                <InstId id={instId} />
                <span className="dt-now-ds">{t(`ds.${tKey(dataset)}`)}</span>
                <Lane job={job} dataset={dataset} nowMs={p.nowMs} />
                <span className="dt-now-reach">
                  {def.open ? (
                    <Tr k="soFar" values={{ n: exact(job.rows_written) }} />
                  ) : job.cursor_ts ? (
                    <>{t('now.backTo', { when: '\u0000' }).split('\u0000').map((part, k) => (
                      <Fragment key={k}>
                        {k > 0 && when(job.cursor_ts!, (def.barMs ?? 0) < 86_400_000, p.nowMs)}
                        {part}
                      </Fragment>
                    ))}</>
                  ) : null}
                </span>
                <span className="dt-now-pct">{frac === null ? '' : <Fig>{pctText(frac)}</Fig>}</span>
              </li>
            )
          })}
        </ul>
        {running.length > 3 && <p className="dt-now-more">{t('now.more', { n: running.length - 3 })}</p>}
      </>
    )
  }

  return (
    <section className={`dt-group dt-summary${p.celebrate ? ' is-celebrating' : ''}`} aria-labelledby="dt-sum-title" data-testid="dt-summary" data-state={state}>
      <div className="dt-sum-head">
        <SummaryIcon state={state} />
        <div className="dt-sum-text" aria-live="polite">
          <h2 className="dt-sum-title" id="dt-sum-title">
            {t(`title.${state}`)}
          </h2>
          <p className="dt-sum-sub">{sub}</p>
        </div>
        <div className="dt-sum-actions">{action}</div>
      </div>
      {notice}
      <div className={`dt-map${state === 'stalled' ? ' is-stalled' : ''}${p.celebrate ? ' is-complete' : ''}`} role="img" aria-label={mapLabel} data-testid="dt-map">
        {groups}
      </div>
      <div className="dt-legend">
        {present.map((s) => (
          <button key={s} type="button" className="dt-key" onClick={() => p.onFilter(LEGEND_FILTER[s])}>
            <i className={`cell is-${s}`} style={{ '--p': s === 'done' ? 1 : 0.5 } as CSSProperties} aria-hidden="true" />
            <span>{t(`st.${s}`)}</span>
            <Fig className="num">{exact(m.counts[s])}</Fig>
          </button>
        ))}
        <span className="dt-legend-note">{t('legendNote')}</span>
      </div>
      <div className="dt-now">{now}</div>
    </section>
  )
}
