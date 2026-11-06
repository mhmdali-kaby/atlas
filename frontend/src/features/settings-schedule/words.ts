import { DATE_LOCALE, type Lang } from '../../lib/format'
import { TEAM_TZ } from '../../lib/ownerZone'
import { WEEKDAYS, type Weekday } from './model'

export const NS = 'settings-schedule'
const LRI = '\u2066'
const PDI = '\u2069'
/** A time inside a sentence: an LTR isolate so "07:30" never reorders in Arabic. */
export const iso = (s: string | number) => `${LRI}${s}${PDI}`

/** "Sunday" / "الأحد" for a settings weekday ("Sun"). 2 Nov 2026 is a Monday. */
export function dayName(day: Weekday, lang: Lang, style: 'long' | 'short' = 'long'): string {
  const ref = Date.UTC(2026, 10, 2 + WEEKDAYS.indexOf(day), 12)
  return new Intl.DateTimeFormat(DATE_LOCALE[lang], { weekday: style, timeZone: 'UTC' }).format(new Date(ref))
}

/** "Friday 03:00" in Baghdad for an instant. */
export function teamWeekdayTime(ms: number, lang: Lang): string {
  const parts = new Intl.DateTimeFormat(DATE_LOCALE[lang], { timeZone: TEAM_TZ, weekday: 'long', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms))
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return `${get('weekday')} ${iso(`${get('hour')}:${get('minute')}`)}`
}
