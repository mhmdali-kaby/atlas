import { ArrowDownRight, ArrowUpRight, Layers } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { FIGURE_LOCALE, NO_VALUE, type Lang } from '../../lib/format'
import { SignedValue } from '../../ui'
import { POLL_MS, usePositions, useSummary, type Position } from './api'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { formatDay, formatDayTime, formatDuration } from './time'
import './portfolio.css'

/*
 * Portfolio → Positions (brief §13, design: Positions). Every open position of the newest
 * synced positions tick, from /api/portfolio/positions (polled every 30 s). Stop, targets,
 * the opening agent/proposal and the Close action arrive with the Executor (Phase 2).
 */

const COLUMNS = ['instrument', 'side', 'size', 'leverage', 'mgnMode', 'entry', 'mark', 'liq', 'upl', 'time'] as const
type Column = (typeof COLUMNS)[number]
const NUMERIC = new Set<Column>(['size', 'leverage', 'entry', 'mark', 'liq', 'upl', 'time'])

/**
 * A decimal string grouped en-US in both languages, never rounded: at least `minFrac`
 * fraction digits (the design's 1 for prices ≥ 10,000, else 2), and every digit OKX sent.
 */
function formatDecimal(value: string | null, lang: Lang, minFrac: number): string {
  if (value === null || value.trim() === '') return NO_VALUE
  return new Intl.NumberFormat(FIGURE_LOCALE[lang], { minimumFractionDigits: minFrac, maximumFractionDigits: 20 })
    .format(value.trim() as Intl.StringNumericLiteral)
    .replace(/-/g, '−')
}

/** Display precision only (design priceDecimals); the value itself stays a string. */
function priceDecimals(value: string | null): number {
  return value !== null && Math.abs(Number(value)) >= 10_000 ? 1 : 2
}

function price(value: string | null, lang: Lang): string {
  return formatDecimal(value, lang, priceDecimals(value))
}

/** OKX instrument ID verbatim: the base asset carries the row, the rest recedes. */
function InstId({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span dir="ltr" className="pf-inst">
      {cut < 0 ? id : id.slice(0, cut)}
      {cut >= 0 && <span className="pf-inst-rest">{id.slice(cut)}</span>}
    </span>
  )
}

function SideBadge({ side }: { side: Position['side'] }) {
  const { t } = useTranslation('portfolio')
  const Icon = side === 'long' ? ArrowUpRight : ArrowDownRight
  return (
    <span className="pf-badge" data-side={side}>
      <Icon aria-hidden="true" strokeWidth={2} className="rtl:-scale-x-100" />
      {t(`positions.${side}`)}
    </span>
  )
}

function PositionRow({ p, lang, now }: { p: Position; lang: Lang; now: number }) {
  const { t } = useTranslation('portfolio')
  const mode = p.margin_mode === 'isolated' || p.margin_mode === 'cross' ? t(`positions.${p.margin_mode}`) : (p.margin_mode ?? NO_VALUE)
  return (
    <tr data-testid="position-row" data-inst={p.inst_id}>
      <td data-col="instrument">
        <InstId id={p.inst_id} />
      </td>
      <td data-col="side">
        <SideBadge side={p.side} />
      </td>
      <td data-col="size" className="is-num">
        <span className="fig">{formatDecimal(p.size, lang, 0)}</span>
        <span className="pf-cell-sub">{t('positions.contractsUnit')}</span>
      </td>
      <td data-col="leverage" className="is-num">
        <span className="fig">{p.leverage === null ? NO_VALUE : `${formatDecimal(p.leverage, lang, 0)}×`}</span>
      </td>
      <td data-col="mgnMode">{mode}</td>
      <td data-col="entry" className="is-num fig">
        {price(p.entry, lang)}
      </td>
      <td data-col="mark" className="is-num fig">
        {price(p.mark, lang)}
      </td>
      <td data-col="liq" className="is-num fig text-label-secondary">
        {price(p.liq_px, lang)}
      </td>
      <td data-col="upl" className="is-num">
        <span className="flex justify-end">
          <SignedValue value={p.upl_usd} unit="USD" />
        </span>
        <span className="pf-cell-sub flex justify-end [&_svg]:size-3">
          <SignedValue value={p.upl_pct} unit="percent" />
        </span>
      </td>
      <td data-col="time" className="is-num">
        {p.opened_at ? (
          <>
            <span className="fig" tabIndex={0} title={t('positions.opened', { date: formatDayTime(p.opened_at, lang) })}>
              {formatDuration(now - Date.parse(p.opened_at), lang)}
            </span>
            <span className="pf-cell-sub fig">{formatDay(p.opened_at, lang)}</span>
          </>
        ) : (
          <span className="text-label-secondary">{NO_VALUE}</span>
        )}
      </td>
    </tr>
  )
}

function TableSkeleton() {
  return (
    <div className="pf-panel" data-testid="positions-loading">
      <table className="pf-table pf-positions">
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
          {[0, 1, 2, 3].map((r) => (
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

export function PositionsPage() {
  const { t } = useTranslation('portfolio')
  const lang = useLang()
  const env = useTradingEnv()
  // Time in trade keeps counting between polls.
  const now = useNow(POLL_MS)
  const positions = usePositions(env)
  const summary = useSummary(env)

  const retry = () => {
    void positions.refetch()
    void summary.refetch()
  }

  if (positions.isError && !positions.data) {
    return <StateBlock kind="error" title={t('error.title')} body={t('error.body')} onRetry={retry} />
  }

  const data = positions.data?.data
  if (data && data.taken_at === null) {
    return <StateBlock kind="empty" title={t('empty.title')} body={t('empty.body')} />
  }

  const loading = positions.isPending
  const items = data?.items ?? []
  const s = summary.data?.data

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-6 pt-6 pb-4">
      <div className="grid gap-1">
        {loading ? (
          <Skeleton style={{ width: 200, height: 26 }} />
        ) : (
          items.length > 0 && (
            <h2 data-testid="positions-summary" className="text-title-2">
              {t('openPositions', { count: items.length })}
            </h2>
          )
        )}
        {!loading && items.length > 0 && s && (
          <p className="flex items-baseline gap-2 text-title-3 font-semibold [&_svg]:size-4">
            <span className="text-subheadline font-medium text-label-secondary">{t('positions.unrealisedTotal')}</span>
            <span data-testid="positions-upl-total">
              <SignedValue value={s.unrealised_pnl_usd} unit="USD" />
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
          <StateBlock kind="none" compact icon={Layers} title={t('positions.empty.title')} body={t('positions.empty.body')} />
        </div>
      </div>
    )
  }

  return (
    <div>
      {head}
      <div className="pf-panel">
        <table className="pf-table pf-positions" data-testid="positions-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c} scope="col" data-col={c} className={NUMERIC.has(c) ? 'is-num' : undefined}>
                  {t(`positions.col.${c}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <PositionRow key={p.id} p={p} lang={lang} now={now} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
