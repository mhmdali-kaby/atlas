import { ScrollText } from 'lucide-react'
import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { baseOf, type ConditionDraft, type FormState } from './model'
import { Fig, NS } from './parts'
import { dayText, fill, pctText, useIndicatorLabel } from './text'

/*
 * The rule read as one sentence (design: .bt-sentence), so the owner reads what will run before it
 * runs. Every value the form sets is a token; a missing one is a red dash.
 */

function Tok({ children, missing }: { children: ReactNode; missing?: boolean }) {
  return <span className={cx('bt-tok', missing && 'is-missing')}>{children}</span>
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
