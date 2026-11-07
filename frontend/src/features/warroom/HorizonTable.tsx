import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { MIN_N, type Dist, type HistoryVerdictCard as Card } from './historian'
import { pctSigned, pctWhole } from './historianFormat'
import { Fig, type MarkFn } from './historianParts'
import { fillSlots, slot } from './slots'

/* The HistoryVerdict card's distribution table: one row group per horizon, with a range strip. */

// ------------------------------------------------------------------------------ range strip

const STRIP_W = 144
const STRIP_C = STRIP_W / 2

/** A small forest plot on its own symmetric scale around zero: these cases (top) over the base rate. */
function RangeStrip({ cases, base, weak, id }: { cases: Dist; base: Dist | null; weak: boolean; id: string }) {
  const vals = [cases, base].flatMap((q) => (q ? [q.p10, q.p90, q.median] : [])).map(Math.abs)
  const ext = Math.max(...vals, 1e-6) * 1.1
  const x = (v: number) => STRIP_C + (v / ext) * (STRIP_C - 4)
  const band = (q: Dist, y: number, cls: string, fill?: string) => (
    <>
      <rect className={cls} x={x(q.p10)} y={y - 3} width={Math.max(3, x(q.p90) - x(q.p10))} height={6} rx={3} style={fill ? { fill } : undefined} />
      <rect className={`${cls}-med`} x={x(q.median) - 1} y={y - 6} width={2} height={12} rx={1} />
    </>
  )
  const pid = `hatch-${id}`
  return (
    <svg className={weak ? 'hv-strip is-weak' : 'hv-strip'} width={STRIP_W} height={48} viewBox={`0 0 ${STRIP_W} 48`} aria-hidden="true" focusable="false">
      {weak && (
        <defs>
          <pattern id={pid} width={4} height={4} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect className="hatch" width={1.6} height={4} />
          </pattern>
        </defs>
      )}
      <line className="zero" x1={STRIP_C} x2={STRIP_C} y1={2} y2={46} />
      {base && band(base, 36, 'b-base')}
      {band(cases, 12, 'b-cases', weak ? `url(#${pid})` : undefined)}
    </svg>
  )
}

// ---------------------------------------------------------------------------- horizon table

/** Shows an inline-end fade while a horizontal scroller has more to the inline end (design review v7, 390 px). */
function useMoreAtEnd() {
  const ref = useRef<HTMLDivElement>(null)
  const [more, setMore] = useState(false)
  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    const room = el.scrollWidth - el.clientWidth
    setMore(room > 1 && Math.abs(el.scrollLeft) < room - 1)
  }, [])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure])
  return { ref, more, onScroll: measure }
}
export function HorizonTable({ card, lang, mark }: { card: Card; lang: Lang; mark: MarkFn }) {
  const { t } = useTranslation('warroom')
  const id = useId().replace(/:/g, '')
  const { ref: distRef, more, onScroll } = useMoreAtEnd()
  const weak = card.n < MIN_N
  const cell = (path: string, text: string) => mark(path, <Fig>{text}</Fig>)
  const cells = (q: Dist | null, root: string, h: string) =>
    q ? (
      <>
        <td className="hv-num">{cell(`${root}.${h}.median`, pctSigned(q.median, lang))}</td>
        <td className="hv-num hv-mid">
          {fillSlots(t('hv.range', { a: slot('a'), b: slot('b') }), {
            a: cell(`${root}.${h}.p10`, pctSigned(q.p10, lang)),
            b: cell(`${root}.${h}.p90`, pctSigned(q.p90, lang)),
          })}
        </td>
        <td className="hv-num">{cell(`${root}.${h}.pct_positive`, pctWhole(q.pct_positive, lang))}</td>
      </>
    ) : (
      <td colSpan={3} className="hv-none">
        {t('hv.noBase')}
      </td>
    )
  return (
    <div
      ref={distRef}
      onScroll={onScroll}
      className={more ? 'hv-dist has-more' : 'hv-dist'}
      data-testid="hv-dist"
      // A scroller with content past its edge must be reachable by keyboard (axe scrollable-region-focusable).
      tabIndex={more ? 0 : undefined}
      aria-label={more ? t('hv.more') : undefined}
      role={more ? 'region' : undefined}
    >
      <table className="hv-table">
        <caption className="sr-only">{t('hv.table')}</caption>
        <thead>
          <tr>
            <th scope="col" className="hv-h">
              {t('hv.col.horizon')}
            </th>
            <td />
            <th scope="col" className="hv-num">
              {t('hv.col.median')}
            </th>
            <th scope="col" className="hv-num">
              <span className="hv-tip" tabIndex={0} title={t('hv.col.mid80Tip')}>
                {t('hv.col.mid80')}
              </span>
            </th>
            <th scope="col" className="hv-num">
              <span className="hv-tip" tabIndex={0} title={t('hv.col.roseTip')}>
                {t('hv.col.rose')}
              </span>
            </th>
            <td className="hv-strip-cell" aria-hidden="true">
              <span className="hv-axis" dir="ltr">
                <span>−</span>
                <span>0</span>
                <span>+</span>
              </span>
            </td>
          </tr>
        </thead>
        {card.horizons.map((h, i) => {
          const label = h.hours !== null && Number.isInteger(h.hours) ? t('hv.h', { count: h.hours }) : null
          const tip = t('hv.stripTip', { a: pctSigned(h.cases.median, lang), b: h.base ? pctSigned(h.base.median, lang) : '—' })
          return (
            <tbody key={h.key} data-testid="hv-horizon" data-horizon={h.key}>
              <tr className="is-cases">
                <th scope="rowgroup" rowSpan={2} className="hv-h">
                  {label ? <span className="num">{label}</span> : <Fig>{h.key}</Fig>}
                </th>
                <th scope="row" className="hv-who">
                  {t('hv.row.cases')}
                </th>
                {cells(h.cases, 'distribution', h.key)}
                <td rowSpan={2} className="hv-strip-cell" title={tip}>
                  <RangeStrip cases={h.cases} base={h.base} weak={weak} id={`${id}-${i}`} />
                </td>
              </tr>
              <tr className="is-base">
                <th scope="row" className="hv-who">
                  <span className="hv-tip" tabIndex={0} title={t('hv.row.baseTip', { h: label ?? h.key })}>
                    {t('hv.row.base')}
                  </span>
                </th>
                {cells(h.base, 'base_rate', h.key)}
              </tr>
            </tbody>
          )
        })}
      </table>
    </div>
  )
}
