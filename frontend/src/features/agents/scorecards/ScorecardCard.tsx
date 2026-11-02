import { CircleCheck, Scale, TriangleAlert } from 'lucide-react'
import type { CSSProperties, ReactElement } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { SignedValue } from '../../../ui'
import { useLang } from '../../portfolio/hooks'
import { formatDay } from '../../portfolio/time'
import { Medallion } from '../../warroom/Medallion'
import { Fig, Tip } from '../parts'
import type { AgentScores, ScoreDays } from './api'
import { weightDetail, figureText, fmtBrier, fmtInt, fmtPct, fmtR, fmtWeight, fmtAtr } from './format'
import {
  CARDS,
  METRICS,
  STATED_R_TARGET,
  VERDICTS,
  calVerdict,
  figure,
  trend,
  verdictOf,
  type Figure,
  type MetricSpec,
  type TrendPoint,
  type Verdict,
  type Weight,
} from './model'
import './scorecards.css'

/*
 * One agent's scorecard (design v16 §5 "The card"): its headline figure against its target with
 * the n it rests on, a 7-day trend that mirrors in Arabic, the rows under it, and the vote-weight
 * chip for voters. Under a metric's minimum, "Not enough data" and the n replace the figure.
 * Exported for Portfolio → Scorecards (ATL-204), which passes `pnl` to add the P&L line.
 */

export const SC_NS = 'scorecards'

function useSc() {
  const { t } = useTranslation(SC_NS)
  return { t, lang: useLang() }
}

/** A translated sentence whose <f> become LTR figure islands. */
function T({ k, values, f }: { k: string; values?: Record<string, unknown>; f?: ReactElement }) {
  const { t } = useTranslation(SC_NS)
  return <Trans t={t} i18nKey={k} values={values} components={{ f: f ?? <Fig />, cmd: <code dir="ltr" className="cmd" /> }} />
}

/** A metric's value. Seconds read in the sentence's direction: the number is the island, the unit follows. */
function Value({ spec, v }: { spec: MetricSpec; v: number }) {
  const { t, lang } = useSc()
  if (spec.unit === 'sec') {
    return (
      <>
        <Fig>{fmtInt(v, lang)}</Fig>
        {' '}
        {t('secUnit')}
      </>
    )
  }
  return <Fig>{figureText(spec, v, lang)}</Fig>
}

const valueAria = (spec: MetricSpec, v: number, lang: 'en' | 'ar', unit: string) =>
  spec.unit === 'sec' ? `${fmtInt(v, lang)} ${unit}` : figureText(spec, v, lang)

/** "45% or more", "under 90 s", "100%". */
function targetText(spec: MetricSpec, t: (k: string, o?: Record<string, unknown>) => string, lang: 'en' | 'ar'): string {
  if (spec.target === undefined) return ''
  const v = valueAria(spec, spec.target, lang, t('secUnit'))
  if (spec.target === 1 && spec.unit === 'pct') return v
  return spec.better === 'down' ? t('under', { v }) : t('orMore', { v })
}

const REF_WORD: Readonly<Record<string, string>> = {
  selection_edge: 'ref.shortlist',
  direction: 'ref.coin',
  supports: 'ref.coin',
  avgR: 'ref.breakeven',
}

/** Meets / misses the target, or better / worse than the reference: always an icon and words. */
function Pill({ spec, v }: { spec: MetricSpec; v: number | null }) {
  const { t, lang } = useSc()
  const verdict = verdictOf(spec, v)
  if (!verdict) return null
  if (verdict.kind === 'ref' && !REF_WORD[spec.id]) return null
  const Icon = verdict.ok ? CircleCheck : TriangleAlert
  return (
    <span className={verdict.ok ? 'sc-pill is-ok' : 'sc-pill is-warn'} data-testid={`sc-pill-${spec.id}`}>
      <Icon className="icon" aria-hidden="true" />
      <span>
        {verdict.kind === 'target' ? (
          <T
            k={verdict.ok ? 'meets' : 'misses'}
            values={{ target: targetText(spec, t, lang) }}
            f={spec.unit === 'sec' ? <span /> : undefined}
          />
        ) : (
          t(verdict.ok ? 'beats' : 'worse', { ref: t(REF_WORD[spec.id]!) })
        )}
      </span>
    </span>
  )
}

