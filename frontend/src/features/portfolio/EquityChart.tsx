import { useReducedMotion } from 'motion/react'
import { useId, useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import { formatMoney, type Lang } from '../../lib/format'
import type { CashflowMarker, EquityRange, EquitySeries } from './api'
import { formatDay, formatDayTime, formatClock, timeTicks, valueTicks } from './time'

/*
 * The Overview equity curve (design: Overview chart). A thin accent line over a soft
 * area gradient, hairline horizontal grid only, value axis on the trailing edge, a
 * frosted tooltip card, and deposit/withdrawal markers at the foot of the plot with a
 * dashed stem up to the curve. In Arabic the time axis runs right to left and the
 * value axis moves to the left; every figure keeps Western digits. Values are plotted
 * as numbers (display only); every label is formatted from the API's decimal string.
 */

interface Row {
  t: number
  v: number
  /** The API's exact decimal string, for labels. */
  equity: string
}

const AXIS_TICK = { fill: 'var(--chart-axis)', fontSize: 11.5, fontFamily: 'var(--font-stack-num)' }
const AXIS_FMT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

function nearestRow(rows: readonly Row[], t: number): Row | undefined {
  let best: Row | undefined
  for (const row of rows) if (!best || Math.abs(row.t - t) < Math.abs(best.t - t)) best = row
  return best
}

function markerLabel(m: CashflowMarker, lang: Lang, kindLabel: string): string {
  return `${kindLabel} · ${formatMoney(m.amount, m.ccy, lang)} · ${formatDay(m.ts, lang)}`
}

function FrostedTooltip({ active, payload, lang, range }: TooltipContentProps<number, string> & { lang: Lang; range: EquityRange }) {
  const row = payload?.[0]?.payload as Row | undefined
  if (!active || !row) return null
  const when = range === '1D' ? `${formatClock(row.t, lang, false)} · ${formatDay(row.t, lang)}` : formatDayTime(row.t, lang)
  return (
    <div
      dir={lang === 'ar' ? 'rtl' : 'ltr'}
      className="vibrancy-popover grid gap-px whitespace-nowrap rounded-[10px] px-2.5 py-[7px] shadow-[var(--shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
    >
      <span className="text-caption text-label-secondary">{when}</span>
      <span className="fig text-subheadline font-semibold text-label">{formatMoney(row.equity, 'USD', lang)}</span>
    </div>
  )
}

function MarkerShape({ cx, cy, kind, title }: { cx?: number; cy?: number; kind: CashflowMarker['kind']; title: string }) {
  if (cx === undefined || cy === undefined) return <g />
  // Lucide arrow-down / arrow-up, 11px.
  const arrow = kind === 'deposit' ? 'M12 5v14M19 12l-7 7-7-7' : 'M12 19V5M5 12l7-7 7 7'
  return (
    <g data-marker={kind}>
      <title>{title}</title>
      <circle
        cx={cx}
        cy={cy}
        r={9}
        fill="var(--bg)"
        stroke={kind === 'deposit' ? 'var(--accent)' : 'var(--label-secondary)'}
        strokeWidth={1.25}
      />
      <path
        d={arrow}
        transform={`translate(${cx - 5.5} ${cy - 5.5}) scale(${11 / 24})`}
        fill="none"
        stroke={kind === 'deposit' ? 'var(--accent-ink)' : 'var(--label)'}
        strokeWidth={2.25 * (24 / 11)}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  )
}

export interface EquityChartProps {
  series: EquitySeries
  lang: Lang
  /** The range control, drawn at the head's start with the marker legend at its end. */
  head?: ReactNode
}

export function EquityChart({ series, lang, head }: EquityChartProps) {
  const { t } = useTranslation('portfolio')
  const reduce = useReducedMotion()
  const gradientId = useId().replace(/:/g, '')
  const rtl = lang === 'ar'
  const { range } = series

  const rows = useMemo<Row[]>(
    () => series.points.map((p) => ({ t: Date.parse(p.ts), v: Number(p.equity_usd), equity: p.equity_usd })),
    [series.points],
  )
  const t0 = rows[0]?.t ?? 0
  const t1 = rows[rows.length - 1]?.t ?? 0
  const { lo, hi, ticks } = useMemo(() => {
    const values = rows.map((r) => r.v)
    return valueTicks(Math.min(...values), Math.max(...values))
  }, [rows])
  const xTicks = useMemo(() => timeTicks(t0, t1, range, lang), [t0, t1, range, lang])
  const tickLabel = useMemo(() => new Map(xTicks.map((x) => [x.ts, x.label])), [xTicks])
  const markers = useMemo(
    () => series.markers.map((m) => ({ m, t: Date.parse(m.ts) })).filter((x) => x.t >= t0 && x.t <= t1),
    [series.markers, t0, t1],
  )
  const last = rows[rows.length - 1]
  const kinds = (['deposit', 'withdrawal'] as const).filter((k) => markers.some((x) => x.m.kind === k))

  // Screen readers: about a dozen evenly spaced points, always ending with the latest.
  const sample = useMemo(() => {
    const every = Math.max(1, Math.ceil(rows.length / 12))
    const picked = rows.filter((_, i) => i % every === 0)
    if (last && picked[picked.length - 1] !== last) picked.push(last)
    return picked
  }, [rows, last])

  const chartLabel = t('chartLabel', {
    range: t(`range.${range}`),
    from: formatDay(t0, lang, true),
    to: formatDay(t1, lang, true),
  })

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between gap-4">
        {head}
        <div className="flex gap-3.5 text-footnote text-label-secondary" aria-hidden="true">
          {kinds.map((k) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <svg width="16" height="16" viewBox="0 0 18 18">
                <MarkerShape cx={9} cy={9} kind={k} title="" />
              </svg>
              {t(k)}
            </span>
          ))}
        </div>
      </div>
      <div
        dir="ltr"
        data-testid="equity-chart"
        data-points={rows.length}
        data-range={range}
        data-reversed={rtl ? 'true' : 'false'}
        role="img"
        aria-label={chartLabel}
        className="equity-chart relative h-[clamp(240px,36vh,340px)] rounded-card"
      >
        <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 800, height: 300 }}>
          <AreaChart data={rows} margin={{ top: 12, right: 8, bottom: 4, left: 8 }} accessibilityLayer>
            <defs>
              <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="var(--chart-area-top)" />
                <stop offset="100%" stopColor="var(--chart-area-bottom)" />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={[t0, t1]}
              ticks={xTicks.map((x) => x.ts)}
              tickFormatter={(v: number) => tickLabel.get(v) ?? ''}
              reversed={rtl}
              axisLine={false}
              tickLine={false}
              tick={{ ...AXIS_TICK, className: 'pf-x-tick' }}
              tickMargin={10}
              height={30}
            />
            <YAxis
              type="number"
              domain={[lo, hi]}
              ticks={ticks}
              orientation={rtl ? 'left' : 'right'}
              tickFormatter={(v: number) => AXIS_FMT.format(v)}
              axisLine={false}
              tickLine={false}
              tick={AXIS_TICK}
              width={76}
            />
            <Tooltip
              content={(props) => <FrostedTooltip {...(props as unknown as TooltipContentProps<number, string>)} lang={lang} range={range} />}
              cursor={{ stroke: 'var(--label-secondary)', strokeWidth: 1, strokeDasharray: '3 3' }}
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="v"
              stroke="var(--chart-line)"
              strokeWidth={1.75}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{ r: 4.5, fill: 'var(--bg)', stroke: 'var(--chart-line)', strokeWidth: 2.25 }}
              isAnimationActive={!reduce}
              animationDuration={700}
            />
            {markers.map(({ m, t: mt }) => {
              const at = nearestRow(rows, mt)
              return (
                <ReferenceLine
                  key={`stem-${m.id}`}
                  segment={[
                    { x: mt, y: lo },
                    { x: mt, y: at?.v ?? lo },
                  ]}
                  stroke="var(--label-tertiary)"
                  strokeDasharray="2 3"
                  ifOverflow="visible"
                />
              )
            })}
            {markers.map(({ m, t: mt }) => (
              <ReferenceDot
                key={`marker-${m.id}`}
                x={mt}
                y={lo}
                ifOverflow="visible"
                shape={(props: { cx?: number; cy?: number }) => (
                  <MarkerShape cx={props.cx} cy={props.cy} kind={m.kind} title={markerLabel(m, lang, t(m.kind))} />
                )}
              />
            ))}
            {last && <ReferenceDot x={last.t} y={last.v} r={4} fill="var(--chart-line)" stroke="var(--bg)" strokeWidth={2} />}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <table className="sr-only" data-testid="equity-table">
        <caption>{t('chartTable')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('chartTableTime')}</th>
            <th scope="col">{t('chartTableValue')}</th>
          </tr>
        </thead>
        <tbody>
          {sample.map((r) => (
            <tr key={r.t}>
              <th scope="row">{formatDayTime(r.t, lang)}</th>
              <td>{formatMoney(r.equity, 'USD', lang)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {markers.length > 0 && (
        <ul className="sr-only" aria-label={t('chartMarkers')}>
          {markers.map(({ m }) => (
            <li key={m.id}>{markerLabel(m, lang, t(m.kind))}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
