import { ChevronDown, ChevronUp, CircleAlert, CircleCheck, RefreshCw, ShieldCheck } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../ui'
import type { KillItem, KillReport as KillReportData } from './api'
import { Fig, Inst, NS, Tr } from './parts'

function itemKind(item: KillItem): string {
  if (item.kind === 'algo') return `kind.${item.algo_kind ?? 'trigger'}`
  return `kind.${item.kind}`
}

/** The per-item result of a kill: cancelled, kept, closed and failed (with OKX's message). */
export function KillReport({ report, retrying, onRetry }: { report: KillReportData; retrying: boolean; onRetry: () => void }) {
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
            <span className="inline-flex items-center gap-1">
              {t(showCancelled ? 'r.hide' : 'r.show')}
              {showCancelled ? (
                <ChevronUp aria-hidden="true" className="size-3.5" />
              ) : (
                <ChevronDown aria-hidden="true" className="size-3.5" />
              )}
            </span>
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
              <Tr k="r.keptLine" count={keptCount} />
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
                  <span className="ks-side">
                    {i.pos_side && i.pos_side !== 'net' ? t(i.pos_side === 'short' ? 'short' : 'long') : null}
                  </span>
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
