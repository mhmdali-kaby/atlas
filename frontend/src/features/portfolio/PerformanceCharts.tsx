import { useReducedMotion } from 'motion/react'
import { useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Bar, BarChart, Cell, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from 'recharts'
import { formatSigned, signOf, type Lang } from '../../lib/format'
import { SignedValue } from '../../ui'
import { cx } from '../../ui/cx'
import type { Bucket, HeatCell } from './performanceApi'
import { heatStep, iso } from './performanceFormat'

/*
 * Performance charts (design v14 #/portfolio/performance, css/history.css "pp-"):
 * diverging horizontal P&L bars (Recharts) labelled at their tips with sign and icon, and a
 * weekday × hour heatmap in Baghdad time on a loss↔profit scale through grey. Chart axes stay
 * left-to-right in Arabic; their names and the page around them mirror. Bar lengths and heat
 * steps use Number() for geometry only; every label is formatted from the API's decimal string.
 */

const pad2 = (h: number) => String(h).padStart(2, '0')

export interface BarItem extends Bucket {
  key: string
  name: string
  /** Draw the name as an OKX instrument ID (base asset bold, the rest secondary). */
  inst?: boolean
}

interface BarRow extends BarItem {
  v: number
}

const ROW_H = 30
/** Room past each bar's tip for its signed value label. */
const TIP_ROOM = 112

function BarShape(props: { x?: number; y?: number; width?: number; height?: number; fill?: string; payload?: BarRow }) {
  const { y = 0, height = 0, fill, payload } = props
  const x0 = Math.min(props.x ?? 0, (props.x ?? 0) + (props.width ?? 0))
  const w = Math.max(2, Math.abs(props.width ?? 0))
  const r = Math.min(4, w / 2, height / 2)
  const pos = (payload?.v ?? 0) >= 0
  // Rounded on the side away from zero only.
  const d = pos
    ? `M${x0},${y} H${x0 + w - r} Q${x0 + w},${y} ${x0 + w},${y + r} V${y + height - r} Q${x0 + w},${y + height} ${x0 + w - r},${y + height} H${x0} Z`
    : `M${x0 + w},${y} H${x0 + r} Q${x0},${y} ${x0},${y + r} V${y + height - r} Q${x0},${y + height} ${x0 + r},${y + height} H${x0 + w} Z`
  return <path d={d} fill={fill} />
}

function TipValue(props: { x?: number | string; y?: number | string; width?: number | string; height?: number | string; index?: number; rows: BarRow[] }) {
  const row = props.index === undefined ? undefined : props.rows[props.index]
  if (!row) return null
  const x = Number(props.x ?? 0)
  const w = Number(props.width ?? 0)
  const y = Number(props.y ?? 0)
  const h = Number(props.height ?? 0)
  const end = Math.max(x, x + w)
  const start = Math.min(x, x + w)
  const pos = row.v >= 0
  const box = TIP_ROOM - 6
  return (
    <foreignObject x={pos ? end + 6 : start - 6 - box} y={y + h / 2 - 10} width={box} height={20} className="overflow-visible">
      <div className={cx('pp-bar-val', pos ? 'justify-start' : 'justify-end')}>
        <SignedValue value={row.pnl_usd} unit="USD" />
      </div>
    </foreignObject>
  )
}

function NameTick(props: { x?: number | string; y?: number | string; payload?: { value: string }; rows: BarRow[]; rtl: boolean }) {
  const row = props.rows.find((r) => r.key === props.payload?.value)
  if (!row) return null
  const x = Number(props.x ?? 0)
  const y = Number(props.y ?? 0)
  const anchor = props.rtl ? 'start' : 'end'
  if (row.inst) {
    const cut = row.name.indexOf('-')
    return (
      <text x={x} y={y} dy="0.35em" textAnchor={anchor} direction="ltr" className="pp-bar-name">
        <tspan className="pp-bar-name-base">{cut < 0 ? row.name : row.name.slice(0, cut)}</tspan>
        {cut >= 0 && <tspan className="pp-bar-name-rest">{row.name.slice(cut)}</tspan>}
      </text>
    )
  }
  return (
    <text x={x} y={y} dy="0.35em" textAnchor={anchor} className="pp-bar-name">
      {row.name}
    </text>
  )
}

function BarTip({ active, payload, lang }: TooltipContentProps<number, string> & { lang: Lang }) {
  const { t } = useTranslation('portfolio-performance')
  const row = payload?.[0]?.payload as BarRow | undefined
  if (!active || !row) return null
  return (
    <FrostedTip
      lang={lang}
      sign={signOf(row.pnl_usd)}
      value={formatSigned(row.pnl_usd, 'USD', lang)}
      title={row.name}
      sub={t('tipTrades', { count: row.trades, w: iso(String(row.wins)) })}
    />
  )
}

function FrostedTip({ lang, sign, value, title, sub }: { lang: Lang; sign: number | null; value: string; title: string; sub?: string }) {
  return (
    <div
      dir={lang === 'ar' ? 'rtl' : 'ltr'}
      className="vibrancy-popover grid min-w-[120px] gap-px whitespace-nowrap rounded-[10px] px-2.5 py-[7px] shadow-[var(--shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
    >
      <span
        dir="ltr"
        className={cx(
          'fig justify-self-start text-subheadline font-semibold',
          sign === 1 ? 'text-profit-ink' : sign === -1 ? 'text-loss-ink' : 'text-label',
        )}
      >
        {value}
      </span>
      <span className="text-footnote text-label-secondary">{title}</span>
      {sub && <span className="text-caption text-label-secondary">{sub}</span>}
    </div>
  )
}

/** One "By instrument / asset class / agent" panel: bars diverging from zero, best first. */
export function PnlBars({ id, title, sub, items, lang }: { id: string; title: string; sub: string; items: BarItem[]; lang: Lang }) {
  const { t } = useTranslation('portfolio-performance')
  const reduced = useReducedMotion()
  const rtl = lang === 'ar'
  const rows: BarRow[] = items.map((x) => ({ ...x, v: Number(x.pnl_usd) }))
  const min = Math.min(0, ...rows.map((r) => r.v))
  const max = Math.max(0, ...rows.map((r) => r.v))
  const nameW = id === 'inst' ? 156 : 170
  const margin = { top: 2, bottom: 2, left: min < 0 ? TIP_ROOM : 4, right: max > 0 ? TIP_ROOM : 4 }

  return (
    <figure className={cx('pf-panel pp-chart', `pp-chart-${id}`)} data-testid={`pp-bars-${id}`}>
      <figcaption className="pp-chart-head">
        <h3 className="pp-chart-title">{title}</h3>
        <p className="pp-chart-sub">{sub}</p>
      </figcaption>
      {rows.length === 0 ? (
        <p className="pp-chart-empty">{t('noBars')}</p>
      ) : (
        <>
          <ul className="sr-only" aria-label={title}>
            {rows.map((r) => (
              <li key={r.key}>
                {t('rowLabel', {
                  name: r.name,
                  pnl: iso(formatSigned(r.pnl_usd, 'USD', lang)),
                  trades: t('ntrades', { count: r.trades }),
                  wins: iso(String(r.wins)),
                })}
              </li>
            ))}
          </ul>
          <div aria-hidden="true" className="pp-bars-plot" style={{ height: rows.length * ROW_H + 4 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                layout="vertical"
                data={rows}
                margin={margin}
                barCategoryGap={8}
                accessibilityLayer={false}
              >
                <XAxis type="number" dataKey="v" domain={[min, max]} hide />
                <YAxis
                  type="category"
                  dataKey="key"
                  orientation={rtl ? 'right' : 'left'}
                  width={nameW}
                  axisLine={false}
                  tickLine={false}
                  interval={0}
                  tick={<NameTick rows={rows} rtl={rtl} />}
                />
                <ReferenceLine x={0} stroke="var(--chart-grid)" />
                <Tooltip
                  cursor={{ fill: 'var(--fill-4)' }}
                  isAnimationActive={false}
                  content={(p) => <BarTip {...(p as TooltipContentProps<number, string>)} lang={lang} />}
                />
                <Bar dataKey="v" shape={<BarShape />} isAnimationActive={!reduced} maxBarSize={14}>
                  {rows.map((r) => (
                    <Cell key={r.key} fill={r.v >= 0 ? 'var(--profit)' : 'var(--loss)'} />
                  ))}
                  <LabelList dataKey="v" content={<TipValue rows={rows} />} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </figure>
  )
}

/* ------------------------------------------------------------------ heatmap */

/** ISO weekdays, Monday first; the weekend closes the week. */
const DAYS = [1, 2, 3, 4, 5, 6, 7] as const

export function Heatmap({ cells, lang }: { cells: HeatCell[]; lang: Lang }) {
  const { t } = useTranslation('portfolio-performance')
  const rtl = lang === 'ar'
  const byKey = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]))
  const maxAbs = Math.max(0, ...cells.map((c) => Math.abs(Number(c.pnl_usd))))
  const firstFilled = DAYS.flatMap((d) => Array.from({ length: 24 }, (_, h) => [d, h] as const)).find(([d, h]) => byKey.has(`${d}-${h}`))
  const [focus, setFocus] = useState<readonly [number, number]>(firstFilled ?? [1, 0])
  const [tip, setTip] = useState<{ d: number; h: number; x: number; y: number } | null>(null)
  const figRef = useRef<HTMLElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)

  const show = (d: number, h: number, el: HTMLElement) => {
    const fig = figRef.current
    if (!fig) return
    const fr = fig.getBoundingClientRect()
    const er = el.getBoundingClientRect()
    setTip({ d, h, x: er.left - fr.left + er.width / 2, y: er.top - fr.top })
  }

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    let [d, h] = focus
    // The hour axis runs left to right in both languages.
    if (e.key === 'ArrowRight') h = Math.min(23, h + 1)
    else if (e.key === 'ArrowLeft') h = Math.max(0, h - 1)
    else if (e.key === 'ArrowDown') d = Math.min(7, d + 1)
    else if (e.key === 'ArrowUp') d = Math.max(1, d - 1)
    else if (e.key === 'Home') h = 0
    else if (e.key === 'End') h = 23
    else return
    e.preventDefault()
    setFocus([d, h])
    const el = gridRef.current?.querySelector<HTMLElement>(`[data-cell="${d}-${h}"]`)
    if (el) {
      el.focus()
      show(d, h, el)
    }
  }

  const tipCell = tip ? byKey.get(`${tip.d}-${tip.h}`) : undefined
  const maxText = formatSigned(maxAbs, 'USD', lang)
  const minText = formatSigned(-maxAbs, 'USD', lang)

  return (
    <figure ref={figRef} className="pf-panel pp-heat" data-testid="pp-heat">
      <figcaption className="pp-chart-head">
        <h3 className="pp-chart-title">{t('heat')}</h3>
        <p className="pp-chart-sub">{t('heatSub')}</p>
      </figcaption>
      <div className="pp-heat-scroll">
        <div
          ref={gridRef}
          className="pp-heat-grid"
          role="grid"
          aria-label={t('heatLabel')}
          aria-rowcount={7}
          aria-colcount={25}
          onKeyDown={onKey}
        >
          {DAYS.map((d) => {
            const day = t(`d.${d}`)
            return (
              <div key={d} role="row" dir="ltr" className={cx('pp-heat-row', rtl && 'is-rtl')}>
                <span role="rowheader" dir={rtl ? 'rtl' : 'ltr'} className="pp-heat-day">
                  <span className="pp-day-long">{day}</span>
                  <span className="pp-day-short" aria-hidden="true">
                    {t(`ds.${d}`)}
                  </span>
                </span>
                {Array.from({ length: 24 }, (_, h) => {
                  const c = byKey.get(`${d}-${h}`)
                  const v = c ? Number(c.pnl_usd) : 0
                  const st = c ? heatStep(v, maxAbs) : -1
                  const cls = st < 0 ? 'is-empty' : st === 0 ? 'is-zero' : `${v > 0 ? 'is-pos' : 'is-neg'} s${st}`
                  const label = c
                    ? t('cell', {
                        day,
                        hour: iso(pad2(h)),
                        pnl: iso(formatSigned(c.pnl_usd, 'USD', lang)),
                        trades: t('ntrades', { count: c.trades }),
                      })
                    : t('cellNone', { day, hour: iso(pad2(h)) })
                  const isFocus = focus[0] === d && focus[1] === h
                  return (
                    <span
                      key={h}
                      role="gridcell"
                      data-cell={`${d}-${h}`}
                      data-testid="pp-cell"
                      tabIndex={isFocus ? 0 : -1}
                      aria-label={label}
                      className={cx('pp-cell', cls)}
                      onPointerEnter={(e) => show(d, h, e.currentTarget)}
                      onPointerLeave={(e) => {
                        if (document.activeElement !== e.currentTarget) setTip(null)
                      }}
                      onFocus={(e) => {
                        setFocus([d, h])
                        show(d, h, e.currentTarget)
                      }}
                      onBlur={() => setTip(null)}
                    />
                  )
                })}
              </div>
            )
          })}
          <div aria-hidden="true" dir="ltr" className={cx('pp-heat-row pp-heat-hours', rtl && 'is-rtl')}>
            <span className="pp-heat-day" />
            {Array.from({ length: 24 }, (_, h) => (
              <span key={h} className="pp-hour">
                {h % 3 === 0 ? pad2(h) : ''}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="pp-legend" aria-hidden="true">
        <span className="pp-legend-scale" dir="ltr">
          <span className="pp-legend-end" dir="auto">
            {t('loss')}
          </span>
          <span className="pp-ramp-wrap">
            <span className="pp-ramp">
              {[4, 3, 2, 1].map((s) => (
                <i key={`n${s}`} className={`pp-cell is-neg s${s}`} />
              ))}
              <i className="pp-cell is-zero" />
              {[1, 2, 3, 4].map((s) => (
                <i key={`p${s}`} className={`pp-cell is-pos s${s}`} />
              ))}
            </span>
            <span className="pp-ticks fig">
              <span>{minText}</span>
              <span>{formatSigned(0, 'USD', lang)}</span>
              <span>{maxText}</span>
            </span>
          </span>
          <span className="pp-legend-end" dir="auto">
            {t('profit')}
          </span>
        </span>
        <span className="pp-legend-none">
          <i className="pp-cell is-empty" />
          <span>{t('noTrades')}</span>
        </span>
      </div>
      {tip && (
        <div
          className="pp-tip"
          style={{ left: tip.x, top: tip.y }}
          data-testid="pp-heat-tip"
        >
          <FrostedTip
            lang={lang}
            sign={tipCell ? signOf(tipCell.pnl_usd) : null}
            value={tipCell ? formatSigned(tipCell.pnl_usd, 'USD', lang) : t('noTrades')}
            title={t('cellTip', { day: t(`d.${tip.d}`), hour: iso(pad2(tip.h)), next: iso(pad2((tip.h + 1) % 24)) })}
            sub={tipCell ? t('tipTrades', { count: tipCell.trades, w: iso(String(tipCell.wins)) }) : undefined}
          />
        </div>
      )}
    </figure>
  )
}

