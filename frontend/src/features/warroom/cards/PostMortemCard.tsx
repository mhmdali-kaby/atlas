import { ArrowDownRight, ArrowUpRight, CircleCheck, CircleX, Equal, Lightbulb, NotebookPen, PenLine, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, signOf, type Lang } from '../../../lib/format'
import { Button } from '../../../ui'
import { Duration } from '../../portfolio/parts'
import { fillSlots, slot } from '../slots'
import { Inline } from '../text'
import { Ladder } from './Ladder'
import { closeLabel, displayTags, num, planOf, splitTag, tp1Hit, type Outcome, type PostMortem } from './learning'
import { decimalsOf } from './pm'
import { Fig, Signed } from './pmParts'
import { rText } from './rText'
import { AsOf, InstrumentButton } from './TechnicalSetupCard'
import './learning.css'

/*
 * The Portfolio Manager's post-mortem in the War Room (brief §7.8 POST-MORTEM, design v16 §9,
 * ATL-203; card JSON from ATL-194). What the owner reads first is the outcome and the lesson:
 * the instrument and side, the outcome line exactly as in the Journal ("↘ Lost │ Stopped out"),
 * R and USD, the closed ladder (plan, ground covered, exit), a three-row expected-against-happened
 * table, the lesson (or that it is still being written), tags, and a link to the Journal entry.
 */

const OUT_ICON: Record<Outcome, LucideIcon> = { win: ArrowUpRight, loss: ArrowDownRight, scratch: Equal }

/** A signed R with its icon, so the sign never rests on colour alone. */
export function SignedR({ value, digits = 2, className = '', testId }: { value: string | null; digits?: number; className?: string; testId?: string }) {
  const s = signOf(value)
  const Icon = s === 1 ? ArrowUpRight : s === -1 ? ArrowDownRight : Equal
  if (value === null) return <span className={`lc-r is-zero ${className}`.trim()}>—</span>
  return (
    <span className={`lc-r ${s === 1 ? 'is-pos' : s === -1 ? 'is-neg' : 'is-zero'} ${className}`.trim()} data-testid={testId}>
      <Icon className="icon" aria-hidden="true" />
      <Fig>{rText(value, digits)}</Fig>
    </span>
  )
}

/** "Won │ Closed at TP2": the outcome in its ink, then the close label verbatim as its own item. */
export function OutcomeLine({ pm }: { pm: PostMortem }) {
  const { t } = useTranslation('warroom')
  const Icon = OUT_ICON[pm.outcome]
  const label = closeLabel(pm)
  return (
    <span className={`lc-out is-${pm.outcome}`} data-testid="pmo-outcome">
      <Icon className="icon" aria-hidden="true" />
      <span>{t(`pmo.out.${pm.outcome}`)}</span>
      {label && (
        <span className="lc-rule" data-testid="pmo-close">
          {t(`pmo.rule.${label}`)}
        </span>
      )}
    </span>
  )
}

export function useTagLabel() {
  const { t, i18n } = useTranslation('warroom')
  return (tag: string) => {
    const { group, value } = splitTag(tag)
    const key = group ? `pmo.tag.${group}.${value}` : ''
    return group && i18n.exists(`warroom:${key}`) ? t(key) : value.replace(/_/g, ' ')
  }
}

const pctText = (p: string, lang: Lang) => `${formatFixed(String(Math.round(Number(p) * 100)), 0, lang)}%`

