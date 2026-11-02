import { useReducedMotion } from 'motion/react'
import { useId, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Area,
  ComposedChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from 'recharts'
import { FIGURE_LOCALE, formatSigned, formatter, typographicMinus, type Lang } from '../../lib/format'
import { formatDate } from '../warroom/time'
import { NS } from './parts'
import { percentAxis, type InstrumentResult } from './result'

/*
 * The run's equity curve (design: backtester.js curve()). Starting equity is 0%; a thin accent
 * line over a soft area, a dashed zero line, and the out-of-sample block of each walk-forward
 * split shaded and labelled "Split n, tested". No grid. The tooltip is the frosted card. In
 * Arabic the time axis runs right to left and the value axis moves to the left; figures keep
 * Western digits. Values are plotted as numbers for display only.
 */

interface Row {
  t: number
  v: number
}

const AXIS_TICK = { fill: 'var(--chart-axis)', fontSize: 11.5, fontFamily: 'var(--font-stack-num)' }

function axisPct(v: number, digits: number, lang: Lang): string {
  const nf = formatter(FIGURE_LOCALE[lang], {
    style: 'unit',
    unit: 'percent',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  })
  return typographicMinus(nf.format(v))
}

interface XTickProps {
  x?: number | string
  y?: number | string
  index?: number
  payload?: { value: number }
}

/** A date under the plot. The first and last sit inside the plot's edges (design .bt-x span:first/last-child). */
function XTick({ x, y, index = 0, payload, last, rtl, lang }: XTickProps & { last: number; rtl: boolean; lang: Lang }) {
  if (payload === undefined) return <g />
  const atStart = index === 0
  const atEnd = index === last
  // The oldest date is at the left in English and at the right in Arabic.
  const anchor = atStart ? (rtl ? 'end' : 'start') : atEnd ? (rtl ? 'start' : 'end') : 'middle'
  return (
    <text x={Number(x)} y={Number(y)} dy="0.71em" textAnchor={anchor} className="bt-x-tick" {...AXIS_TICK}>
      {formatDate(payload.value, lang)}
    </text>
  )
}

function FrostedTip({ active, payload, lang }: TooltipContentProps<number, string> & { lang: Lang }) {
  const row = payload?.[0]?.payload as Row | undefined
  if (!active || !row) return null
  return (
    <div
      dir={lang === 'ar' ? 'rtl' : 'ltr'}
      data-testid="bt-tip"
      className="vibrancy-popover grid gap-px whitespace-nowrap rounded-[10px] px-2.5 py-[6px] shadow-[var(--shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
    >
      <span className="text-caption text-label-secondary">{formatDate(row.t, lang, true)}</span>
      <bdi dir="ltr" className="fig tabular-nums text-subheadline font-semibold text-label">
        {formatSigned(row.v.toFixed(2), 'percent', lang)}
      </bdi>
    </div>
  )
}

export function EquityCurve({ inst, lang }: { inst: InstrumentResult; lang: Lang }) {
  const { t } = useTranslation(NS)
  const reduce = useReducedMotion()
  const gradientId = `bt-g-${useId().replace(/:/g, '')}`
  const headId = `${gradientId}-h`
  const rtl = lang === 'ar'

  const rows = useMemo<Row[]>(
    () =>
      inst.equity_curve
        .map((p) => ({ t: Date.parse(p.ts), v: (Number(p.equity) - 1) * 100 }))
        .filter((r) => Number.isFinite(r.t) && Number.isFinite(r.v)),
    [inst.equity_curve],
  )
  const t0 = rows[0]?.t ?? 0
  const t1 = rows[rows.length - 1]?.t ?? 0
  const axis = useMemo(() => percentAxis(rows.map((r) => r.v)), [rows])
  const xTicks = useMemo(() => (t1 > t0 ? [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(t0 + f * (t1 - t0))) : [t0]), [t0, t1])
  const blocks = useMemo(
    () =>
      inst.folds
        .map((f) => ({ n: f.fold, a: Date.parse(f.out_of_sample.start ?? ''), b: Date.parse(f.out_of_sample.end ?? '') }))
        .filter((x) => Number.isFinite(x.a) && Number.isFinite(x.b))
        .map((x) => ({ ...x, a: Math.max(t0, x.a), b: Math.min(t1, x.b) }))
        .filter((x) => x.b > x.a),
    [inst.folds, t0, t1],
  )
  const end = rows[rows.length - 1]?.v ?? 0

  return (
    <section className="bt-panel bt-curve-p" aria-labelledby={headId} data-testid="bt-curve">
      <div className="bt-sec-head">
        <h3 className="text-headline" id={headId}>
          {t('r.curve')}
        </h3>
        <p className="bt-sec-sub">{t('r.curveSub')}</p>
      </div>
      {rows.length < 2 ? (
        <p className="bt-sec-sub bt-empty">{t('r.noCurve')}</p>
      ) : (
        <div
          dir="ltr"
          className="bt-chart"
          data-testid="bt-equity-chart"
          data-points={rows.length}
          data-reversed={rtl ? 'true' : 'false'}
          role="img"
          aria-label={t('r.curveAria', {
            from: formatDate(t0, lang, true),
            to: formatDate(t1, lang, true),
            end: formatSigned(end.toFixed(1), 'percent', lang),
          })}
        >
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 800, height: 260 }}>
            <ComposedChart data={rows} margin={{ top: 20, right: 8, bottom: 4, left: 8 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-area-top)" />
                  <stop offset="100%" stopColor="var(--chart-area-bottom)" />
                </linearGradient>
              </defs>
              {blocks.map((b) => (
                <ReferenceArea
                  key={b.n}
                  x1={b.a}
                  x2={b.b}
                  fill="var(--fill-4)"
                  fillOpacity={1}
                  stroke="none"
                  ifOverflow="hidden"
                  label={{
                    value: t('r.oosBlock', { n: b.n }),
                    position: rtl ? 'insideTopRight' : 'insideTopLeft',
                    offset: 4,
                    fill: 'var(--label-secondary)',
                    fontSize: 11.5,
                  }}
                />
              ))}
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={[t0, t1]}
                ticks={xTicks}
                interval={0}
                tickFormatter={(v: number) => formatDate(v, lang)}
                reversed={rtl}
                axisLine={false}
                tickLine={false}
                tick={(props: XTickProps) => <XTick {...props} last={xTicks.length - 1} rtl={rtl} lang={lang} />}
                tickMargin={8}
                height={26}
              />
              <YAxis
                type="number"
                domain={[axis.lo, axis.hi]}
                ticks={axis.ticks}
                allowDataOverflow
                orientation={rtl ? 'right' : 'left'}
                mirror={false}
                tickFormatter={(v: number) => axisPct(v, axis.digits, lang)}
                axisLine={false}
                tickLine={false}
                tick={{ ...AXIS_TICK, className: 'bt-y-tick' }}
                width={56}
              />
              <ReferenceLine y={0} stroke="var(--label-secondary)" strokeDasharray="3 3" strokeOpacity={0.6} />
              <Tooltip
                content={(props) => <FrostedTip {...(props as unknown as TooltipContentProps<number, string>)} lang={lang} />}
                cursor={{ stroke: 'var(--separator-strong)', strokeWidth: 1 }}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="v"
                stroke="var(--chart-line)"
                strokeWidth={1.75}
                fill={`url(#${gradientId})`}
                baseValue={axis.lo}
                dot={false}
                activeDot={{ r: 4, fill: 'var(--bg)', stroke: 'var(--chart-line)', strokeWidth: 2 }}
                isAnimationActive={!reduce}
                animationDuration={600}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}
