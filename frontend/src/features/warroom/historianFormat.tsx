import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatSigned, type Lang } from '../../lib/format'

/* Formats and slot filling shared by the Historian cards (HistoryVerdictCard, RegimeTableCard). */

/** A signed return given as a fraction: 0.0094 → "+0.94%", −0.0383 → "−3.83%". */
export const pctSigned = (v: number, lang: Lang) => formatSigned(v * 100, 'percent', lang)
/** A 0–100 share → "58%". */
export const pctWhole = (v: number, lang: Lang) => `${formatFixed(v, 0, lang)}%`
/** A 0–1 probability → "71%". */
export const probability = (p: number, lang: Lang) => `${formatFixed(p * 100, 0, lang)}%`

/** A translated template split around {{slot}} placeholders, each placed as a React node. */
export function fillSlots(template: string, nodes: Record<string, ReactNode>): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of template.matchAll(/([a-z]+)/g)) {
    if (m.index > last) out.push(<Fragment key={`t${last}`}>{template.slice(last, m.index)}</Fragment>)
    out.push(<Fragment key={`s${m.index}`}>{nodes[m[1]!] ?? null}</Fragment>)
    last = m.index + m[0].length
  }
  if (last < template.length) out.push(<Fragment key={`t${last}`}>{template.slice(last)}</Fragment>)
  return out
}
/** The slot marker passed to t() for a value that fillSlots replaces. */
export const slot = (name: string) => `${name}`

/** days_in_regime → "6 days", "10 hours", "under an hour". */
export function useSpanText() {
  const { t } = useTranslation('warroom')
  return (d: number) => {
    if (d < 1 / 24) return t('rg.underHour')
    if (d < 1) return t('rg.hours', { count: Math.round(d * 24) })
    return t('rg.days', { count: Math.round(d) })
  }
}

