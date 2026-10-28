import { Wallet } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatDecimal, formatMoney, formatPercent, NO_VALUE, type Lang } from '../../lib/format'
import { POLL_MS, useHoldings, useSummary, type Holding } from './api'
import { byValueDesc } from './ledger'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import './portfolio.css'

/*
 * Portfolio → Holdings (brief §13, design: Holdings). Every balance of the newest synced
 * trading and funding ticks, from /api/portfolio/holdings (polled every 30 s), largest value
 * first. Average cost and 24h change arrive with the Executor (decision #236).
 */

const COLUMNS = ['currency', 'account', 'quantity', 'value', 'share', 'avgCost', 'change'] as const
type Column = (typeof COLUMNS)[number]
const NUMERIC = new Set<Column>(['quantity', 'value', 'avgCost', 'change'])

function LaterPhase() {
  const { t } = useTranslation('portfolio')
  return (
    <span className="text-label-secondary" tabIndex={0} title={t('laterPhase')} aria-label={t('laterPhase')} data-testid="later-phase">
      {NO_VALUE}
    </span>
  )
}

function HoldingRow({ h, lang }: { h: Holding; lang: Lang }) {
  const { t } = useTranslation('portfolio')
  // Display only: the bar's width. The figure beside it is the API's own decimal.
  const pct = h.pct_of_portfolio === null ? 0 : Math.min(100, Math.max(1.5, Number(h.pct_of_portfolio)))
  return (
    <tr data-testid="holding-row" data-ccy={h.ccy} data-account={h.account}>
      <td data-col="currency">
        <span className="pf-ccy">
          <span className="pf-mono" aria-hidden="true" dir="ltr">
            {h.ccy.slice(0, 4)}
          </span>
          <span className="pf-inst" dir="ltr">
            {h.ccy}
          </span>
        </span>
      </td>
      <td data-col="account">
        <span className="pf-badge pf-badge-quiet">{t(`holdings.${h.account}`)}</span>
      </td>
      <td data-col="quantity" className="is-num fig">
        {formatDecimal(h.quantity, lang)}
      </td>
      <td data-col="value" className="is-num fig">
        {formatMoney(h.value_usd, 'USD', lang)}
      </td>
      <td data-col="share">
        {h.pct_of_portfolio === null ? (
          <span className="text-label-secondary">{NO_VALUE}</span>
        ) : (
          <span className="pf-share">
            <span className="pf-share-track" aria-hidden="true">
              <span className="pf-share-fill" style={{ width: `${pct}%` }} />
            </span>
            <span className="pf-share-pct fig" title={`${h.pct_of_portfolio}%`}>{formatPercent(h.pct_of_portfolio, lang)}</span>
          </span>
        )}
      </td>
      <td data-col="avgCost" className="is-num">
        <LaterPhase />
      </td>
      <td data-col="change" className="is-num">
        <LaterPhase />
      </td>
    </tr>
  )
}

function TableSkeleton() {
  return (
    <div className="pf-panel" data-testid="holdings-loading">
      <table className="pf-table">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th key={c}>
                <Skeleton style={{ width: 60, height: 12 }} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[0, 1, 2, 3, 4, 5].map((r) => (
            <tr key={r}>
              {COLUMNS.map((c, i) => (
                <td key={c}>
                  <Skeleton style={{ width: i ? 64 : 150, height: i ? 14 : 28 }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function HoldingsPage() {
  const { t } = useTranslation('portfolio')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(POLL_MS)
  const holdings = useHoldings(env)
  const summary = useSummary(env)

  const retry = () => {
    void holdings.refetch()
    void summary.refetch()
  }

  if (holdings.isError && !holdings.data) {
    return <StateBlock kind="error" title={t('error.title')} body={t('error.body')} onRetry={retry} />
  }

  const data = holdings.data?.data
  if (data && data.taken_at === null) {
    return <StateBlock kind="empty" title={t('empty.title')} body={t('empty.body')} />
  }

  const loading = holdings.isPending
  const items = [...(data?.items ?? [])].sort(byValueDesc)
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 260, height: 26 }} />
        ) : (
          items.length > 0 && (
            <h2 data-testid="holdings-summary" className="text-title-2">
              {t('holdings.summary', { count: items.length })}
            </h2>
          )
        )}
        {!loading && items.length > 0 && data && (
          <p className="flex items-baseline gap-2 text-title-3 font-semibold">
            <span className="text-subheadline font-medium text-label-secondary">{t('holdings.total')}</span>
            <span className="fig" data-testid="holdings-total">
              {formatMoney(data.total_value_usd, 'USD', lang)}
            </span>
          </p>
        )}
      </div>
      <div>{s ? <ReconciledIndicator summary={s} lang={lang} now={now} /> : <Skeleton style={{ width: 250, height: 26 }} />}</div>
    </header>
  )

  if (loading) {
    return (
      <div aria-busy="true">
        <span className="sr-only" role="status">
          {t('loading')}
        </span>
        {head}
        <TableSkeleton />
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div>
        {head}
        <div className="pf-panel">
          <StateBlock kind="none" compact icon={Wallet} title={t('holdings.empty.title')} body={t('holdings.empty.body')} />
        </div>
      </div>
    )
  }

  return (
    <div>
      {head}
      <div className="pf-panel">
        <table className="pf-table" data-testid="holdings-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c} scope="col" data-col={c} className={NUMERIC.has(c) ? 'is-num' : undefined}>
                  {t(`holdings.col.${c}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((h) => (
              <HoldingRow key={`${h.account}:${h.ccy}`} h={h} lang={lang} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
