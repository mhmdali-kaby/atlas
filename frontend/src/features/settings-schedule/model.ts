import type { SettingsChanges, SettingsDoc } from '../onboarding'

/*
 * Settings → Schedule (decision 255 (2), ATL-228 keys; design v21 js/pages/settings-schedule.js).
 * Pure maths for the page: every time is a Baghdad wall-clock "HH:MM" (the clock the team keeps);
 * the owner's zone only adds a second reading beside each one.
 */

/** The team's clock. Schedules, quiet hours and the weekly review are all set in it. */
export const TEAM_TZ = 'Asia/Baghdad'
export const DAY_MIN = 1440

export const JOB_KEYS = ['news_morning_brief', 'historian_regime_table', 'scout_watchlist', 'pm_daily_report'] as const
export type JobKey = (typeof JOB_KEYS)[number]
export const WEEKLY_KEY = 'historian_weekly_review' as const

/** Every schedule.* key the page edits, without the prefix, in display order. */
export const FIELDS = [
  ...JOB_KEYS,
  WEEKLY_KEY,
  'analysis_interval_minutes',
  'analysis_first_slot',
  'analysis_last_slot',
  'quiet_hours_enabled',
  'quiet_hours_start',
  'quiet_hours_end',
  'stock_perps_weekend',
] as const
export type Field = (typeof FIELDS)[number]

/** The HH:MM fields (the weekly review keeps its time beside a weekday). */
export const TIME_FIELDS = [...JOB_KEYS, 'analysis_first_slot', 'analysis_last_slot', 'quiet_hours_start', 'quiet_hours_end'] as const
export type TimeField = (typeof TIME_FIELDS)[number] | typeof WEEKLY_KEY

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
export type Weekday = (typeof WEEKDAYS)[number]
export type Weekend = 'allow' | 'no_new_trades'

export interface Schedule {
  news_morning_brief: string
  historian_regime_table: string
  scout_watchlist: string
  pm_daily_report: string
  /** "Sun 18:00" */
  historian_weekly_review: string
  analysis_interval_minutes: number
  analysis_first_slot: string
  analysis_last_slot: string
  quiet_hours_enabled: boolean
  quiet_hours_start: string
  quiet_hours_end: string
  stock_perps_weekend: Weekend
}

/** config/defaults.yaml, for a row saved before ATL-228 (the API fills them too). */
export const DEFAULTS: Schedule = {
  news_morning_brief: '07:30',
  historian_regime_table: '07:30',
  scout_watchlist: '08:00',
  pm_daily_report: '23:30',
  historian_weekly_review: 'Sun 18:00',
  analysis_interval_minutes: 30,
  analysis_first_slot: '08:10',
  analysis_last_slot: '23:00',
  quiet_hours_enabled: false,
  quiet_hours_start: '23:00',
  quiet_hours_end: '07:00',
  stock_perps_weekend: 'allow',
}

export const INTERVAL = { min: 15, max: 240, step: 15 } as const

export const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/
export const WEEKLY_RE = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01]\d|2[0-3]):([0-5]\d)$/

export const settingKey = (f: Field) => `schedule.${f}`

export function scheduleFrom(doc: SettingsDoc): Schedule {
  const s = doc.settings as Record<string, unknown>
  const out = { ...DEFAULTS } as Record<string, unknown>
  for (const f of FIELDS) {
    const v = s[settingKey(f)]
    if (v !== undefined && v !== null) out[f] = v
  }
  return out as unknown as Schedule
}

