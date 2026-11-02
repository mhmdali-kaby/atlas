import { CircleX, Pencil, SearchX } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatFixed } from '../../lib/format'
import { Button, Skeleton, Spinner, toast } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { isActive, useRun, type RunDetail } from './api'
import { fromRequest } from './model'
import { fill, NS, StatusChip } from './parts'
import { Sentence } from './Sentence'
import './backtester.css'

/*
 * One run (/backtester/run?id=…; design runningHtml / failedHtml). While it is queued or running
 * the page polls GET /api/backtests/{id} every 2 s and shows the running panel with the seconds
 * since it was started; done shows its header and rule (ATL-210 adds the results below), failed
 * shows the error with its reason. The run keeps going if the owner leaves.
 */

const KNOWN_ERRORS = new Set(['invalid', 'too_many_bars', 'no_data', 'insufficient_data', 'timeout', 'trading_core_error', 'trading_core_unavailable', 'job_failed'])

function Header({ run, again }: { run: RunDetail; again: boolean }) {
  const { t } = useTranslation(NS)
  return (
    <header className="bt-head">
      <div className="bt-head-text">
        <h2 className="text-title-1">
          <bdi dir="ltr">{run.rule_name}</bdi>
        </h2>
      </div>
      <div className="bt-head-aside">
        <StatusChip status={run.status} />
        {again && (
          <Link to={`/backtester?from=${encodeURIComponent(run.id)}`} className="bt-link-btn">
            <Pencil aria-hidden="true" strokeWidth={1.75} />
            <span>{t('again')}</span>
          </Link>
        )}
      </div>
    </header>
  )
}

function Running({ run }: { run: RunDetail }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const now = useNow(1000)
  const since = Date.parse(run.started_at ?? run.created_at)
  const secs = Number.isNaN(since) ? 0 : Math.max(0, Math.floor((now - since) / 1000))
  const count = run.inst_ids.length
  return (
    <section className="bt-panel bt-run-p" role="status" aria-live="polite" data-testid="bt-running">
      <div className="bt-run-orbit" aria-hidden="true">
        <Spinner size={28} />
      </div>
      <p className="text-title-3">
        {fill(t('running'), {
          insts: t('insts', { count }),
          bar: <bdi dir="ltr">{run.bar}</bdi>,
        })}
      </p>
      <p className="bt-run-time" aria-hidden="true" data-testid="bt-elapsed">
        <bdi dir="ltr" className="tabular-nums">{fill(t('elapsed'), { s: formatFixed(secs, 0, lang) })}</bdi>
      </p>
      <p className="text-subheadline text-label-secondary">{t('runningSub')}</p>
    </section>
  )
}

function Failed({ run }: { run: RunDetail }) {
  const { t } = useTranslation(NS)
  const code = run.error && KNOWN_ERRORS.has(run.error) ? run.error : 'trading_core_error'
  return (
    <section className="bt-panel bt-fail-p" role="alert" data-testid="bt-failed">
      <div className="bt-state-icon is-error">
        <CircleX aria-hidden="true" strokeWidth={1.5} />
      </div>
      <h3 className="text-title-3">{t(`e.${code}`)}</h3>
      {run.error_reason && (
        <p className="bt-reason">
          <code dir="ltr">{run.error_reason}</code>
        </p>
      )}
      <p className="text-subheadline text-label-secondary">{t(`e.fix.${code}`)}</p>
      <Link to={`/backtester?from=${encodeURIComponent(run.id)}`} className="bt-link-btn">
        <Pencil aria-hidden="true" strokeWidth={1.75} />
        <span>{t('again')}</span>
      </Link>
    </section>
  )
}

export function RunPage() {
  const { t } = useTranslation(NS)
  const env = useTradingEnv()
  const [params] = useSearchParams()
  const id = params.get('id')
  const q = useRun(env, id)
  const run = q.data
  const lastStatus = useRef<string | undefined>(undefined)

  // A run that finishes while the owner watches says so (design: the toast on completion).
  useEffect(() => {
    const prev = lastStatus.current
    lastStatus.current = run?.status
    if (run && prev && isActive(prev as RunDetail['status']) && run.status === 'done') toast({ title: t('doneToast'), body: run.rule_name })
  }, [run, t])

  if (!id || q.error?.status === 404) {
    return (
      <div className="bt-page">
        <section className="bt-panel bt-fail-p" role="status" data-testid="bt-notfound">
          <div className="bt-state-icon">
            <SearchX aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h3 className="text-title-3">{t('notFound')}</h3>
          <p className="text-subheadline text-label-secondary">{t('notFoundBody')}</p>
          <Link to="/backtester" className="bt-link-btn">
            <span>{t('new')}</span>
          </Link>
        </section>
      </div>
    )
  }
  if (q.error) {
    return (
      <div className="bt-page">
        <section className="bt-panel bt-fail-p" role="alert" data-testid="bt-loaderror">
          <div className="bt-state-icon is-error">
            <CircleX aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h3 className="text-title-3">{t('loadError')}</h3>
          <Button onClick={() => void q.refetch()}>{t('retry')}</Button>
        </section>
      </div>
    )
  }
  if (!run) {
    return (
      <div className="bt-page" aria-busy="true">
        <Skeleton style={{ width: 240, height: 32 }} />
        <Skeleton style={{ height: 72 }} />
        <Skeleton style={{ height: 220 }} />
      </div>
    )
  }

  const form = fromRequest(run.request)
  return (
    <div className="bt-page" data-testid="bt-run-page" data-status={run.status}>
      <Header run={run} again={run.status === 'done'} />
      <Sentence form={form} compact />
      {isActive(run.status) && <Running run={run} />}
      {run.status === 'failed' && <Failed run={run} />}
      {/* run.status === 'done': ATL-210 renders run.result here. */}
    </div>
  )
}
