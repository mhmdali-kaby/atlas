import type { CSSProperties } from 'react'
import { formatDay } from '../../portfolio/time'
import type { ScoreDays } from './api'
import { valueAria } from './format'
import type { MetricSpec, TrendPoint } from './model'
import { useSc } from './i18n'
import { T } from './shared'

const dayLabel = (day: string, lang: 'en' | 'ar') => formatDay(`${day}T12:00:00Z`, lang)

/**
 * The headline's trend: the 7-day value per day, the target (or reference) dashed across, the
 * newest point dotted. The SVG mirrors in Arabic, so the newest day sits at the inline end.
 */
export function Trend({ spec, points, label, days }: { spec: MetricSpec; points: TrendPoint[]; label: string; days: ScoreDays }) {
  const { t, lang } = useSc()
  const vals = points.map((p) => p.value).filter((v): v is number => v !== null)
  if (spec.unit === 'mix' || spec.unit === 'count') return null
  if (vals.length === 0) {
    return (
      <div className="sc-trend is-none" data-testid={`sc-trend-${spec.id}`}>
        <span className="sc-trend-empty" aria-hidden="true" />
        <p className="sc-trend-note">{t('trendNone')}</p>
      </div>
    )
  }
  const tgt = spec.target !== undefined && spec.target !== 1 ? spec.target : spec.ref !== undefined && spec.unit !== 'pct' ? spec.ref : null
  let lo = Math.min(...vals, tgt ?? Infinity)
  let hi = Math.max(...vals, tgt ?? -Infinity)
  if (hi - lo < 1e-9) {
    lo -= 0.05
    hi += 0.05
  }
  const pad = (hi - lo) * 0.18
  lo -= pad
  hi += pad
  const W = 300
  const H = 56
  const n = points.length
  const X = (i: number) => (n === 1 ? W : (i / (n - 1)) * W)
  const Y = (v: number) => H - ((v - lo) / (hi - lo)) * H
  let path = ''
  let pen = false
  let first = -1
  let last = -1
  points.forEach((p, i) => {
    if (p.value === null) {
      pen = false
      return
    }
    if (first < 0) first = i
    last = i
    path += `${pen ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`
    pen = true
  })
  // A lone point draws as a short flat stroke so it is visible.
  if (first === last) path += `L${(X(last) + (last === n - 1 ? -4 : 4)).toFixed(1)},${Y(points[last]!.value!).toFixed(1)}`
  const area = `${path}L${X(last).toFixed(1)},${H}L${X(first).toFixed(1)},${H}Z`
  const endV = points[last]!.value!
  const aria = t('trendAria', {
    label,
    period: t(`d${days}`),
    from: valueAria(spec, points[first]!.value!, lang, t('secUnit')),
    to: valueAria(spec, endV, lang, t('secUnit')),
  })
  const tLabel =
    tgt === null ? null : spec.target !== undefined ? (
      <T k="trendTarget" values={{ v: valueAria(spec, tgt, lang, t('secUnit')) }} f={spec.unit === 'sec' ? <span /> : undefined} />
    ) : (
      t(`ref.${spec.id}`)
    )
  return (
    <div className="sc-trend" data-testid={`sc-trend-${spec.id}`}>
      <svg className="sc-trend-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={aria}>
        <path className="sc-trend-area" d={area} />
        {tgt !== null && (
          <line className="sc-trend-target" x1="0" x2={W} y1={Y(tgt).toFixed(1)} y2={Y(tgt).toFixed(1)} vectorEffect="non-scaling-stroke" />
        )}
        <path className="sc-trend-line" d={path} vectorEffect="non-scaling-stroke" />
      </svg>
      <i
        className="sc-trend-dot"
        data-testid="sc-trend-dot"
        style={{ '--x': ((X(last) / W) * 100).toFixed(1), '--y': ((Y(endV) / H) * 100).toFixed(1) } as CSSProperties}
        aria-hidden="true"
      />
      <p className="sc-trend-axis" aria-hidden="true">
        <span>{dayLabel(points[0]!.day, lang)}</span>
        {tLabel !== null && <span className="sc-trend-tgt">{tLabel}</span>}
        <span>{dayLabel(points[n - 1]!.day, lang)}</span>
      </p>
    </div>
  )
}