/** The sub-line under a figure, naming its n in words. */
function Sub({ spec, f }: { spec: MetricSpec; f: Figure }) {
  const { t, lang } = useSc()
  const n = fmtInt(f.n, lang)
  const k = f.k === null ? '' : fmtInt(f.k, lang)
  const id = spec.id
  if (spec.unit === 'count') return id === 'loops' ? <T k="m.loopsSub" /> : null
  if (id === 'selection_edge' && f.value !== null) {
    return (
      <>
        <T k={f.value < 0 ? 'm.selection_edgeSubLess' : 'm.selection_edgeSub'} values={{ v: fmtAtr(Math.abs(f.value), lang).replace('+', '') }} />{' '}
        <Fig className="sc-n">n = {n}</Fig>
      </>
    )
  }
  if (id === 'latency' || id === 'ack') {
    return (
      <T
        k={`m.${id}Sub`}
        values={{ med: f.companion === null || f.companion === undefined ? '—' : fmtInt(f.companion, lang), n }}
        f={<Fig />}
      />
    )
  }
  if (id === 'rr') {
    return (
      <T
        k="m.rrSub"
        values={{
          exp: f.companion === null || f.companion === undefined ? '—' : fmtR(f.companion, lang),
          target: t('orMore', { v: fmtR(STATED_R_TARGET, lang) }),
          n,
        }}
      />
    )
  }
  return <T k={`m.${id}Sub`} values={{ n, k }} />
}

/** "Not enough data" with the n and the minimum; its aria-label gives both. */
function NotEnough({ spec, f, label, head }: { spec: MetricSpec; f: Figure; label: string; head?: boolean }) {
  const { t, lang } = useSc()
  const vals = { n: fmtInt(f.n, lang), min: fmtInt(spec.min, lang) }
  const aria = t('ndAria', { label, ...vals })
  if (head) {
    return (
      <div className="sc-head-fig is-nd" aria-label={aria} role="group" data-testid={`sc-nd-${spec.id}`}>
        <p className="sc-label">{label}</p>
        <p className="sc-value is-nd">{t('nd')}</p>
        <p className="sc-sub">
          <Fig>{t('ndSub', vals)}</Fig>
        </p>
      </div>
    )
  }
  return (
    <div className="sc-row is-nd" aria-label={aria} role="group" data-testid={`sc-nd-${spec.id}`}>
      <div className="sc-row-main">
        <p className="sc-row-label">{label}</p>
        <p className="sc-row-sub">
          <Fig>{t('ndSub', vals)}</Fig>
        </p>
      </div>
      <p className="sc-row-value is-nd">{t('nd')}</p>
    </div>
  )
}

/**
 * The calibration pair: a ring where the agent said, a dot where it landed, on one 0–100% line
 * that stays left to right in Arabic. "Said" sits above the line and "happened" below, so the
 * labels never touch; each runs from its marker toward the middle, so none leaves the card.
 */
