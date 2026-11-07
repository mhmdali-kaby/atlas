import { Fragment, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatFixed, formatMoney, formatSigned, type Lang } from '../../lib/format'
import { SegmentedControl, SignedValue } from '../../ui'
import { cx } from '../../ui/cx'
import { POLL_MS, useSummary } from './api'
import { useLang, useNow } from './hooks'
import { ReconciledIndicator, Skeleton, StateBlock } from './parts'
import { Heatmap, PnlBars, type BarItem } from './PerformanceCharts'
import { iso } from './performanceFormat'
import { PERF_RANGES, usePerformance, type Metric, type Performance, type PerfRange } from './performanceApi'
import { formatDay } from './time'
import './portfolio.css'

/*
 * Portfolio → Performance (brief §13, design v14 #/portfolio/performance, ATL-175) over ATL-174's
 * GET /api/portfolio/performance. A range control (1M, 3M, YTD, All); seven tiles, each with its
 * sample size and "Not enough data" (plus the API's reason) when its value is null; P&L by
 * instrument, asset class and agent; and a weekday × hour heatmap in Baghdad time.
 */

const ISO_PART = /⁦([^⁩]*)⁩/g

/** A translated sentence whose figures arrive as LTR isolates: each figure becomes a `.fig` island. */
function Sentence({ text }: { text: string }) {
  const out: ReactNode[] = []
  let at = 0
  for (const m of text.matchAll(ISO_PART)) {
    out.push(text.slice(at, m.index))
    out.push(
      <bdi key={m.index} dir="ltr" className="fig text-label">
        {m[1]}
      </bdi>,
    )
    at = m.index + m[0].length
  }
  out.push(text.slice(at))
  return (
    <>
      {out.map((p, i) => (
        <Fragment key={i}>{p}</Fragment>
      ))}
    </>
  )
}

function Tile({
  id,
  label,
  value,
  sub,
  none,
  wide,
}: {
  id: string
  label: string
  value: ReactNode
  sub: string
  none?: boolean
  wide?: boolean
}) {
  return (
    <div className={cx('pp-tile', none && 'is-none', wide && 'is-wide')} data-testid={`pp-tile-${id}`} data-null={none || undefined}>
      <p className="pp-tile-label">{label}</p>
      <p className="pp-tile-value">{value}</p>
      <p className="pp-tile-sub" data-testid={`pp-tile-${id}-sub`}>
        <Sentence text={sub} />
      </p>
    </div>
  )
}

const n = (x: number) => iso(String(x))

const R_FMT = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero' })
/** "+0.31R", "−1.20R": the sign from Intl, the unit inside the figure. */
const formatR = (v: string) => `${R_FMT.format(v as Intl.StringNumericLiteral).replace(/-/g, '\u2212')}R`

