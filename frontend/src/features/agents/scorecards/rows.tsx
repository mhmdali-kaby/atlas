import { CircleCheck, TriangleAlert } from 'lucide-react'
import { Fig } from '../parts'
import { Calibration } from './Calibration'
import { figureText, fmtAtr, fmtInt, fmtPct, fmtR, valueAria } from './format'
import { STATED_R_TARGET, VERDICTS, verdictOf, type Figure, type MetricSpec, type Verdict } from './model'
import { useSc } from './i18n'
import { T } from './shared'

/* The figure, the rows and the "not enough data" state under a scorecard's header (design v16 §5). */

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

/** One metric row under the headline. */
export function MetricRow({ spec, f }: { spec: MetricSpec; f: Figure }) {
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

export function Headline({ spec, f }: { spec: MetricSpec; f: Figure }) {
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