export function Calibration({ f, n }: { f: Figure; n: number }) {
  const { t, lang } = useSc()
  const brier = f.value
  const brierLine = (
    <p className="sc-cal-brier">
      {brier !== null && <T k="cal.brier" values={{ v: fmtBrier(brier, lang) }} />} <Fig className="sc-n">n = {fmtInt(n, lang)}</Fig>
    </p>
  )
  if (f.said === null || f.said === undefined || f.happened === null || f.happened === undefined) {
    return <div className="sc-cal">{brierLine}</div>
  }
  const said = f.said
  const hap = f.happened
  const cv = calVerdict(said, hap)
  const pts = t('cal.pts', { count: cv.points })
  const verdict = cv.kind === 'ok' ? t('cal.ok') : t(`cal.${cv.kind}`, { pts })
  const aria = t('cal.aria', { said: fmtPct(said, lang), happened: fmtPct(hap, lang), verdict, brier: brier === null ? '—' : fmtBrier(brier, lang) })
  const side = (v: number) => (v <= 0.5 ? 'is-start' : 'is-end')
  const x = (v: number) => (Math.max(0, Math.min(1, v)) * 100).toFixed(1)
  const lo = Math.min(said, hap)
  const hi = Math.max(said, hap)
  const Icon = cv.kind === 'ok' ? CircleCheck : Scale
  return (
    <div className={cv.wide ? 'sc-cal is-wide' : 'sc-cal'} data-testid="sc-cal">
      <p className="sc-cal-verdict">
        <Icon className="icon" aria-hidden="true" />
        <span>{verdict}</span>
      </p>
      <figure className="sc-cal-line" dir="ltr" role="img" aria-label={aria}>
        <span className="sc-cal-end" aria-hidden="true">
          <Fig>0%</Fig>
        </span>
        <span className="sc-cal-body" aria-hidden="true">
          <span className={`sc-cal-lab is-said ${side(said)}`} style={{ '--x': x(said) } as CSSProperties} dir="auto">
            <T k="cal.said" values={{ v: fmtPct(said, lang) }} />
          </span>
          <span className="sc-cal-track">
            <i className="sc-cal-mid" />
            <i className="sc-cal-gap" style={{ '--a': x(lo), '--b': x(hi) } as CSSProperties} />
            <i className="sc-cal-said" style={{ '--x': x(said) } as CSSProperties} />
            <i className="sc-cal-hap" style={{ '--x': x(hap) } as CSSProperties} />
          </span>
          <span className={`sc-cal-lab is-hap ${side(hap)}`} style={{ '--x': x(hap) } as CSSProperties} dir="auto">
            <T k="cal.happened" values={{ v: fmtPct(hap, lang) }} />
          </span>
        </span>
        <span className="sc-cal-end" aria-hidden="true">
          <Fig>100%</Fig>
        </span>
      </figure>
      {brierLine}
    </div>
  )
}

function MixBar({ mix }: { mix: Record<Verdict, number> }) {
  const { t, lang } = useSc()
  return (
    <div className="sc-mix">
      <div className="sc-mix-bar" aria-hidden="true">
        {VERDICTS.map((k) => (
          <i key={k} className={`sc-mix-seg is-${k}`} style={{ flexGrow: Math.max(0.0001, mix[k]) }} />
        ))}
      </div>
      <ul className="sc-mix-legend">
        {VERDICTS.map((k) => (
          <li key={k}>
            <i className={`sc-mix-key is-${k}`} aria-hidden="true" />
            <span>{t(`mix.${k}`)}</span> <Fig>{fmtPct(mix[k], lang)}</Fig>
          </li>
        ))}
      </ul>
    </div>
  )
}

const dayLabel = (day: string, lang: 'en' | 'ar') => formatDay(`${day}T12:00:00Z`, lang)

/**
 * The headline's trend: the 7-day value per day, the target (or reference) dashed across, the
 * newest point dotted. The SVG mirrors in Arabic, so the newest day sits at the inline end.
 */
