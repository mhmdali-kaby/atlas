import { DATE_LOCALE, type Lang } from '../../lib/format'
import { ownerDateFormat, ownerZone, teamDay } from '../../lib/ownerZone'
import { zoneCity } from '../../lib/zones'

/*
 * War Room times (design v4 War Room §1, §12; v21 "Two clocks"): every clock time is in the
 * owner's zone (Settings → General), 24-hour clock, Western digits in both languages. The team's
 * day ("Today", "Yesterday", day separators) stays the Baghdad business day: lib/ownerZone.
 */

const DAY_MS = 86_400_000

/** The city of the owner's zone, for copy that names the zone a time is in ("14:05 London"). */
export function ownerCity(lang: Lang): string {
  return zoneCity(ownerZone(), lang)
}

function dtf(lang: Lang, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  // The War Room design writes dates day-first in English ("27 Oct", "14 Jul 2026").
  return ownerDateFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, options)
}

const ms = (ts: string | number) => (typeof ts === 'number' ? ts : Date.parse(ts))

/** "2026-10-28": the Baghdad calendar day of an instant. */
export function baghdadDay(ts: string | number): string {
  return teamDay(ms(ts))
}

/** "14:05" in the owner's zone. */
export function formatTime(ts: string | number, lang: Lang): string {
  return dtf(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ms(ts)))
}

/** "14:05:12" in the owner's zone: the Replay day line's clock (ATL-207). */
export function formatClock(ts: string | number, lang: Lang): string {
  return dtf(lang, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(new Date(ms(ts)))
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

/** "Wed 28 Oct" in the owner's zone (calendar day headers). */
export function formatDayShort(ts: string | number, lang: Lang): string {
  return dtf(lang, { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(ms(ts)))
}

/** "28 Oct, 06:10" in the owner's zone. */
export function formatDateTime(ts: string | number, lang: Lang): string {
  return `${formatDate(ts, lang)}${lang === 'ar' ? '، ' : ', '}${formatTime(ts, lang)}`
}

/** "5:54": a countdown's minutes and seconds left, rounded up so 0:00 never shows while time remains. */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
