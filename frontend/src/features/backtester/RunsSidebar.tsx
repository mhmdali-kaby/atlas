import { CircleCheck, CircleX, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useSearchParams } from 'react-router'
import type { SidebarSectionProps } from '../../app/feature'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Skeleton, Spinner } from '../../ui'
import { useLang } from '../portfolio/hooks'
import { formatDate, formatTime } from '../warroom/time'
import { useRuns, type RunSummary } from './api'
import { baseOf } from './model'
import { NS } from './parts'
import './backtester.css'

/*
 * The Backtester area's sidebar (design backtester.js sidebar): "New backtest", then the recent runs
 * newest first, each with its status, instruments and start time. The list polls while one is
 * still going, so its spinner turns into a tick without a reload.
 */

function StatusIcon({ status }: { status: RunSummary['status'] }) {
  if (status === 'done') return <CircleCheck aria-hidden="true" className="bt-ic-done" />
  if (status === 'failed') return <CircleX aria-hidden="true" className="bt-ic-failed" />
  return (
    <span className="bt-ic-run">
      <Spinner />
    </span>
  )
}

export function RunsSidebar({ onNavigate }: SidebarSectionProps) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const env = useTradingEnv()
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  const current = pathname === '/backtester/run' ? params.get('id') : null
  const q = useRuns(env)
  const runs = q.data?.data

  return (
    <>
      <Link
        to="/backtester"
        data-nav-item=""
        aria-current={pathname === '/backtester' ? 'page' : undefined}
        onClick={onNavigate}
        className={
          'flex h-[34px] items-center gap-2.5 rounded-[9px] px-2.5 text-subheadline text-label no-underline hover:bg-fill-4 ' +
          (pathname === '/backtester' ? 'bg-selection font-semibold hover:bg-selection' : '')
        }
      >
        <Plus aria-hidden="true" strokeWidth={1.5} className="size-[18px] shrink-0 text-accent" />
        <span className="truncate">{t('new')}</span>
      </Link>
      <div className="side-section" role="group" aria-labelledby="bt-runs-h" data-testid="bt-runs">
        <h2 className="side-head bt-side-head" id="bt-runs-h">
          <span>{t('recent')}</span>
        </h2>
        {q.isPending ? (
          [120, 96, 132].map((w, i) => (
            <div className="bt-run-row" key={i} aria-hidden="true">
              <Skeleton style={{ width: 16, height: 16, borderRadius: '50%' }} />
              <span className="grid gap-1">
                <Skeleton style={{ width: w, height: 12 }} />
                <Skeleton style={{ width: w - 30, height: 10 }} />
              </span>
            </div>
          ))
        ) : !runs || runs.length === 0 ? (
          <p className="bt-side-none">{runs ? t('noRuns') : t('runsError')}</p>
        ) : (
          runs.map((r) => {
            const when = `${formatDate(r.created_at, lang)} ${formatTime(r.created_at, lang)}`
            const insts = r.inst_ids.map(baseOf).join(', ')
            return (
              <Link
                key={r.id}
                to={`/backtester/run?id=${encodeURIComponent(r.id)}`}
                className="bt-run-row"
                data-nav-item=""
                data-testid="bt-run-row"
                aria-current={current === r.id ? 'page' : undefined}
                onClick={onNavigate}
              >
                <span className="bt-run-ic">
                  <StatusIcon status={r.status} />
                </span>
                <span className="bt-run-text">
                  <bdi dir="ltr" className="bt-run-name">
                    {r.rule_name}
                  </bdi>
                  <span className="bt-run-sub">
                    <bdi dir="ltr">{insts}</bdi> {t(`st.${r.status}`)}, <span className="tabular-nums">{when}</span>
                  </span>
                </span>
              </Link>
            )
          })
        )}
      </div>
    </>
  )
}