function Trend({ spec, points, label, days }: { spec: MetricSpec; points: TrendPoint[]; label: string; days: ScoreDays }) {
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

/** One metric row under the headline. */
function MetricRow({ spec, f }: { spec: MetricSpec; f: Figure }) {
  const { t } = useSc()
  const label = t(`m.${spec.id}`)
  if (spec.unit === 'cal') {
    if (!f.enough) return <NotEnough spec={spec} f={f} label={label} />
    return (
      <div className="sc-row is-cal" data-testid={`sc-row-${spec.id}`}>
        <p className="sc-row-label">{label}</p>
        <Calibration f={f} n={f.n} />
      </div>
    )
  }
  if (spec.unit === 'mix') {
    if (!f.enough || !f.mix) return <NotEnough spec={spec} f={f} label={label} />
    return (
      <div className="sc-row is-mix" data-testid={`sc-row-${spec.id}`}>
        <p className="sc-row-label">{label}</p>
        <MixBar mix={f.mix} />
        <p className="sc-row-sub">
          <Sub spec={spec} f={f} />
        </p>
      </div>
    )
  }
  if (!f.enough || f.value === null) return <NotEnough spec={spec} f={f} label={label} />
  const sub = <Sub spec={spec} f={f} />
  return (
    <div className="sc-row" data-testid={`sc-row-${spec.id}`}>
      <div className="sc-row-main">
        <p className="sc-row-label">{label}</p>
        {spec.unit !== 'count' || spec.id === 'loops' ? <p className="sc-row-sub">{sub}</p> : null}
        <Pill spec={spec} v={f.value} />
      </div>
      <p className="sc-row-value">
        <Value spec={spec} v={f.value} />
      </p>
    </div>
  )
}

function Headline({ spec, f }: { spec: MetricSpec; f: Figure }) {
  const { t } = useSc()
  const label = t(`m.${spec.id}`)
  if (!f.enough || f.value === null) return <NotEnough spec={spec} f={f} label={label} head />
  if (spec.unit === 'cal') {
    return (
      <div className="sc-head-fig is-cal" data-testid={`sc-head-${spec.id}`}>
        <p className="sc-label">{label}</p>
        <Calibration f={f} n={f.n} />
        <p className="sc-sub">
          <Sub spec={spec} f={f} />
        </p>
      </div>
    )
  }
  return (
    <div className="sc-head-fig" data-testid={`sc-head-${spec.id}`}>
      <p className="sc-label">{label}</p>
      <p className="sc-value-line">
        <span className="sc-value">
          <Value spec={spec} v={f.value} />
        </span>
        <Pill spec={spec} v={f.value} />
      </p>
      <p className="sc-sub">
        <Sub spec={spec} f={f} />
      </p>
    </div>
  )
}

/** The `⚖ Vote 1.12×` chip. A default 1.00× (under 10 scored votes) is outlined and grey. */
export function WeightChip({ w }: { w: Weight }) {
  const { t, lang } = useSc()
  const detail = weightDetail(w, t, lang)
  const val = fmtWeight(w.weight, lang)
  return (
    <Tip label={t('voteTip', { w: val, detail })}>
      <span className={w.few ? 'sc-weight is-default' : 'sc-weight'} tabIndex={0} data-testid="sc-weight">
        <Scale className="icon" aria-hidden="true" />
        <span>
          <T k="vote" values={{ w: val }} />
        </span>
      </span>
    </Tip>
  )
}


export interface ScorecardCardProps {
  slug: string
  /** The agent's entry from GET /api/agents/scores; missing = no rows. */
  scores: AgentScores | undefined
  days: ScoreDays
  /** The period's Baghdad days (meta.from / meta.to), for the trend's axis. */
  from: string
  to: string
  /** Voters only: the 30-day reliability weight. */
  weight?: Weight | null
  /** Portfolio → Scorecards: show "P&L of trades it led" when the API sends it. */
  pnl?: boolean
}

/** One agent's scorecard. Needs a radix Tooltip.Provider above it (for the weight chip). */
export function ScorecardCard({ slug, scores, days, from, to, weight, pnl }: ScorecardCardProps) {
  const { t } = useTranslation([SC_NS, 'warroom'])
  const spec = CARDS.find((c) => c.slug === slug)
  if (!spec) return null
  const rows = scores?.days ?? []
  const hs = METRICS[spec.head]!
  const hf = figure(hs, rows)
  const points = trend(hs, rows, from, to)
  const pnlUsd = pnl ? scores?.pnl_attributed_usd : null
  return (
    <article className="sc-card" id={`sc-${slug}`} tabIndex={-1} aria-labelledby={`sc-name-${slug}`} data-testid="sc-card" data-slug={slug}>
      <header className="sc-card-head">
        <Medallion slug={slug} size={36} status="idle" />
        <div className="sc-who">
          <h3 className="sc-name" id={`sc-name-${slug}`}>
            {t(`warroom:agents.${slug}.name`)}
          </h3>
          <p className="sc-role">{t(`warroom:agents.${slug}.role`)}</p>
        </div>
        {weight ? <WeightChip w={weight} /> : <span />}
      </header>
      {pnlUsd !== null && pnlUsd !== undefined && (
        <div className="sc-pnl" data-testid="sc-pnl">
          <span className="sc-pnl-label">{t('pnl')}</span>
          <SignedValue value={pnlUsd} unit="USD" />
        </div>
      )}
      <Headline spec={hs} f={hf} />
      {hf.enough ? (
        <Trend spec={hs} points={points} label={t(`m.${hs.id}`)} days={days} />
      ) : hs.unit !== 'mix' && hs.unit !== 'count' ? (
        <Trend spec={hs} points={[]} label={t(`m.${hs.id}`)} days={days} />
      ) : null}
      {spec.rows.length > 0 && (
        <div className="sc-rows">
          {spec.rows.map((id) => {
            const ms = METRICS[id]!
            return <MetricRow key={id} spec={ms} f={figure(ms, rows)} />
          })}
        </div>
      )}
    </article>
  )
}

