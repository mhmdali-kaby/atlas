import { ScrollText } from 'lucide-react'
import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { formatDate } from '../warroom/time'
import { baseOf, CLOSE, type ConditionDraft, type FormState } from './model'
import { Fig, fill, NS } from './parts'

/*
 * The rule read as one sentence (design: .bt-sentence), so the owner reads what will run before it
 * runs. Every value the form sets is a token; a missing one is a red dash.
 */

/** Noon Baghdad of a calendar day, so the date never slips a day in either locale. */
export const dayText = (day: string, lang: Lang, year = false) => (day ? formatDate(`${day}T12:00:00+03:00`, lang, year) : '—')

/** "3%", "0.5%": a percentage with only the decimals it has. */
export function pctText(raw: string, lang: Lang): string {
  const decimals = (raw.trim().split('.')[1] ?? '').replace(/0+$/, '').length
  return `${formatFixed(raw.trim(), decimals, lang)}%`
}

function Tok({ children, missing }: { children: ReactNode; missing?: boolean }) {
  return <span className={cx('bt-tok', missing && 'is-missing')}>{children}</span>
}

export function useIndicatorLabel(form: Pick<FormState, 'indicators'>) {
  const { t } = useTranslation(NS)
  return (name: string) => {
    if (name === CLOSE) return t('close')
    const ind = form.indicators.find((x) => x.name === name)
    return ind ? `${ind.kind.toUpperCase()} ${ind.window}` : name
  }
}

const joined = (items: ReactNode[], sep: string): ReactNode =>
  items.map((item, i) => (
    <Fragment key={i}>
      {i > 0 && sep}
      {item}
    </Fragment>
  ))

export function Sentence({ form, compact }: { form: FormState; compact?: boolean }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const label = useIndicatorLabel(form)

  const cond = (c: ConditionDraft) => (
    <>
      <Tok>{label(c.left)}</Tok> {t(`op.${c.op}`)} <Tok>{c.num ? <Fig>{c.right.trim() || '—'}</Fig> : label(c.right)}</Tok>
    </>
  )
  const insts = form.insts.length ? joined(form.insts.map((i) => <Tok key={i}><Fig>{baseOf(i)}</Fig></Tok>), t('s.and')) : <Tok missing>—</Tok>
  const exits: ReactNode[] = form.exit.map((c) => cond(c))
  if (form.stop.trim()) exits.push(fill(t('s.stop'), { v: <Tok><Fig>{pctText(form.stop, lang)}</Fig></Tok> }))
  if (form.tp.trim()) exits.push(fill(t('s.tp'), { v: <Tok><Fig>{pctText(form.tp, lang)}</Fig></Tok> }))

  return (
    <section className={cx('bt-rule bt-panel', compact && 'is-compact')} aria-label={t('f.rule')}>
      <ScrollText aria-hidden="true" className="bt-rule-ic" strokeWidth={1.5} />
      <p className={cx('bt-sentence', compact && 'is-compact')} aria-live={compact ? undefined : 'polite'} data-testid="bt-sentence">
        {fill(t('s.go'), {
          side: <Tok>{t(form.side === 'long' ? 'long' : 'short')}</Tok>,
          insts,
          bar: <Tok><Fig>{form.bar}</Fig></Tok>,
          from: <Tok>{dayText(form.from, lang)}</Tok>,
          to: <Tok>{dayText(form.to, lang, true)}</Tok>,
        })}{' '}
        {fill(t('s.enter'), { conds: joined(form.entry.map((c) => cond(c)), t('s.and')) })}{' '}
        {fill(t('s.exit'), { conds: exits.length ? joined(exits, t('s.or')) : t('s.none') })}{' '}
        {fill(t('s.test'), {
          n: <Tok><Fig>{formatFixed(form.splits, 0, lang)}</Fig></Tok>,
          bps: <Tok>{fill(t('bps'), { n: <Fig>{formatFixed(form.slip, 0, lang)}</Fig> })}</Tok>,
        })}
      </p>
    </section>
  )
}
