import { Fragment, type CSSProperties, type ReactNode } from 'react'
import { DATE_LOCALE, formatFixed, type Lang } from '../../lib/format'
import { InstrumentId } from './text'
import { formatTime } from './time'

/* Small formatting pieces shared by the watchlist card's parts. */

export const css = (vars: Record<string, string | number>) => vars as CSSProperties

/** "A, B and C" with React nodes, in the reader's language. */
export function joinList(items: ReactNode[], lang: Lang): ReactNode[] {
  const marks = items.map((_, i) => `\u0001${i}\u0001`)
  const parts = new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { style: 'long', type: 'conjunction' }).formatToParts(marks)
  return parts.map((p, k) => <Fragment key={k}>{p.type === 'element' ? items[Number(p.value.slice(1, -1))] : p.value}</Fragment>)
}
export const insts = (ids: string[], lang: Lang) => joinList(ids.map((id) => <InstrumentId key={id} id={id} />), lang)

/** "Wednesday 28 October" for a "YYYY-MM-DD" day: formatted at midday Baghdad, never at midnight. */
export function titleDate(date: string, lang: Lang): string {
  return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { timeZone: 'Asia/Baghdad', weekday: 'long', day: 'numeric', month: 'long' }).format(
    new Date(`${date}T09:00:00Z`),
  )
}

export const conv = (v: number, lang: Lang) => formatFixed(v, 2, lang)
export const price = (v: number, lang: Lang) => formatFixed(v, Math.abs(v) >= 1000 && Number.isInteger(v) ? 0 : 2, lang)

export function Time({ ts, lang }: { ts: string | number; lang: Lang }) {
  const iso = typeof ts === 'number' ? new Date(ts).toISOString() : ts
  return (
    <time className="fig num" dateTime={iso}>
      {formatTime(ts, lang)}
    </time>
  )
}