function Tiles({ p, lang }: { p: Performance; lang: Lang }) {
  const { t } = useTranslation('portfolio-performance')
  const why = (m: { reason: string | null; sample_size: number }, dd = false) =>
    t(`why.${m.reason === 'insufficient_data' && dd ? 'insufficient_data_dd' : (m.reason ?? 'no_trades')}`, { n: n(m.sample_size) })
  const noneTile = (id: string, label: string, m: Metric) => (
    <Tile id={id} label={label} none value={<span className="pp-none">{t('nodata')}</span>} sub={why(m)} />
  )
  const big = (s: string) => (
    <bdi dir="ltr" className="fig pp-big">
      {s}
    </bdi>
  )

  const win = p.win_rate_pct
  const aw = p.avg_win_usd
  const al = p.avg_loss_usd
  const pf = p.profit_factor
  const ex = p.expectancy_r
  const dd = p.max_drawdown

  let avgTile: ReactNode
  if (aw.value === null && al.value === null) {
    avgTile = noneTile('avg', t('avg'), aw.reason ? aw : al)
  } else {
    // Display ratio only ("1.54×"); the money figures themselves stay decimal strings.
    const ratio = aw.value !== null && al.value !== null && Number(al.value) !== 0 ? Number(aw.value) / -Number(al.value) : null
    const counts = { w: n(aw.sample_size), l: n(al.sample_size) }
    avgTile = (
      <Tile
        id="avg"
        label={t('avg')}
        value={
          <span className="pp-pair" data-testid="pp-avg-pair">
            <span className="pp-pair-item">
              <SignedValue value={aw.value} unit="USD" nullTip={aw.reason ? why(aw) : undefined} />
            </span>
            <span className="pp-pair-item">
              <span className="pp-slash" aria-hidden="true">
                /
              </span>
              <SignedValue value={al.value} unit="USD" nullTip={al.reason ? why(al) : undefined} />
            </span>
          </span>
        }
        sub={ratio === null ? t('avgSubOne', counts) : t('avgSub', { ...counts, x: iso(`${formatFixed(ratio, 2, lang)}×`) })}
      />
    )
  }

  return (
    <section className="pf-panel pp-tiles" aria-label={t('tiles')} data-testid="pp-tiles">
      {win.value === null ? (
        noneTile('win', t('winRate'), win)
      ) : (
        <Tile
          id="win"
          label={t('winRate')}
          value={big(`${formatFixed(win.value, 0, lang)}%`)}
          sub={t('winRateSub', { w: n(p.trades.wins), n: n(win.sample_size) })}
        />
      )}
      {avgTile}
      {pf.value === null ? (
        noneTile('pf', t('pf'), pf)
      ) : (
        <Tile
          id="pf"
          label={t('pf')}
          value={big(formatFixed(pf.value, 2, lang))}
          sub={t('pfSub', {
            won: iso(formatMoney(p.trades.gross_profit_usd, 'USD', lang)),
            lost: iso(formatMoney(p.trades.gross_loss_usd.replace(/^-/, ''), 'USD', lang)),
            n: n(pf.sample_size),
          })}
        />
      )}
      {ex.value === null ? (
        noneTile('exp', t('exp'), ex)
      ) : (
        <Tile
          id="exp"
          label={t('exp')}
          value={big(formatR(ex.value))}
          sub={t('expSub', { n: n(ex.sample_size) })}
        />
      )}
      {dd.pct === null ? (
        <Tile id="dd" label={t('dd')} wide none value={<span className="pp-none">{t('nodata')}</span>} sub={why(dd, true)} />
      ) : (
        <Tile
          id="dd"
          label={t('dd')}
          wide
          value={<SignedValue value={dd.pct === '0.00' ? '0' : `-${dd.pct}`} unit="percent" />}
          sub={
            dd.usd !== null && dd.peak_at && dd.trough_at && Number(dd.usd) !== 0
              ? t('ddSub', {
                  usd: iso(formatSigned(`-${dd.usd}`, 'USD', lang)),
                  from: formatDay(dd.peak_at, lang),
                  to: formatDay(dd.trough_at, lang),
                  n: n(dd.sample_size),
                })
              : t('ddFlat', { n: n(dd.sample_size) })
          }
        />
      )}
      {p.sharpe.value === null ? (
        noneTile('sharpe', t('sharpe'), p.sharpe)
      ) : (
        <Tile id="sharpe" label={t('sharpe')} value={big(formatFixed(p.sharpe.value, 2, lang))} sub={t('sharpeSub', { n: n(p.sharpe.sample_size) })} />
      )}
      {p.sortino.value === null ? (
        noneTile('sortino', t('sortino'), p.sortino)
      ) : (
        <Tile
          id="sortino"
          label={t('sortino')}
          value={big(formatFixed(p.sortino.value, 2, lang))}
          sub={t('sortinoSub', { n: n(p.sortino.sample_size) })}
        />
      )}
    </section>
  )
}

