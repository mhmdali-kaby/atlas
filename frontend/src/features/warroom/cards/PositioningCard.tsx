import { ArrowDownRight, ArrowUpRight, CircleCheck, CircleDot, CircleMinus, CircleX, Minus, TriangleAlert, Users, type LucideIcon } from 'lucide-react'
import { useId, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { FIGURE_LOCALE, formatFixed, type Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import { Inline } from '../text'
import { formatFullTime, ownerCity, formatTime, formatUtcFull } from '../time'
import type { Evidence, PositioningReport, Regime, Stance } from './positioning'
import { AsOf, InstrumentButton } from './TechnicalSetupCard'
import './technicalSetup.css'
import './positioning.css'

/*
 * The Derivatives & Sentiment Analyst's PositioningReport card (brief §7.5, design v11 Phase 2 §7):
 * the OI-price quadrant with today's regime filled and its one-line meaning, the stance for a long
 * and a short (icon and words, so colour is never the only signal), crowding and squeeze meters,
 * funding, basis, the underlying premium for stock and commodity perps, signed sentiment, then the
 * key evidence with each line's as_of. Unreliable data replaces the stances and scores with a notice.
 */

/** The four quadrants, laid out price-down | price-up over OI-up / OI-down (design QUAD). */
const QUAD: ReadonlyArray<{ k: Exclude<Regime, 'flat'>; price: 'pu' | 'pd'; oi: 'ou' | 'od' }> = [
  { k: 'new_shorts', price: 'pd', oi: 'ou' },
  { k: 'new_longs', price: 'pu', oi: 'ou' },
  { k: 'long_liquidation', price: 'pd', oi: 'od' },
  { k: 'short_covering', price: 'pu', oi: 'od' },
]
const ST_ICON: Record<Stance, LucideIcon> = { supports: CircleCheck, neutral: CircleMinus, opposes: CircleX }

function Fig({ children, testId }: { children: string; testId?: string }) {
  return (
    <bdi dir="ltr" className="fig num" data-testid={testId}>
      {children}
    </bdi>
  )
}

/** A signed figure at fixed precision: "+4.3", "−0.12", "0.0". The sign comes from Intl, never a template. */
function signedFixed(v: string | number, digits: number, lang: Lang): string {
  const nf = new Intl.NumberFormat(FIGURE_LOCALE[lang], { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: 'exceptZero' })
  return nf.format(typeof v === 'number' ? v : (v as Intl.StringNumericLiteral)).replace(/-/g, '−')
}

const pct0 = (v: number, lang: Lang) => `${formatFixed(Math.round(v * 100), 0, lang)}%`

function Meter({ v }: { v: number }) {
  return (
    <span className="an-meter" aria-hidden="true">
      <i style={{ '--v': Math.max(0, Math.min(1, v)).toFixed(3) } as CSSProperties} />
    </span>
  )
}

function Quadrant({ report, reliable }: { report: PositioningReport; reliable: boolean }) {
  const { t } = useTranslation('warroom')
  const label = reliable ? t('pos.quadAria', { regime: t(`pos.regime.${report.regime}`) }) : t('pos.unrelTitle')
  return (
    <ul className="an-quad" aria-label={label} data-testid="pos-quad">
      {QUAD.map((q) => {
        const on = reliable && q.k === report.regime
        return (
          <li key={q.k} className={`an-qc${on ? ' is-on' : ''}`} aria-current={on ? 'true' : undefined} data-regime={q.k}>
            <span className="an-qc-name">
              {on && <CircleDot className="icon" aria-hidden="true" />}
              <span>{t(`pos.regime.${q.k}`)}</span>
            </span>
            <span className="an-qc-sub">
              {t(`pos.q.${q.price}`)}
              <br />
              {t(`pos.q.${q.oi}`)}
            </span>
            {on && <span className="sr-only">{t('pos.q.now')}</span>}
          </li>
        )
      })}
    </ul>
  )
}

function StancePill({ side, stance }: { side: 'long' | 'short'; stance: Stance }) {
  const { t } = useTranslation('warroom')
  const Icon = ST_ICON[stance]
  return (
    <span className={`an-st is-${stance}`} data-testid={`pos-stance-${side}`} data-stance={stance}>
      <Icon className="icon" aria-hidden="true" data-testid={`pos-stance-${side}-icon`} />
      <span>
        <span className="sr-only">{`${t('pos.stance')}: `}</span>
        {t(`pos.st.${side}.${stance}`)}
      </span>
    </span>
  )
}

/** Baghdad time of an evidence line, both full times in the tooltip. */
function Stamp({ ts, lang }: { ts: string; lang: Lang }) {
  const { t } = useTranslation('warroom')
  return (
    <time className="an-stamp num" dateTime={ts} tabIndex={0} title={t('ta.dual', { city: ownerCity(lang), local: formatFullTime(ts, lang), utc: formatUtcFull(ts) })} data-testid="pos-ev-asof">
      {formatTime(ts, lang)}
    </time>
  )
}

function EvidenceList({ items, lang }: { items: Evidence[]; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const id = useId()
  if (items.length === 0) return null
  return (
    <div className="an-sec">
      <p className="an-sec-h" id={id}>
        {t('pos.evidence')}
      </p>
      <ul className="an-ev" aria-labelledby={id} data-testid="pos-evidence">
        {items.map((e, i) => (
          <li key={i} data-testid="pos-ev">
            <span dir="auto">
              <Inline text={e.text} flat />
            </span>
            {e.asOf && <Stamp ts={e.asOf} lang={lang} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Sentiment({ v, lang }: { v: number; lang: Lang }) {
  const sign = v > 0 ? 'is-pos' : v < 0 ? 'is-neg' : 'is-zero'
  const Icon = v > 0 ? ArrowUpRight : v < 0 ? ArrowDownRight : Minus
  return (
    <span className={`an-sig ${sign}`} data-testid="pos-senti">
      <Icon className="icon" aria-hidden="true" />
      <Fig>{signedFixed(v, 2, lang)}</Fig>
    </span>
  )
}

export function PositioningCard({ report, lang }: { report: PositioningReport; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const reliable = report.reliable
  const meaning = report.regimeMeaning ?? t(`pos.meaning.${report.regime}`)
  const reasons = report.unreliableReasons.length > 0 ? report.unreliableReasons.join(' ') : t('pos.unrelDefault')

  return (
    <div className="hv-card an-card an-pos" role="group" aria-label={t('pos.kind')} data-testid="pos-card" data-reliable={reliable ? 'true' : 'false'}>
      <div className="hv-head">
        <span className="hv-tile">
          <Users aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('pos.kind')}</span>
        <AsOf ts={report.dataAsOf} lang={lang} testId="pos-asof" />
      </div>
      <p className="an-title">
        <InstrumentButton instId={report.instId} testId="pos-inst" />
      </p>

      {reliable ? (
        <div className="an-stances" data-testid="pos-stances">
          <StancePill side="long" stance={report.supports.long} />
          <StancePill side="short" stance={report.supports.short} />
        </div>
      ) : (
        <div className="hv-warn an-unrel" role="note" data-testid="pos-unreliable">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>
            <b>{t('pos.unrelTitle')}.</b>{' '}
            <span dir="auto">
              <Inline text={reasons} flat />
            </span>
          </span>
        </div>
      )}

      <div className="an-regime">
        <Quadrant report={report} reliable={reliable} />
        {reliable && (
          <div className="an-regime-text">
            <p className="an-regime-name" data-testid="pos-regime">
              {t(`pos.regime.${report.regime}`)}
            </p>
            <p className="an-regime-mean" dir="auto" data-testid="pos-meaning">
              <Inline text={meaning} flat />
            </p>
          </div>
        )}
      </div>

      <dl className="an-dl">
        {reliable && (
          <>
            <dt>{t('pos.crowd')}</dt>
            <dd className="is-row" data-testid="pos-crowding">
              <Meter v={report.crowding} />
              <span>{fillSlots(t(`pos.crowdV.${report.crowdedSide}`, { pct: slot('pct') }), { pct: <Fig>{pct0(report.crowding, lang)}</Fig> })}</span>
            </dd>
            <dt>{t('pos.squeeze')}</dt>
            <dd className="an-sq" data-testid="pos-squeeze">
              <span className="an-sq-row">
                <span className="an-sq-k">{t('pos.sqLong')}</span>
                <Meter v={report.longSqueeze} />
                <Fig testId="pos-sq-long">{pct0(report.longSqueeze, lang)}</Fig>
              </span>
              <span className="an-sq-row">
                <span className="an-sq-k">{t('pos.sqShort')}</span>
                <Meter v={report.shortSqueeze} />
                <Fig testId="pos-sq-short">{pct0(report.shortSqueeze, lang)}</Fig>
              </span>
            </dd>
          </>
        )}
        {report.fundingView && (
          <>
            <dt>{t('pos.funding')}</dt>
            <dd dir="auto" data-testid="pos-funding">
              <Inline text={report.fundingView} flat />
            </dd>
          </>
        )}
        {report.basisBps !== null && (
          <>
            <dt>{t('pos.basis')}</dt>
            <dd data-testid="pos-basis">
              <Fig>{`${signedFixed(report.basisBps, 1, lang)} bps`}</Fig>
            </dd>
          </>
        )}
        {report.underlyingPremiumPct !== null && (
          <>
            <dt>{t('pos.premium')}</dt>
            <dd data-testid="pos-premium">
              <Fig>{`${signedFixed(report.underlyingPremiumPct, 2, lang)}%`}</Fig>
            </dd>
          </>
        )}
        {reliable && (
          <>
            <dt>{t('pos.senti')}</dt>
            <dd>
              <Sentiment v={report.sentiment} lang={lang} />
            </dd>
          </>
        )}
      </dl>

      <EvidenceList items={report.evidence} lang={lang} />
    </div>
  )
}
