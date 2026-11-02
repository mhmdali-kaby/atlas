import { CircleCheck, Scale } from 'lucide-react'
import type { CSSProperties } from 'react'
import { Fig } from '../parts'
import { fmtBrier, fmtInt, fmtPct } from './format'
import { calVerdict, type Figure } from './model'
import { useSc } from './i18n'
import { T } from './shared'

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