function Charts({ p, lang }: { p: Performance; lang: Lang }) {
  const { t } = useTranslation(['portfolio-performance', 'portfolio-history', 'warroom'])
  const b = p.breakdowns
  const inst: BarItem[] = b.by_instrument.map((x) => ({ ...x, key: x.inst_id, name: x.inst_id, inst: true }))
  const cls: BarItem[] = b.by_asset_class.map((x) => ({
    ...x,
    key: x.asset_class,
    name: t(`portfolio-history:cls.${x.asset_class}`, { defaultValue: x.asset_class }),
  }))
  const agent: BarItem[] = b.by_agent.map((x) => ({
    ...x,
    key: x.agent ?? '__okx',
    name: x.agent === null ? t('okx') : t(`warroom:agents.${x.agent}.name`, { defaultValue: x.agent }),
  }))
  return (
    <section className="pp-charts">
      <PnlBars id="inst" title={t('byInst')} sub={t('chartSub')} items={inst} lang={lang} />
      <PnlBars id="cls" title={t('byCls')} sub={t('chartSub')} items={cls} lang={lang} />
      <PnlBars id="agent" title={t('byAgent')} sub={t('agentSub')} items={agent} lang={lang} />
    </section>
  )
}

function PageSkeleton() {
  return (
    <>
      <section className="pf-panel pp-tiles" aria-hidden="true">
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className={cx('pp-tile', i === 4 && 'is-wide')}>
            <Skeleton style={{ width: 90, height: 12 }} />
            <Skeleton style={{ width: 120, height: 24, marginTop: 8 }} />
            <Skeleton style={{ width: 150, height: 11, marginTop: 8 }} />
          </div>
        ))}
      </section>
      <section className="pp-charts" aria-hidden="true">
        <div className="pf-panel pp-chart pp-chart-inst">
          <Skeleton style={{ display: 'block', minHeight: 360, borderRadius: 12 }} />
        </div>
        <div className="pf-panel pp-chart pp-chart-cls">
          <Skeleton style={{ display: 'block', height: 160, borderRadius: 12 }} />
        </div>
        <div className="pf-panel pp-chart pp-chart-agent">
          <Skeleton style={{ display: 'block', height: 160, borderRadius: 12 }} />
        </div>
      </section>
    </>
  )
}

export function PerformancePage() {
  const { t } = useTranslation('portfolio-performance')
  const lang = useLang()
  const env = useTradingEnv()
  const reconNow = useNow(POLL_MS)
  const summary = useSummary(env)
  const [range, setRange] = useState<PerfRange>('ALL')
  const perf = usePerformance(env, range)
  const p = perf.data?.data
  const s = summary.data?.data
  const loading = perf.isPending

  const trades = p ? t('trades', { count: p.trades.total }) : ''
  const sum = p
    ? p.from
      ? t('sumFrom', { trades, from: formatDay(p.from, lang, true), to: formatDay(p.to, lang, true) })
      : t('sumAll', { trades, to: formatDay(p.to, lang, true) })
    : null

  const head = (
    <header className="flex flex-wrap items-end justify-between gap-4 pt-6 pb-0">
      <div className="grid gap-2">
        <SegmentedControl<PerfRange>
          aria-label={t('range')}
          value={range}
          onValueChange={setRange}
          options={PERF_RANGES.map((r) => ({ value: r, label: t(`r.${r}`) }))}
        />
        {sum ? (
          <p className="text-footnote text-label-secondary" data-testid="pp-summary">
            {sum}
            {p && p.trades.excluded_non_usd > 0 && <> {t('excluded', { count: p.trades.excluded_non_usd })}</>}
          </p>
        ) : (
          <Skeleton style={{ width: 260, height: 18 }} />
        )}
      </div>
      {s ? <ReconciledIndicator summary={s} lang={lang} now={reconNow} /> : <Skeleton style={{ width: 250, height: 26 }} />}
    </header>
  )

  let body: ReactNode
  if (perf.isError && !p) {
    body = (
      <div className="pf-panel">
        <StateBlock kind="error" compact title={t('error.title')} body={t('error.body')} onRetry={() => void perf.refetch()} />
      </div>
    )
  } else if (loading || !p) {
    body = <PageSkeleton />
  } else {
    body = (
      <>
        <Tiles p={p} lang={lang} />
        <Charts p={p} lang={lang} />
        <Heatmap cells={p.breakdowns.heatmap} lang={lang} />
      </>
    )
  }

  return (
    <div className="pp-page" aria-busy={loading || undefined} data-testid="performance-page" data-range={range}>
      {loading && (
        <span className="sr-only" role="status">
          {t('loading')}
        </span>
      )}
      {head}
      {body}
    </div>
  )
}