export const hm = (s: string): number => {
  const m = TIME_RE.exec(s)
  return m ? Number(m[1]) * 60 + Number(m[2]) : NaN
}
export const clock = (min: number): string => {
  const m = ((Math.round(min) % DAY_MIN) + DAY_MIN) % DAY_MIN
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export const weeklyParts = (v: string): { day: Weekday; time: string } => {
  const [day, time] = v.split(' ')
  return { day: (WEEKDAYS as readonly string[]).includes(day ?? '') ? (day as Weekday) : 'Sun', time: time ?? '' }
}

/** Analysis slots in Baghdad minutes: first + k × interval up to and including last (ATL-229). */
export function slots(first: string, last: string, interval: number): number[] {
  const a = hm(first)
  const b = hm(last)
  if (!Number.isFinite(a) || !Number.isFinite(b) || !Number.isInteger(interval) || interval < INTERVAL.min || interval > INTERVAL.max || b <= a) return []
  const out: number[] = []
  for (let m = a; m <= b; m += interval) out.push(m)
  return out
}

/** "08:10, 08:40 … 22:40": the first two slots, an ellipsis, the last; all of them when three or fewer. */
export function previewTimes(list: readonly number[]): string {
  if (list.length === 0) return ''
  if (list.length <= 3) return list.map(clock).join(', ')
  return `${clock(list[0]!)}, ${clock(list[1]!)} … ${clock(list[list.length - 1]!)}`
}

export const crossesMidnight = (start: string, end: string) => Number.isFinite(hm(start)) && Number.isFinite(hm(end)) && hm(end) < hm(start)

export function inQuiet(m: number, start: string, end: string): boolean {
  const a = hm(start)
  const b = hm(end)
  return a < b ? m >= a && m < b : m >= a || m < b
}

/* ---------------------------------------------------------------- typing */

/** Arabic-Indic digits typed on an Arabic keyboard become 0–9. */
export const western = (s: string) =>
  s.replace(/[٠-٩]/g, (c) => String(c.charCodeAt(0) - 0x660)).replace(/[۰-۹]/g, (c) => String(c.charCodeAt(0) - 0x6f0))

/** "7:30", "0730", "730", "7" → "07:30" / "07:00"; null when it is not a time. */
export function parseTime(text: string): string | null {
  const v = western(text).trim().replace(/[.٫،,]/g, ':')
  const m = /^(\d{1,2}):(\d{2})$/.exec(v) ?? /^(\d{2})(\d{2})$/.exec(v) ?? /^(\d)(\d{2})$/.exec(v) ?? /^(\d{1,2})$/.exec(v)
  if (!m) return null
  const h = Number(m[1])
  const mm = m[2] !== undefined ? Number(m[2]) : 0
  if (h > 23 || mm > 59) return null
  return `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

/** One interval stepper press: snap to the 15-minute grid, inside 15–240. */
export function steppedInterval(current: number, dir: 1 | -1): number {
  const v = Number.isFinite(current) ? current : DEFAULTS.analysis_interval_minutes
  return Math.min(INTERVAL.max, Math.max(INTERVAL.min, (Math.round(v / INTERVAL.step) + dir) * INTERVAL.step))
}

/* ---------------------------------------------------------------- draft */

/** The page's editable state: the values, plus the text of any time field that does not parse yet. */
export interface Draft {
  values: Schedule
  /** interval as typed */
  interval: string
  raw: Partial<Record<TimeField, string>>
}

export const draftFrom = (s: Schedule): Draft => ({ values: { ...s }, interval: String(s.analysis_interval_minutes), raw: {} })

export function intervalOf(d: Draft): number | null {
  const t = western(d.interval).trim()
  return /^\d+$/.test(t) ? Number(t) : null
}

export type ClientError = 'time' | 'every'

/**
 * What the page refuses before sending: only text that is not a time or not a number at all.
 * Order and range rules (watchlist after the 07:30 jobs, first slot before last, 15–240 minutes,
 * quiet start ≠ end) are the server's (ATL-228): its 422 message shows under the field.
 */
export function clientErrors(d: Draft, touched: ReadonlySet<string>): Partial<Record<Field, ClientError>> {
  const e: Partial<Record<Field, ClientError>> = {}
  for (const k of Object.keys(d.raw) as TimeField[]) if (touched.has(k)) e[k] = 'time'
  if (intervalOf(d) === null) e.analysis_interval_minutes = 'every'
  return e
}

/** Fields whose text cannot be sent yet (Save stays off until they are fixed). */
export const unsendable = (d: Draft): number => Object.keys(d.raw).length + (intervalOf(d) === null ? 1 : 0)

/** The values as they will be sent: the typed interval as a number. */
export function effective(d: Draft): Schedule {
  const iv = intervalOf(d)
  return { ...d.values, analysis_interval_minutes: iv ?? Number.NaN }
}

/** The PATCH `changes` (schedule.* keys) that turn the saved schedule into the draft. */
export function changesFor(d: Draft, saved: Schedule): SettingsChanges {
  const now = effective(d)
  const out: Record<string, string | number | boolean> = {}
  for (const f of FIELDS) {
    if (f === 'analysis_interval_minutes') {
      const iv = intervalOf(d)
      if (iv !== saved.analysis_interval_minutes) out[settingKey(f)] = iv ?? d.interval
      continue
    }
    if (now[f] !== saved[f]) out[settingKey(f)] = now[f]
  }
  return out as SettingsChanges
}

export const changedFields = (d: Draft, saved: Schedule): Field[] =>
  FIELDS.filter((f) => settingKey(f) in changesFor(d, saved))

/** A server 422 field (`schedule.analysis_last_slot`) as the page's field name. */
export function scheduleErrors(byKey: Partial<Record<string, string>>): Partial<Record<Field, string>> {
  const out: Partial<Record<Field, string>> = {}
  for (const [k, msg] of Object.entries(byKey)) {
    if (!msg || !k.startsWith('schedule.')) continue
    const f = k.slice('schedule.'.length) as Field
    if ((FIELDS as readonly string[]).includes(f)) out[f] = msg
  }
  return out
}

/* ---------------------------------------------------------------- zones */

/** The zone's UTC offset in minutes at that moment (from Intl's "GMT+3:30"). */
export function offsetMin(zone: string, at: number): number {
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' })
      .formatToParts(new Date(at))
      .find((p) => p.type === 'timeZoneName')?.value
    const m = /GMT([+-−])(\d{1,2})(?::(\d{2}))?/.exec(part ?? '')
    if (!m) return 0
    const sign = m[1] === '+' ? 1 : -1
    return sign * (Number(m[2]) * 60 + Number(m[3] ?? 0))
  } catch {
    return 0
  }
}

/** Minutes the owner's zone is ahead of Baghdad (negative = behind). */
export const diffFromTeam = (zone: string, at: number) => offsetMin(zone, at) - offsetMin(TEAM_TZ, at)

/** "Europe/London" → "London"; "America/Argentina/Buenos_Aires" → "Buenos Aires". */
export const zoneCity = (zone: string) => (zone.split('/').pop() ?? zone).replace(/_/g, ' ')

/** The same moment in the owner's zone: the time and whether it falls a day later or earlier. */
export function inZone(time: string, diff: number): { time: string; shift: -1 | 0 | 1 } | null {
  const m = hm(time)
  if (!Number.isFinite(m)) return null
  const t = m + diff
  return { time: clock(t), shift: t >= DAY_MIN ? 1 : t < 0 ? -1 : 0 }
}

export function shiftDay(day: Weekday, shift: -1 | 0 | 1): Weekday {
  const i = WEEKDAYS.indexOf(day)
  return WEEKDAYS[(i + shift + 7) % 7]!
}

/* ---------------------------------------------------------------- the US weekend */

/**
 * Friday 20:00 to Sunday 20:00 New York (ATL-229 Risk Engine window) for the coming weekend,
 * as instants; each end is converted with its own day's New York offset.
 */
export function usWeekend(now: number): { from: number; to: number } {
  const NY = 'America/New_York'
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', { timeZone: NY, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' })
      .formatToParts(new Date(now))
      .map((p) => [p.type, p.value]),
  )
  const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(parts.weekday ?? 'Mon')
  const add = (5 - dow + 7) % 7
  const at = (dd: number, hh: number) => {
    const wall = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + dd, hh)
    return wall - offsetMin(NY, wall + 5 * 3600e3) * 60e3
  }
  return { from: at(add, 20), to: at(add + 2, 20) }
}
