import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, Check, ChevronLeft, ChevronRight, Clock, Lock, X } from 'lucide-react'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatDecimal, type Lang } from '../../lib/format'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import { CASHFLOWS_PER_PAGE, POLL_MS, useCashflows, useSummary, type CashflowMarker } from './api'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { flowState, shortTx } from './ledger'
import { formatDayTime } from './time'
import './portfolio.css'

/*
 * Portfolio → Cash flows (brief §13, design: Cash flows). Deposits and withdrawals that
 * trading_core reads from OKX (ATL-11), newest first, from /api/portfolio/cashflows. Read
 * only: the only controls are the pager's Previous and Next. The page lives in ?page=.
 */

const COLUMNS = ['date', 'kind', 'currency', 'amount', 'state', 'tx'] as const

const STATE_ICON = { completed: Check, pending: Clock, failed: X } as const
const STATE_CLASS = { completed: 'pf-badge-quiet', pending: 'pf-badge-warning', failed: 'pf-badge-loss' } as const

/** Deposits are signed "+", withdrawals "−", whatever sign the stored amount carries. */
function signedAmount(f: CashflowMarker, lang: Lang): string {
  const magnitude = f.amount.trim().replace(/^[-+]/, '')
  return formatDecimal(f.kind === 'withdrawal' ? `-${magnitude}` : magnitude, lang, 2, 'exceptZero')
}

function FlowRow({ f, lang }: { f: CashflowMarker; lang: Lang }) {
  const { t } = useTranslation('portfolio')
  const state = flowState(f.state)
  const StateIcon = STATE_ICON[state]
  const KindIcon = f.kind === 'deposit' ? ArrowDownLeft : ArrowUpRight
  return (
    <tr data-testid="cashflow-row" data-id={f.id} data-kind={f.kind} data-state={state}>
      <td data-col="date">
        <span title={f.ts}>{formatDayTime(f.ts, lang, true)}</span>
      </td>
      <td data-col="kind">
        <span className="pf-flow-type">
          <KindIcon aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
          {t(f.kind)}
        </span>
      </td>
      <td data-col="currency">
        <span dir="ltr" className="[unicode-bidi:isolate]">
          {f.ccy}
        </span>
      </td>
      <td data-col="amount" className="is-num fig">
        {signedAmount(f, lang)}
      </td>
      <td data-col="state">
        <span className={cx('pf-badge', STATE_CLASS[state])} title={t('cashflows.stateCode', { code: f.state })}>
          <StateIcon aria-hidden="true" strokeWidth={2} />
          {t(`cashflows.${state}`)}
        </span>
      </td>
      <td data-col="tx">
        {f.tx_id ? (
          <span className="pf-tx" dir="ltr" tabIndex={0} title={f.tx_id} aria-label={f.tx_id}>
            {shortTx(f.tx_id)}
          </span>
        ) : (
          <span className="text-label-secondary">{t('cashflows.noTx')}</span>
        )}
      </td>
    </tr>
  )
}

function TableSkeleton() {
  return (
    <div className="pf-panel" data-testid="cashflows-loading">
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
          {[0, 1, 2, 3, 4, 5, 6, 7].map((r) => (
            <tr key={r}>
              {COLUMNS.map((c, i) => (
                <td key={c}>
                  <Skeleton style={{ width: i ? 64 : 150, height: 14 }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function readPage(raw: string | null): number {
  const n = Number(raw)
  return Number.isInteger(n) && n >= 1 ? n : 1
}

export function CashflowsPage() {
  const { t } = useTranslation('portfolio')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(POLL_MS)
  const [params, setParams] = useSearchParams()
  const page = readPage(params.get('page'))
  const flows = useCashflows(env, page)
  const summary = useSummary(env)

  const goTo = (p: number) => setParams(p <= 1 ? {} : { page: String(p) })
  const meta = flows.data?.meta
  const lastPage = meta ? Math.max(1, meta.last_page) : 1

  // A stale link past the last page (history shrank, or a typed URL) lands on the last page.
  useEffect(() => {
    if (meta && !flows.isPlaceholderData && page > lastPage) setParams(lastPage <= 1 ? {} : { page: String(lastPage) }, { replace: true })
  }, [meta, flows.isPlaceholderData, page, lastPage, setParams])

  const retry = () => {
    void flows.refetch()
    void summary.refetch()
  }

  if (flows.isError && !flows.data) {
    return <StateBlock kind="error" title={t('error.title')} body={t('error.body')} onRetry={retry} />
  }

  const loading = flows.isPending
  const rows = flows.data?.data ?? []
  const total = meta?.total ?? 0
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 280, height: 26 }} />
        ) : (
          total > 0 && (
            <h2 data-testid="cashflows-summary" className="text-title-2">
              {t('cashflows.summary', { count: total })}
            </h2>
          )
        )}
        <p className="pf-head-note">
          <Lock aria-hidden="true" strokeWidth={1.5} />
          <span>{t('cashflows.readOnly')}</span>
        </p>
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

  if (total === 0) {
    return (
      <div>
        {head}
        <div className="pf-panel">
          <StateBlock
            kind="none"
            compact
            icon={ArrowLeftRight}
            title={t('cashflows.empty.title')}
            body={t('cashflows.empty.body')}
          />
        </div>
      </div>
    )
  }

  const from = meta?.from ?? (page - 1) * CASHFLOWS_PER_PAGE + 1
  const to = meta?.to ?? from + rows.length - 1
  // Each figure is a left-to-right isolate (U+2066…U+2069) so "21–40" never reads "40–21" in Arabic.
  const fig = (text: string) => `\u2066${text}\u2069`
  const count = (n: number) => formatDecimal(n, lang, 0)

  return (
    <div>
      {head}
      <div className="pf-panel" aria-busy={flows.isPlaceholderData || undefined}>
        <table className="pf-table" data-testid="cashflows-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c} scope="col" data-col={c} className={c === 'amount' ? 'is-num' : undefined}>
                  {t(`cashflows.col.${c}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <FlowRow key={f.id} f={f} lang={lang} />
            ))}
          </tbody>
        </table>
        <nav className="pf-pager" aria-label={t('cashflows.pagination')} data-testid="cashflows-pager">
          <span className="text-footnote text-label-secondary fig" data-testid="cashflows-range" aria-live="polite">
            {t('cashflows.page', { range: fig(`${count(from)}–${count(to)}`), total: fig(count(total)) })}
          </span>
          <div className="pf-pager-btns">
            <Button
              size="sm"
              variant="secondary"
              disabled={page <= 1}
              onClick={() => goTo(page - 1)}
              icon={<ChevronLeft aria-hidden="true" className="rtl:-scale-x-100" />}
            >
              {t('cashflows.prev')}
            </Button>
            <Button size="sm" variant="secondary" disabled={page >= lastPage} onClick={() => goTo(page + 1)}>
              <span className="inline-flex items-center gap-1.5">
                {t('cashflows.next')}
                <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
              </span>
            </Button>
          </div>
        </nav>
      </div>
    </div>
  )
}
