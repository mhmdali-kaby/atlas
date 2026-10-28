import { CircleCheck, CloudOff, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatMoney, type Lang } from '../../lib/format'
import { Button, Seal, SegmentedControl, SignedValue, Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import { RANGES, POLL_MS, useConnections, useEquity, usePositions, useSummary, type EquityRange, type PortfolioSummary } from './api'
import { EquityChart } from './EquityChart'
import { formatAgo, formatClock, formatDay } from './time'
import './portfolio.css'

/*
 * Portfolio → Overview (brief §13, design: Overview). The hero equity, the reconciled
 * seal and indicator, the equity curve with its range control, and the six-figure
 * ledger strip. Reads /api/portfolio/summary (polled every 30 s) and
 * /api/portfolio/equity?range=…; never calls OKX.
 */

function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** Re-renders every `ms` so "N minutes ago" keeps counting between polls. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return now
}

function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <span aria-hidden="true" className={cx('pf-skeleton', className)} style={style} />
}

/** "$5,136,121" + ".59" in the secondary label colour; tinted for a moment when it changes. */
function HeroEquity({ value, lang }: { value: string; lang: Lang }) {
  const text = formatMoney(value, 'USD', lang)
  const cut = text.lastIndexOf('.')
  const previous = useRef(value)
  const [tint, setTint] = useState<'profit' | 'loss' | null>(null)
  useEffect(() => {
    if (previous.current === value) return
    // Direction only, for the colour flash: not money maths.
    setTint(Number(value) >= Number(previous.current) ? 'profit' : 'loss')
    previous.current = value
    const id = window.setTimeout(() => setTint(null), 1200)
    return () => window.clearTimeout(id)
  }, [value])
  return (
    <p className="mt-0.5 mb-2.5 whitespace-nowrap text-hero max-[760px]:text-[2.25rem] max-[760px]:leading-[2.75rem]">
      <span
        data-testid="hero-equity"
        className={cx('fig', tint === 'profit' && 'pf-tint-profit', tint === 'loss' && 'pf-tint-loss')}
      >
        {cut < 0 ? (
          text
        ) : (
          <>
            {text.slice(0, cut)}
            <span className="text-label-secondary">{text.slice(cut)}</span>
          </>
        )}
      </span>
    </p>
  )
}

function ReconciledIndicator({ summary, lang, now }: { summary: PortfolioSummary; lang: Lang; now: number }) {
  const { t } = useTranslation('portfolio')
  const pill = 'inline-flex items-center gap-1.5 rounded-full py-1 ps-2 pe-2.5 text-footnote [&_svg]:size-[15px]'
  if (summary.stale) {
    const at = summary.reconciled_at
    return (
      <p
        data-testid="reconciled"
        data-state="stale"
        tabIndex={0}
        title={t('staleTip')}
        className={cx(pill, 'bg-warning-wash font-medium text-warning-ink')}
      >
        <TriangleAlert aria-hidden="true" strokeWidth={2} />
        <span>{at ? t('stale', { ago: formatAgo(now - Date.parse(at), lang) }) : t('staleNever')}</span>
        <span className="sr-only">. {t('staleTip')}</span>
      </p>
    )
  }
  return (
    <p
      data-testid="reconciled"
      data-state="ok"
      tabIndex={0}
      title={t('reconciledTip')}
      aria-live="polite"
      className={cx(pill, 'bg-fill-4 text-label-secondary')}
    >
      <CircleCheck aria-hidden="true" strokeWidth={2} className="text-profit-ink" />
      <span>
        {t('reconciled', { time: summary.reconciled_at ? formatClock(summary.reconciled_at, lang) : '—' })}
      </span>
    </p>
  )
}

function AccountSeal({ uidLast4, stale }: { uidLast4: string; stale: boolean }) {
  const { t } = useTranslation('portfolio')
  return (
    <figure className="m-0 me-3 grid justify-items-center gap-2 max-[760px]:hidden">
      <span className="relative grid place-items-center">
        <Seal seed={`okx-demo-${uidLast4}`} size={176} inkClassName={stale ? 'stroke-seal-muted' : undefined} />
        <span className="pointer-events-none absolute inset-0 grid place-items-center">
          {stale ? (
            <TriangleAlert aria-hidden="true" className="size-[19px] text-warning-ink" strokeWidth={2.4} />
          ) : (
            <CircleCheck aria-hidden="true" className="size-[19px] text-profit-ink" strokeWidth={2.4} />
          )}
        </span>
      </span>
      <figcaption className="flex justify-center gap-1.5 text-footnote text-label-secondary">
        <span>{t('sealCaption')}</span>
        <span className="fig">•••• {uidLast4}</span>
      </figcaption>
    </figure>
  )
}

