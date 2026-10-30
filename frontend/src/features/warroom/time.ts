import { DATE_LOCALE, type Lang } from '../../lib/format'

/*
 * War Room times (design v4 War Room §1, §12): Asia/Baghdad, 24-hour clock, Western digits in
 * both languages. Baghdad is UTC+3 all year (no DST since 2008), so its calendar day is the UTC
 * day of the instant plus three hours.
 */

export const OWNER_TZ = 'Asia/Baghdad'
const BAGHDAD_OFFSET_MS = 3 * 3_600_000
const DAY_MS = 86_400_000

const cache = new Map<string, Intl.DateTimeFormat>()
function dtf(lang: Lang, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${lang}|${JSON.stringify(options)}`
  let f = cache.get(key)
  if (!f) {
    // The War Room design writes dates day-first in English ("27 Oct", "14 Jul 2026").
    f = new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, { timeZone: OWNER_TZ, ...options })
    cache.set(key, f)
  }
  return f
}

const ms = (ts: string | number) => (typeof ts === 'number' ? ts : Date.parse(ts))

/** "2026-10-28": the Baghdad calendar day of an instant. */
export function baghdadDay(ts: string | number): string {
  return new Date(ms(ts) + BAGHDAD_OFFSET_MS).toISOString().slice(0, 10)
}

/** "14:05" in Baghdad. */
export function formatTime(ts: string | number, lang: Lang): string {
  return dtf(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ms(ts)))
}

/** The time's tooltip: "Wed, 28 Oct 2026, 14:05:12". */
export function formatFullTime(ts: string | number, lang: Lang): string {
  return dtf(lang, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(ms(ts)))
}

/** "27 Oct" or, with `year`, "14 Jul 2026". */
export function formatDate(ts: string | number, lang: Lang, year = false): string {
  return dtf(lang, { day: 'numeric', month: 'short', year: year ? 'numeric' : undefined }).format(new Date(ms(ts)))
}

function relativeDay(ts: string | number, now: number): 'today' | 'yesterday' | 'other' {
  const day = baghdadDay(ts)
  if (day === baghdadDay(now)) return 'today'
  if (day === baghdadDay(now - DAY_MS)) return 'yesterday'
  return 'other'
}

/** A thread row's time: "HH:mm" today, "Yesterday", or "27 Oct". */
export function dayOrTime(ts: string | number, lang: Lang, now: number, yesterday: string): string {
  const rel = relativeDay(ts, now)
  if (rel === 'today') return formatTime(ts, lang)
  if (rel === 'yesterday') return yesterday
  return formatDate(ts, lang)
}

/** A day separator: "Today", "Yesterday", or "Monday 26 October". */
export function dayLabel(ts: string | number, lang: Lang, now: number, words: { today: string; yesterday: string }): string {
  const rel = relativeDay(ts, now)
  if (rel === 'today') return words.today
  if (rel === 'yesterday') return words.yesterday
  return dtf(lang, { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date(ms(ts)))
}

const utcTime = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const utcFull = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

/** "01:47": the instant's time in UTC (Western digits in both languages, like every War Room time). */
export function formatUtcTime(ts: string | number): string {
  return utcTime.format(new Date(ms(ts)))
}

/** "28 Oct, 01:47": the instant's date and time in UTC, for tooltips. */
export function formatUtcFull(ts: string | number): string {
  return utcFull.format(new Date(ms(ts)))
}

/** "Wed 28 Oct" in Baghdad (calendar day headers). */
export function formatDayShort(ts: string | number, lang: Lang): string {
  return dtf(lang, { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(ms(ts)))
}

/** "28 Oct, 06:10" in Baghdad. */
export function formatDateTime(ts: string | number, lang: Lang): string {
  return `${formatDate(ts, lang)}${lang === 'ar' ? '، ' : ', '}${formatTime(ts, lang)}`
}
