import { CircleAlert, CircleCheck, Database, Sigma, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { MIN_N, normaliseRaw, type HistoryVerdictCard as Card, type Unmatched } from './historian'
import { probability, useSpanText } from './historianFormat'
import { AsOf, Fig, RegimeLabel, RuleBased, type MarkFn } from './historianParts'
import { HorizonTable } from './HorizonTable'
import { fillSlots, slot } from './slots'
import { InstrumentId, Inline } from './text'
import { formatDate } from './time'
import { UntracedMark, UntracedScope } from './Untraced'
import { CorrectionNote, VerdictPill } from './VerdictPill'

/*
 * HistoryVerdict card (design v7 "Historian cards and live reasoning" §5): question, then the
 * verdict, then the evidence. Statistics keep their sign but stay in label ink (they are not
 * P&L); the verdict pill is the one coloured thing on the card, always icon + words.
 */

// ----------------------------------------------------------------------------------- sample

function Sample({ card, lang }: { card: Card; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const segs: ReactNode[] = []
  if (card.instId) segs.push(<InstrumentId id={card.instId} />)
  if (card.bar) {
    const label = t(`hv.bar.${card.bar}`, { defaultValue: '' })
    segs.push(label ? <span>{label}</span> : <Fig>{card.bar}</Fig>)
  }
  if (card.period) {
    const [a = '', b = ''] = card.period.split('..')
    const da = Date.parse(a)
    const db = Date.parse(b)
    segs.push(
      Number.isFinite(da) && Number.isFinite(db) ? (
        <span>
          {fillSlots(t('hv.period', { from: slot('from'), to: slot('to') }), {
            from: <span className="num">{formatDate(da, lang, true)}</span>,
            to: <span className="num">{formatDate(db, lang, true)}</span>,
          })}
        </span>
      ) : (
        <Fig>{card.period}</Fig>
      ),
    )
  }
  segs.push(
    card.noData ? (
      <span className="hv-n" title={t('hv.noCasesTip')} data-testid="hv-n">
        {t('hv.noCases')}
      </span>
    ) : (
      <span className="hv-n num" data-testid="hv-n">
        {t('hv.cases', { count: card.n })}
      </span>
    ),
  )
  return (
    <p className="hv-sample" data-testid="hv-sample">
      <span className="sr-only">{t('hv.sample')}: </span>
      {segs.map((s, i) => (
        <span key={i} className="hv-seg">
          {s}
        </span>
      ))}
      {!card.noData && card.n < MIN_N && (
        <span className="hv-weak" tabIndex={0} title={t('hv.weakTip')} data-testid="hv-weak">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>{t('hv.weak')}</span>
        </span>
      )}
    </p>
  )
}

// ---------------------------------------------------------------------------- context rows

function Context({ card, lang, mark }: { card: Card; lang: Lang; mark: MarkFn }) {
  const { t } = useTranslation('warroom')
  const spanText = useSpanText()
  const comma = <span className="hv-comma">{lang === 'ar' ? '، ' : ', '}</span>
  if (!card.regime && !card.stop) return null
  const r = card.regime
  const s = card.stop
  return (
    <dl className="hv-ctx">
      {r && (
        <>
          <dt>{t('hv.regime')}</dt>
          <dd data-testid="hv-regime">
            <RegimeLabel label={r.label} />
            {comma}
            {r.probability === null ? (
              <RuleBased />
            ) : (
              <span>
                {fillSlots(t('rg.likely', { p: slot('p') }), { p: mark('regime.probability', <Fig>{probability(r.probability, lang)}</Fig>) })}
              </span>
            )}
            {r.days !== null && (
              <>
                {comma}
                <span>{t('rg.for', { d: spanText(r.days) })}</span>
              </>
            )}
          </dd>
        </>
      )}
      {s && (
        <>
          <dt>{t('hv.stop')}</dt>
          <dd data-testid="hv-stop">
            <span>
              {fillSlots(t('hv.stopLine', { stop: slot('stop'), range: slot('range') }), {
                stop: mark('stop_noise_check.stop_distance_atr', <Fig>{formatFixed(s.stopAtr, 1, lang)}</Fig>),
                range: mark('stop_noise_check.median_4h_range_atr', <Fig>{formatFixed(s.rangeAtr, 1, lang)}</Fig>),
              })}
            </span>
            <span className={s.verdict === 'inside_noise' ? 'hv-noise is-inside' : 'hv-noise is-ok'}>
              {s.verdict === 'inside_noise' ? <TriangleAlert aria-hidden="true" strokeWidth={2} /> : <CircleCheck aria-hidden="true" strokeWidth={2} />}
              <span>{t(`hv.stopState.${s.verdict}`)}</span>
            </span>
          </dd>
        </>
      )}
    </dl>
  )
}

// -------------------------------------------------------------------------------- the card

/** Key stat: an untraced figure is marked where it appears in the line, else the whole line is. */
function KeyStat({ text, raws }: { text: string; raws: string[] }) {
  const norm = normaliseRaw(text)
  const found = raws.filter((r) => norm.includes(normaliseRaw(r)))
  const body = <Inline text={text} />
  return (
    <p className="hv-key" dir="auto" data-testid="hv-key">
      {raws.length === 0 ? body : found.length > 0 ? <UntracedScope raws={found}>{body}</UntracedScope> : <UntracedMark>{body}</UntracedMark>}
    </p>
  )
}

export function HistoryVerdictCard({ card, unmatched, lang }: { card: Card; unmatched: Unmatched[]; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const cardPaths = new Set(unmatched.filter((u) => u.location === 'card' && u.path).map((u) => u.path!))
  const keyRaws = unmatched.filter((u) => u.location === 'card' && u.path === 'key_stat').map((u) => u.raw)
  const mark: MarkFn = (path, node) => (cardPaths.has(path) ? <UntracedMark>{node}</UntracedMark> : node)
  return (
    <UntracedScope raws={[]}>
    <div className="hv-card" role="group" aria-label={t('kind.research_report')} data-testid="hv-card">
      <div className="hv-head">
        <span className="hv-tile">
          <Sigma aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('kind.research_report')}</span>
        <AsOf ts={card.dataAsOf} lang={lang} />
      </div>
      <p className="hv-q" dir="auto" data-testid="hv-question">
        <Inline text={card.question} />
      </p>
      <div className="hv-verdict">
        <VerdictPill verdict={card.verdict} />
        {card.corrections.map((c, i) => (
          <CorrectionNote key={i} c={c} n={card.n} />
        ))}
      </div>
      {card.keyStat && <KeyStat text={card.keyStat} raws={keyRaws} />}
      <div className="hv-evidence">
        <Sample card={card} lang={lang} />
        {card.horizons.length > 0 && <HorizonTable card={card} lang={lang} mark={mark} />}
        <Context card={card} lang={lang} mark={mark} />
      </div>
      {card.overfit && (
        <p className="hv-warn" data-testid="hv-overfit">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>{t('hv.overfit')}</span>
        </p>
      )}
      {card.notes.length > 0 && (
        <div className="hv-notes" data-testid="hv-notes">
          <p className="hv-notes-h">
            <Database aria-hidden="true" strokeWidth={1.5} />
            <span>{t('hv.notes')}</span>
          </p>
          <ul>
            {card.notes.map((n, i) => (
              <li key={i} dir="auto">
                <Inline text={n} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {unmatched.length > 0 && (
        <p className="hv-unv-note" data-testid="hv-unv-note">
          <CircleAlert aria-hidden="true" strokeWidth={1.75} />
          <span>{t('hv.unvNote', { count: unmatched.length })}</span>
        </p>
      )}
    </div>
    </UntracedScope>
  )
}