function StateBlock({
  kind,
  title,
  body,
  compact,
  onRetry,
}: {
  kind: 'empty' | 'error'
  title: string
  body: string
  compact?: boolean
  onRetry?: () => void
}) {
  const { t } = useTranslation('portfolio')
  const parts = body.split('./atlas up')
  return (
    <div
      role={kind === 'error' ? 'alert' : 'status'}
      data-testid={`state-${kind}`}
      className={cx(
        'mx-auto grid max-w-[520px] justify-items-center gap-2.5 px-6 text-center',
        compact ? 'pt-14 pb-16' : 'pt-[12vh] pb-16',
      )}
    >
      {kind === 'empty' ? (
        <span className="mb-2">
          <Seal seed="okx-demo" size={compact ? 72 : 120} inkClassName="stroke-seal-muted" />
        </span>
      ) : (
        <span className="mb-1.5 grid size-14 place-items-center rounded-panel bg-loss-wash text-loss-ink">
          <CloudOff aria-hidden="true" className="size-7" strokeWidth={1.5} />
        </span>
      )}
      <h2 className="text-title-3">{title}</h2>
      <p className="max-w-[44ch] text-subheadline text-balance text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr" className="inline-block whitespace-nowrap rounded-[6px] bg-fill-3 px-1.5 text-label [unicode-bidi:isolate]">
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          body
        )}
      </p>
      {kind === 'empty' && (
        <p className="mt-1.5 inline-flex items-center gap-2 text-footnote text-label-secondary">
          <Spinner />
          <span>{t('waiting')}</span>
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" className="mt-2" icon={<RefreshCw aria-hidden="true" />} onClick={onRetry}>
          {t('error.retry')}
        </Button>
      )}
    </div>
  )
}

interface LedgerItem {
  key: string
  label: string
  value: ReactNode
  sub?: ReactNode
  to?: string
}

function Ledger({ items, loading }: { items: LedgerItem[]; loading?: boolean }) {
  const { t } = useTranslation('portfolio')
  return (
    <section aria-label={t('figures')} className="pf-ledger mt-7">
      {items.map((item) => {
        const body = (
          <>
            <span className="text-footnote font-medium text-label-secondary">{item.label}</span>
            {loading ? (
              <>
                <Skeleton style={{ width: '70%', height: 24, marginBlock: 4 }} />
                <Skeleton style={{ width: '50%', height: 14 }} />
              </>
            ) : (
              <>
                <span data-testid={`tile-${item.key}`} className="fig text-title-3 font-semibold [&_svg]:size-4">
                  {item.value}
                </span>
                {item.sub && (
                  <span className="flex flex-wrap items-center gap-1 text-footnote text-label-secondary">{item.sub}</span>
                )}
              </>
            )}
          </>
        )
        return item.to && !loading ? (
          <Link key={item.key} to={item.to} className="pf-ledger-item">
            {body}
          </Link>
        ) : (
          <div key={item.key} className="pf-ledger-item">
            {body}
          </div>
        )
      })}
    </section>
  )
}

const LEDGER_KEYS = ['net-deposits', 'total-pnl', 'today-pnl', 'unrealised', 'fees', 'funding'] as const

