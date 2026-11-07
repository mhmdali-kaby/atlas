import { ArrowDownRight, ArrowUpRight, Equal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DATE_LOCALE, formatSigned, type Lang } from '../../lib/format'
import { ownerDateFormat } from '../../lib/ownerZone'
import { formatTime } from '../warroom/time'
import { splitTag } from './model'

/* Formatting shared by the Journal's row and entry (no components here). */

export const OUTCOME_ICON = { win: ArrowUpRight, loss: ArrowDownRight, scratch: Equal } as const

/** "+$3.71" / "−$512.40": the sign only (Footnote, secondary in the row). */
export function usdText(v: string | null | undefined, lang: Lang): string {
  return v === null || v === undefined || v === '' ? '—' : formatSigned(v, 'USD', lang)
}

/** "Tue 27 Oct, 17:04" / "الثلاثاء 27 تشرين الأول، 17:04" in the owner's zone (design §7 row line 2). */
export function whenText(ts: string | number, lang: Lang): string {
  const f = ownerDateFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { weekday: 'short', day: 'numeric', month: 'short' })
  const ms = typeof ts === 'number' ? ts : Date.parse(ts.replace(/(\.\d{3})\d+/, '$1'))
  return `${f.format(new Date(ms))}${lang === 'ar' ? '، ' : ', '}${formatTime(ms, lang)}`
}

/** The agents a post-mortem can name (the proposer and the voters, ATL-194 data.agents). */
export const NAMEABLE = ['technical_analyst', 'derivatives_sentiment', 'news_intelligence', 'historian', 'devils_advocate', 'strategist'] as const

/** "Breakout", "Crypto", …: a tag's words; an unknown value reads as itself. */
export function useTagLabel() {
  const { t } = useTranslation('journal')
  return (tag: string) => {
    const { group, value } = splitTag(tag)
    if (!group) return value.replace(/_/g, ' ')
    return t(`tag.${group}.${value}`, { defaultValue: value.replace(/_/g, ' ') })
  }
}
