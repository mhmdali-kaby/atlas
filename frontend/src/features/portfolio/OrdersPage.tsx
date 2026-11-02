import { Info, ListChecks, Shield } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { CancelOrderSheet } from './CancelOrderSheet'
import { POLL_MS, useOrders, useSummary, type OpenOrder } from './api'
import { useLang, useNow } from './hooks'
import { InstFilterChip, useInstFilterParam } from './instFilter'
import { ALGO_COLUMNS, ALGO_NUMERIC, OPEN_COLUMNS, OPEN_NUMERIC } from './ordersFormat'
import { AlgoRow, OpenRow, TableSkeleton } from './OrdersTables'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import './portfolio.css'

/*
 * Portfolio → Orders (brief §13, design v11 #/portfolio/orders, ATL-150). The orders resting
 * on OKX right now, from GET /api/portfolio/orders (ATL-148, polled every 30 s): open orders
 * with a Cancel sheet (POST /api/orders/{id}/cancel), and algo orders read-only, because the
 * API has no route to cancel one on its own (cancelling the entry takes its stop and targets).
 */

function SectionHead({ id, title, note }: { id: string; title: string; note: string }) {
  return (
    <div className="grid gap-0.5 px-1 pb-2.5">
      <h3 id={id} className="text-headline">
        {title}
      </h3>
      <p className="max-w-[70ch] text-footnote text-label-secondary">{note}</p>
    </div>
  )
}

export function OrdersPage() {
  const { t } = useTranslation('portfolio-orders')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(POLL_MS)
  const orders = useOrders(env)
  const summary = useSummary(env)
  const [cancelling, setCancelling] = useState<OpenOrder | null>(null)
  const [instId, clearInstFilter] = useInstFilterParam()

  if (orders.isError && !orders.data) {
    return (
      <StateBlock
        kind="error"
        title={t('error.title')}
        body={t('error.body')}
        onRetry={() => {
          void orders.refetch()
          void summary.refetch()
        }}
      />
    )
  }

  const loading = orders.isPending
  const openAll = orders.data?.data.orders ?? []
  const algoAll = orders.data?.data.algo_orders ?? []
  const open = instId ? openAll.filter((o) => o.inst_id === instId) : openAll
  const algo = instId ? algoAll.filter((a) => a.inst_id === instId) : algoAll
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 300, height: 26 }} />
        ) : (
          (open.length > 0 || algo.length > 0) && (
            <h2 data-testid="orders-summary" className="text-title-2">
              {t('summary', { open: t('open', { count: open.length }), algo: t('algo', { count: algo.length }) })}
            </h2>
          )
        )}
      </div>
      <div>{s ? <ReconciledIndicator summary={s} lang={lang} now={now} /> : <Skeleton style={{ width: 250, height: 26 }} />}</div>
    </header>
  )

  return (
    <div aria-busy={loading || undefined}>
      {loading && (
        <span className="sr-only" role="status" data-testid="orders-loading">
          {t('loading')}
        </span>
      )}
      {head}
      {!loading && instId && (
        <InstFilterChip instId={instId} shown={open.length + algo.length} total={openAll.length + algoAll.length} onClear={clearInstFilter} />
      )}
      <section aria-labelledby="ord-open-title">
        <SectionHead id="ord-open-title" title={t('openTitle')} note={t('openNote')} />
        {loading ? (
          <TableSkeleton cols={OPEN_COLUMNS.length + 1} rows={2} testId="open-orders-loading" />
        ) : open.length === 0 && !instId ? (
          <div className="pf-panel">
            <StateBlock kind="none" compact icon={ListChecks} title={t('empty.title')} body={t('empty.body')} />
          </div>
        ) : (
          <div className="pf-panel">
            <table className="pf-table" data-testid="open-orders-table" aria-labelledby="ord-open-title">
              <thead>
                <tr>
                  {OPEN_COLUMNS.map((c) => (
                    <th key={c} scope="col" data-col={c} className={OPEN_NUMERIC.has(c) ? 'is-num' : undefined}>
                      {t(`col.${c}`)}
                    </th>
                  ))}
                  <th scope="col" data-col="actions" className="w-[1%]">
                    <span className="sr-only">{t('col.actions')}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {open.length === 0 ? (
                  <tr data-testid="open-orders-filtered-empty">
                    <td colSpan={OPEN_COLUMNS.length + 1} className="px-5 py-4 text-footnote text-label-secondary">
                      {t('filteredEmptyOpen', { inst: instId })}
                    </td>
                  </tr>
                ) : (
                  open.map((o) => <OpenRow key={o.id} o={o} lang={lang} now={now} onCancel={setCancelling} />)
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section aria-labelledby="ord-algo-title" className="mt-7">
        <SectionHead id="ord-algo-title" title={t('algoTitle')} note={t('algoNote')} />
        {loading ? (
          <TableSkeleton cols={ALGO_COLUMNS.length} rows={3} testId="algo-orders-loading" />
        ) : algo.length === 0 && !instId ? (
          <div className="pf-panel">
            <StateBlock kind="none" compact icon={Shield} title={t('algoEmpty.title')} body={t('algoEmpty.body')} />
          </div>
        ) : (
          <div className="pf-panel">
            <table className="pf-table" data-testid="algo-orders-table" aria-labelledby="ord-algo-title">
              <thead>
                <tr>
                  {ALGO_COLUMNS.map((c) => (
                    <th key={c} scope="col" data-col={c} className={ALGO_NUMERIC.has(c) ? 'is-num' : undefined}>
                      {t(`col.${c}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {algo.length === 0 ? (
                  <tr data-testid="algo-orders-filtered-empty">
                    <td colSpan={ALGO_COLUMNS.length} className="px-5 py-4 text-footnote text-label-secondary">
                      {t('filteredEmptyAlgo', { inst: instId })}
                    </td>
                  </tr>
                ) : (
                  algo.map((a) => <AlgoRow key={a.id} a={a} lang={lang} />)
                )}
              </tbody>
            </table>
            {algo.length > 0 && (
              <p className="flex items-start gap-2 border-t-[0.5px] border-separator px-5 py-3 text-footnote text-label-secondary [&_svg]:mt-0.5 [&_svg]:size-3.5 [&_svg]:shrink-0">
                <Info aria-hidden="true" strokeWidth={2} />
                {t('readOnlyTip')}
              </p>
            )}
          </div>
        )}
      </section>
      {cancelling && <CancelOrderSheet order={cancelling} lang={lang} onClose={() => setCancelling(null)} />}
    </div>
  )
}