export function OverviewPage() {
  const { t } = useTranslation('portfolio')
  const lang = useLang()
  const env = useTradingEnv()
  const now = useNow(POLL_MS)
  const [range, setRange] = useState<EquityRange>('1M')

  const summary = useSummary(env)
  const equity = useEquity(env, range)
  const positions = usePositions(env)
  const connections = useConnections()

  const retry = () => {
    void summary.refetch()
    void equity.refetch()
    void positions.refetch()
  }

  if (summary.isError && !summary.data) {
    return <StateBlock kind="error" title={t('error.title')} body={t('error.body')} onRetry={retry} />
  }

  const s = summary.data?.data
  const loading = summary.isPending

  if (s && s.total_equity_usd === null) {
    return <StateBlock kind="empty" title={t('empty.title')} body={t('empty.body')} />
  }

  const connection = connections.data?.data.find((c) => c.exchange === 'okx' && c.env === env)
  const uidLast4 = connection?.uid_last4 ?? null
  const since = s?.pnl_since ? t('sinceDate', { date: formatDay(s.pnl_since, lang) }) : undefined
  const openCount = positions.data?.data.taken_at ? positions.data.data.items.length : null

  const rangeLabels: Record<EquityRange, string> = {
    '1D': t('range.1D'),
    '1W': t('range.1W'),
    '1M': t('range.1M'),
    '3M': t('range.3M'),
    YTD: t('range.YTD'),
    ALL: t('range.ALL'),
  }
  const rangeControl = (
    <SegmentedControl<EquityRange>
      aria-label={t('range.label')}
      options={RANGES.map((r) => ({ value: r, label: rangeLabels[r] }))}
      value={range}
      onValueChange={setRange}
    />
  )

  const ledger: LedgerItem[] = s
    ? [
        {
          key: 'net-deposits',
          label: t('netDeposits'),
          value: formatMoney(s.net_deposits_usd, 'USD', lang),
          sub: t('netDepositsSub'),
          to: '/portfolio/cashflows',
        },
        {
          key: 'total-pnl',
          label: t('totalPnl'),
          value: <SignedValue value={s.total_pnl_usd} unit="USD" />,
          sub: (
            <>
              <span data-testid="tile-total-pnl-pct" className="font-medium [&_svg]:size-3">
                <SignedValue value={s.total_pnl_pct} unit="percent" nullTip={t('pctNeedsBaseline')} />
              </span>
              {since && <span>{since}</span>}
            </>
          ),
        },
        { key: 'today-pnl', label: t('todayPnl'), value: <SignedValue value={s.today_pnl_usd} unit="USD" />, sub: t('sinceMidnight') },
        {
          key: 'unrealised',
          label: t('unrealised'),
          value: <SignedValue value={s.unrealised_pnl_usd} unit="USD" />,
          sub: openCount === null ? undefined : t('openPositions', { count: openCount }),
          to: '/portfolio/positions',
        },
        { key: 'fees', label: t('fees'), value: formatMoney(s.fees_paid_usd, 'USD', lang), sub: since },
        { key: 'funding', label: t('funding'), value: <SignedValue value={s.funding_net_usd} unit="USD" />, sub: since },
      ]
    : LEDGER_KEYS.map((key) => ({
        key,
        label: t(
          { 'net-deposits': 'netDeposits', 'total-pnl': 'totalPnl', 'today-pnl': 'todayPnl', unrealised: 'unrealised', fees: 'fees', funding: 'funding' }[
            key
          ],
        ),
        value: null,
      }))

  const series = equity.data?.data
  let chart: ReactNode
  if (equity.isPending || loading) {
    chart = (
      <>
        <div className="mb-2.5 flex items-center justify-between gap-4">{rangeControl}</div>
        <div data-testid="chart-loading" className="h-[clamp(240px,36vh,340px)] pt-3">
          <Skeleton className="size-full rounded-card" />
        </div>
      </>
    )
  } else if (equity.isError && !series) {
    chart = (
      <>
        <div className="mb-2.5 flex items-center justify-between gap-4">{rangeControl}</div>
        <StateBlock kind="error" compact title={t('error.title')} body={t('error.body')} onRetry={() => void equity.refetch()} />
      </>
    )
  } else if (!series || series.points.length === 0) {
    chart = (
      <>
        <div className="mb-2.5 flex items-center justify-between gap-4">{rangeControl}</div>
        <StateBlock kind="empty" compact title={t('empty.title')} body={t('empty.body')} />
      </>
    )
  } else {
    chart = <EquityChart series={series} lang={lang} head={rangeControl} />
  }

  return (
    <div aria-busy={loading || undefined}>
      {loading && <span className="sr-only" role="status">{t('loading')}</span>}
      <section
        aria-labelledby="ov-eq-label"
        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-8 pt-3 max-[760px]:grid-cols-1"
      >
        <div>
          <p id="ov-eq-label" className="mb-0.5 text-subheadline font-medium text-label-secondary">
            {t('equity')}
          </p>
          {s?.total_equity_usd ? (
            <HeroEquity value={s.total_equity_usd} lang={lang} />
          ) : (
            <Skeleton style={{ width: 340, maxWidth: '100%', height: 48, marginBlock: '4px 8px' }} />
          )}
          {s ? <ReconciledIndicator summary={s} lang={lang} now={now} /> : <Skeleton style={{ width: 260, height: 18 }} />}
        </div>
        {uidLast4 && !loading && <AccountSeal uidLast4={uidLast4} stale={s?.stale ?? false} />}
      </section>

      <section aria-label={t('equity')} className="mt-3">
        {chart}
        <p className="sr-only" aria-live="polite" data-testid="range-announcement">
          {t('range.label')}: {rangeLabels[range]}
        </p>
      </section>

      <Ledger items={ledger} loading={loading} />
    </div>
  )
}
