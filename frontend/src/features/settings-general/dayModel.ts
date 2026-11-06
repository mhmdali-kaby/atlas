import { TEAM_TZ, zoneOffsetMin } from '../../lib/ownerZone'
import { minuteOfDay } from '../../lib/zones'

/*
 * The team's day as numbers (design v21 js/dayline.js, owner-setup §10). Everything is computed
 * in team (Baghdad) minutes from the schedule.* settings (ATL-228), then shifted into the frame
 * the line is drawn in: the owner's day (General) or the team's (Schedule).
 */

export const DAY_MIN = 1440

/** The schedule.* keys the line reads, with ATL-228's defaults. */
export interface ScheduleValues {
  news_morning_brief: string
  historian_regime_table: string
  scout_watchlist: string
  pm_daily_report: string
  analysis_interval_minutes: number
  analysis_first_slot: string
  analysis_last_slot: string
  quiet_hours_enabled: boolean
  quiet_hours_start: string
  quiet_hours_end: string
}

export const SCHEDULE_DEFAULTS: ScheduleValues = {
  news_morning_brief: '07:30',
  historian_regime_table: '07:30',
  scout_watchlist: '08:00',
  pm_daily_report: '23:30',
  analysis_interval_minutes: 30,
  analysis_first_slot: '08:10',
  analysis_last_slot: '23:00',
  quiet_hours_enabled: false,
  quiet_hours_start: '23:00',
  quiet_hours_end: '07:00',
}

const HM = /^([01]\d|2[0-3]):([0-5]\d)$/

/** Reads the schedule.* flat keys of a settings doc, falling back to defaults for anything unusable. */
export function scheduleFrom(settings: Record<string, unknown> | undefined): ScheduleValues {
  const out: ScheduleValues = { ...SCHEDULE_DEFAULTS }
  if (!settings) return out
  for (const key of Object.keys(SCHEDULE_DEFAULTS) as (keyof ScheduleValues)[]) {
    const v = settings[`schedule.${key}`]
    const d = SCHEDULE_DEFAULTS[key]
    if (typeof d === 'string' && typeof v === 'string' && HM.test(v)) (out as unknown as Record<string, unknown>)[key] = v
    else if (typeof d === 'number' && Number.isFinite(Number(v)) && Number(v) > 0) (out as unknown as Record<string, unknown>)[key] = Number(v)
    else if (typeof d === 'boolean' && typeof v === 'boolean') (out as unknown as Record<string, unknown>)[key] = v
  }
  return out
}