export function PostMortemCard({ pm, lang }: { pm: PostMortem; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const tagLabel = useTagLabel()
  const plan = planOf(pm)
  const a = pm.actual
  const x = pm.expected
  const r = a.realisedR
  const side = t(pm.side === 'long' ? 'pmo.long' : 'pmo.short')
  const held = a.holdingMinutes === null ? null : <Duration ms={a.holdingMinutes * 60_000} lang={lang} />
  const hit = tp1Hit(pm)
  const tags = displayTags(pm.tags)

  const decimals = decimalsOf([x.stop, ...(x.entryZone ?? [x.entry]), ...x.targets.map((tp) => tp.price), a.avgExit])
  const mfe = num(a.mfeR)
  const rv = num(r)
  let caption: ReactNode = null
  if (a.mfeR && a.maeR && r) {
    const figs = { best: <Fig>{rText(a.mfeR, 2)}</Fig>, worst: <Fig>{rText(a.maeR, 2)}</Fig>, exit: <Fig>{rText(r, 2)}</Fig> }
    caption =
      rv !== null && mfe !== null && rv > 0 && mfe > 0
        ? fillSlots(t('pmo.cap', { best: slot('best'), worst: slot('worst'), exit: slot('exit'), kept: slot('kept') }), {
            ...figs,
            kept: <Fig>{`${formatFixed(String(Math.round(Math.min(1, rv / mfe) * 100)), 0, lang)}%`}</Fig>,
          })
        : fillSlots(t('pmo.capLoss', { best: slot('best'), worst: slot('worst'), exit: slot('exit') }), figs)
  }

  const rows: [string, ReactNode, ReactNode][] = [
    [
      'result',
      x.expectedR ? fillSlots(t('pmo.expR', { r: slot('r') }), { r: <Fig>{rText(x.expectedR, 1)}</Fig> }) : '—',
      <SignedR key="r" value={r} />,
    ],
    [
      'tp1',
      x.probability ? fillSlots(t('pmo.prob', { p: slot('p') }), { p: <Fig>{pctText(x.probability, lang)}</Fig> }) : '—',
      hit === null ? (
        '—'
      ) : hit ? (
        <span className="lc-good">
          <CircleCheck className="icon" aria-hidden="true" />
          {t('pmo.yes')}
        </span>
      ) : (
        <span className="lc-bad">
          <CircleX className="icon" aria-hidden="true" />
          {t('pmo.no')}
        </span>
      ),
    ],
    [
      'time',
      x.timeStopMinutes ? fillSlots(t('pmo.within', { d: slot('d') }), { d: <Duration ms={x.timeStopMinutes * 60_000} lang={lang} /> }) : t('pmo.noTimeStop'),
      held ? fillSlots(t('pmo.heldFor', { d: slot('d') }), { d: held }) : '—',
    ],
  ]

  return (
    <div className={`hv-card lc-card pmo-card is-${pm.outcome}`} role="group" aria-label={t('pmo.aria', { inst: pm.instId, side, outcome: t(`pmo.out.${pm.outcome}`), r: r ? rText(r, 2) : '—' })} data-testid="pmo-card">
      <div className="hv-head lc-head">
        <span className="hv-tile">
          <NotebookPen aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('pmo.kind')}</span>
        {pm.proposalId && (
          <span className="lc-ref">
            {fillSlots(t('pmo.ref', { id: slot('id') }), { id: <Fig>{pm.proposalId.slice(0, 8)}</Fig> })}
          </span>
        )}
        {pm.lessonUpdate && <span className="lc-ref">{t('pmo.lessonUpdate')}</span>}
        <AsOf ts={pm.asOf} lang={lang} testId="pmo-asof" />
      </div>

      <div className="pmo-top">
        <div className="pmo-what">
          <p className="pmo-inst">
            <InstrumentButton instId={pm.instId} testId="pmo-inst" />
            <span className="pmo-side">{side}</span>
          </p>
          <p className="pmo-how">
            <OutcomeLine pm={pm} />
            {held && <span data-testid="pmo-held">{fillSlots(t('pmo.held', { d: slot('d') }), { d: held })}</span>}
          </p>
        </div>
        <div className="pmo-figs">
          <p className="pmo-r">
            <SignedR value={r} testId="pmo-r" />
          </p>
          <p className="pmo-usd">
            <Signed value={a.realisedUsd} lang={lang} testId="pmo-usd" />
          </p>
        </div>
      </div>

      {plan && r && (
        <div className="pmo-ladder">
          <Ladder
            side={pm.side}
            entry={plan.zone}
            stop={plan.stop}
            targets={plan.targets.map((tp) => ({ price: tp.price, pct: tp.pct }))}
            travel={{ mae: a.maeR, mfe: a.mfeR, exit: r, price: a.avgExit }}
            decimals={decimals}
            compact
            lang={lang}
          />
        </div>
      )}
      {caption && (
        <p className="pmo-cap" data-testid="pmo-caption">
          {caption}
        </p>
      )}

      <table className="pmo-cmp" data-testid="pmo-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">{t('pmo.compare')}</span>
            </th>
            <th scope="col">{t('pmo.exp')}</th>
            <th scope="col">{t('pmo.act')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, e, h]) => (
            <tr key={k} data-row={k}>
              <th scope="row">{t(`pmo.${k}`)}</th>
              <td>{e}</td>
              <td>{h}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {pm.lessonPending || !pm.lesson ? (
        <div className="pmo-lesson is-pending" data-testid="pmo-pending">
          <PenLine className="icon" aria-hidden="true" />
          <p>{t('pmo.pending')}</p>
        </div>
      ) : (
        <div className="pmo-lesson" data-testid="pmo-lesson">
          <Lightbulb className="icon" aria-hidden="true" />
          <div>
            <p className="pmo-lesson-k">{t('pmo.lesson')}</p>
            <p className="pmo-lesson-text" dir="auto">
              <Inline text={pm.lesson} flat />
            </p>
          </div>
        </div>
      )}

      <div className="pmo-foot">
        {tags.length > 0 && (
          <ul className="pmo-tags" aria-label={t('pmo.tags')} data-testid="pmo-tags">
            {tags.map((g) => (
              <li key={g} className="lc-tag">
                {tagLabel(g)}
              </li>
            ))}
          </ul>
        )}
        <Button asChild size="sm" variant="secondary">
          <Link to={`/journal?entry=${encodeURIComponent(pm.journalId)}`} data-testid="pmo-journal">
            <NotebookPen aria-hidden="true" strokeWidth={1.75} />
            <span>{t('pmo.open')}</span>
          </Link>
        </Button>
      </div>
    </div>
  )
}
