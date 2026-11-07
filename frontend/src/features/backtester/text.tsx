import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { formatDate } from '../warroom/time'
import { CLOSE, type FormState } from './model'
import { NS } from './parts'

/** Splits "{{a}} … {{b}}" on its placeholders and drops nodes into them (the template stays in the locale file). */
export function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>
  })
}

/** Noon Baghdad of a calendar day, so the date never slips a day in either locale. */
export const dayText = (day: string, lang: Lang, year = false) => (day ? formatDate(`${day}T12:00:00+03:00`, lang, year) : '—')

/** "3%", "0.5%": a percentage with only the decimals it has. */
export function pctText(raw: string, lang: Lang): string {
  if (!/^-?\d+(\.\d+)?$/.test(raw.trim())) return `${raw.trim()}%`
  const decimals = (raw.trim().split('.')[1] ?? '').replace(/0+$/, '').length
  return `${formatFixed(raw.trim(), decimals, lang)}%`
}

export function useIndicatorLabel(form: Pick<FormState, 'indicators'>) {
  const { t } = useTranslation(NS)
  return (name: string) => {
    if (name === CLOSE) return t('close')
    const ind = form.indicators.find((x) => x.name === name)
    return ind ? `${ind.kind.toUpperCase()} ${ind.window}` : name
  }
}