export const hm = (s: string): number => {
  const [h, m] = s.split(':').map(Number)
  return (h ?? 0) * 60 + (m ?? 0)
}
export const mod = (m: number): number => ((m % DAY_MIN) + DAY_MIN) % DAY_MIN
const pad = (n: number) => String(n).padStart(2, '0')
/** "07:30" for a minute of the day (wraps). */
export const clock = (min: number): string => {
  const m = mod(Math.round(min))
  return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`
}

/** Analysis slots: first + k·interval, never after last. */
export function slots(v: ScheduleValues): number[] {
  const a = hm(v.analysis_first_slot)
  const b = hm(v.analysis_last_slot)
  const step = v.analysis_interval_minutes > 0 ? v.analysis_interval_minutes : 30
  const out: number[] = []
  for (let m = a; m <= b; m += step) out.push(m)
  return out
}

/** New York's cash session (09:30–16:00 America/New_York) in team minutes, for the day of `at`. */
export function usSession(at: number): [number, number] {
  const ny = zoneOffsetMin('America/New_York', at)
  const team = zoneOffsetMin(TEAM_TZ, at)
  return [mod(9 * 60 + 30 - ny + team), mod(16 * 60 - ny + team)]
}

export type PointKey = 'brief' | 'regime' | 'watchlist' | 'report'
export type Ring = 'news-intelligence' | 'historian' | 'market-scout' | 'portfolio-manager'

export interface DayPoint {
  key: PointKey
  /** Team minute. */
  m: number
  /** True when the brief and the regime table share a minute (one paired ring). */
  paired: boolean
  rings: Ring[]
}

export interface DayBand {
  key: 'us' | 'quiet'
  from: number
  to: number
}

export interface DayItems {
  points: DayPoint[]
  run: { from: number; to: number; slots: number[] } | null
  bands: DayBand[]
}

/** Everything on the line, in team minutes. */
export function dayItems(v: ScheduleValues, at: number): DayItems {
  const points: DayPoint[] = []
  const b = hm(v.news_morning_brief)
  const r = hm(v.historian_regime_table)
  if (b === r) points.push({ key: 'brief', m: b, paired: true, rings: ['news-intelligence', 'historian'] })
  else {
    points.push({ key: 'brief', m: b, paired: false, rings: ['news-intelligence'] })
    points.push({ key: 'regime', m: r, paired: false, rings: ['historian'] })
  }
  points.push({ key: 'watchlist', m: hm(v.scout_watchlist), paired: false, rings: ['market-scout'] })
  points.push({ key: 'report', m: hm(v.pm_daily_report), paired: false, rings: ['portfolio-manager'] })
  const sl = slots(v)
  const run = sl.length ? { from: sl[0] as number, to: sl[sl.length - 1] as number, slots: sl } : null
  const us = usSession(at)
  const bands: DayBand[] = [{ key: 'us', from: us[0], to: us[1] }]
  if (v.quiet_hours_enabled) bands.push({ key: 'quiet', from: hm(v.quiet_hours_start), to: hm(v.quiet_hours_end) })
  return { points, run, bands }
}

/** A span on the frame's 0–1440, split where it crosses the frame's midnight. */
export function segments(from: number, to: number): [number, number][] {
  if (to > from) return [[from, to]]
  if (to === from) return []
  return ([[from, DAY_MIN], [0, to]] as [number, number][]).filter(([a, c]) => c > a)
}

/**
 * The frame: `owner` draws the owner's 00:00–24:00, `team` draws Baghdad's. `diff` is how many
 * minutes the owner is ahead of the team (negative = behind).
 */
export function frameOf(frame: 'owner' | 'team', diff: number) {
  const shift = frame === 'team' ? 0 : diff
  return {
    /** team minute → frame minute */
    toFrame: (m: number) => mod(m + shift),
    /** frame minute → the other clock's minute */
    toOther: (f: number) => (frame === 'team' ? mod(f + diff) : mod(f - diff)),
  }
}

/** The current minute on the frame. */
export function nowOnFrame(at: number, frame: 'owner' | 'team', diff: number): number {
  return frameOf(frame, diff).toFrame(minuteOfDay(at, TEAM_TZ))
}

export interface LabelBox {
  /** Frame minute it points at. */
  f: number
  width: number
  run: boolean
  now: boolean
}

export interface LabelPlace {
  lane: number
  /** s = starts at its point, e = ends at its point, c = centred (the run), s0 = pinned to the start. */
  anchor: 's' | 'e' | 'c' | 's0'
}

/**
 * Labels sit above the line in lanes: each takes the lowest lane where it clears the labels
 * already placed by 8 px, starting 6 px before its point, or ending at it when it would run off
 * the line's end. Jobs first, then the run, then Now.
 */
export function placeLabels(boxes: LabelBox[], width: number): { places: LabelPlace[]; lanes: number } {
  const lanes: [number, number][][] = []
  const places: LabelPlace[] = boxes.map(() => ({ lane: 0, anchor: 's' }))
  const rank = (b: LabelBox) => (b.now ? 2 : b.run ? 1 : 0)
  const order = boxes.map((b, i) => ({ b, i })).sort((x, y) => rank(x.b) - rank(y.b) || x.b.f - y.b.f)
  let maxLane = 0
  for (const { b, i } of order) {
    const x = (b.f / DAY_MIN) * width
    let start = b.run ? x - b.width / 2 : x - 6
    let end = start + b.width
    let anchor: LabelPlace['anchor'] = b.run ? 'c' : 's'
    if (end > width) {
      end = b.run ? width : x + 6
      start = end - b.width
      anchor = 'e'
    }
    if (start < 0) {
      start = 0
      end = b.width
      anchor = 's0'
    }
    let lane = 0
    while ((lanes[lane] ?? []).some(([a, c]) => start < c + 8 && end > a - 8)) lane++
    ;(lanes[lane] ??= []).push([start, end])
    maxLane = Math.max(maxLane, lane)
    places[i] = { lane, anchor }
  }
  return { places, lanes: maxLane + 1 }
}
