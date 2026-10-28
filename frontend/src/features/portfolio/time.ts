import { DATE_LOCALE, type Lang } from '../../lib/format'
import type { EquityRange } from './api'

/*
 * Dates and times on the Overview: Asia/Baghdad, 24-hour clock, Western digits in
 * both languages (DATE_LOCALE: en-US / ar-IQ-u-nu-latn; never a bare "ar").
 */

export const OWNER_TZ = 'Asia/Baghdad'
const HOUR = 3_600_000
const DAY = 24 * HOUR
/** Baghdad is UTC+3 all year (no DST since 2008). */
const BAGHDAD_OFFSET = 3 * HOUR

const cache = new Map<string, Intl.DateTimeFormat>()
function dtf(lang: Lang, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${lang}|${JSON.stringify(options)}`
  let f = cache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(DATE_LOCALE[lang], { timeZone: OWNER_TZ, ...options })
    cache.set(key, f)
  }
  return f
}

/** "17:36:00" */
export function formatClock(ts: number | string, lang: Lang, seconds = true): string {
  return dtf(lang, { hour: '2-digit', minute: '2-digit', second: seconds ? '2-digit' : undefined, hourCycle: 'h23' }).format(
    new Date(ts),
  )
}

/** "Oct 28" / "28 تشرين الأول" */
export function formatDay(ts: number | string, lang: Lang, year = false): string {
  return dtf(lang, { day: 'numeric', month: 'short', year: year ? 'numeric' : undefined }).format(new Date(ts))
}

/** "Oct 28, 17:36" */
export function formatDayTime(ts: number | string, lang: Lang): string {
  return dtf(lang, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts))
}

/** "12 minutes ago" / "قبل 12 دقيقة", Western digits. */
export function formatAgo(ms: number, lang: Lang): string {
  const rtf = new Intl.RelativeTimeFormat(DATE_LOCALE[lang], { numeric: 'auto', style: 'long' })
  const m = Math.max(0, Math.round(ms / 60_000))
  if (m < 60) return rtf.format(-m, 'minute')
  const h = Math.round(m / 60)
  if (h < 48) return rtf.format(-h, 'hour')
  return rtf.format(-Math.round(h / 24), 'day')
}

export interface TimeTick {
  ts: number
  label: string
}

/** The time axis' ticks per range (ported from the approved prototype's timeTicks). */
export function timeTicks(t0: number, t1: number, range: EquityRange, lang: Lang): TimeTick[] {
  const out: TimeTick[] = []
  if (t1 <= t0) return out
  if (range === '1D') {
    const f = dtf(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    const step = 4 * HOUR
    for (let ts = Math.ceil(t0 / step) * step; ts < t1 - HOUR / 2; ts += step) out.push({ ts, label: f.format(ts) })
  } else if (range === '1W') {
    const f = dtf(lang, { weekday: 'short', day: 'numeric' })
    const start = Math.ceil((t0 + BAGHDAD_OFFSET) / DAY) * DAY - BAGHDAD_OFFSET
    for (let ts = start; ts < t1 - 6 * HOUR; ts += DAY) out.push({ ts, label: f.format(ts) })
  } else if (range === '1M') {
    const f = dtf(lang, { day: 'numeric', month: 'short' })
    const start = Math.ceil((t0 + BAGHDAD_OFFSET) / DAY) * DAY - BAGHDAD_OFFSET + 2 * DAY
    for (let ts = start; ts < t1 - 2 * DAY; ts += 6 * DAY) out.push({ ts, label: f.format(ts) })
  } else {
    const short = t1 - t0 < 190 * DAY
    const f = dtf(lang, short ? { day: 'numeric', month: 'short' } : { month: 'short', year: '2-digit' })
    const d = new Date(t0)
    const months = Math.ceil((t1 - t0) / (28 * DAY)) + 2
    for (let m = 0; m < months; m++) {
      for (const day of short ? [1, 15] : [1]) {
        const x = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + m, day) - BAGHDAD_OFFSET
        if (x > t0 + 3 * DAY && x < t1 - 4 * DAY) out.push({ ts: x, label: f.format(x) })
      }
    }
    // A long history: at most ~8 labels.
    if (out.length > 8) {
      const every = Math.ceil(out.length / 8)
      return out.filter((_, i) => i % every === 0)
    }
  }
  return out
}

/** Nice round bounds and 4–5 ticks for the value axis. Display only, so numbers are fine. */
export function valueTicks(min: number, max: number): { lo: number; hi: number; ticks: number[] } {
  const span = max - min || Math.max(Math.abs(max) * 0.01, 1)
  const rawStep = span / 4
  const mag = 10 ** Math.floor(Math.log10(rawStep))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rawStep) ?? 10 * mag
  const lo = Math.floor(min / step) * step
  let hi = Math.ceil(max / step) * step
  if (hi === lo) hi = lo + step
  const ticks: number[] = []
  for (let v = lo; v <= hi + step / 1e6; v += step) ticks.push(Number(v.toPrecision(12)))
  return { lo, hi, ticks }
}
