import type { WarroomMessage } from './api'

/*
 * The Replay day line's arithmetic (ATL-207; design v18 Replay "the day line"). Pure functions
 * over the day's messages, which arrive oldest first, so "what had been said by then" is a prefix
 * of the list found by binary search. Times are epoch milliseconds; the day runs from 00:00
 * Baghdad (dayStartMs) for 24 hours, and the playhead's last instant is 23:59:59.999.
 */

export const DAY_MS = 86_400_000
const MIN_MS = 60_000
/** The density strip's buckets: ten minutes each (design: 144 bars). */
export const BUCKETS = 144
/** Playback speeds (design: 1×, 10×, 60×). */
export const SPEEDS = [1, 10, 60] as const
export type Speed = (typeof SPEEDS)[number]

/** Every message's created_at as epoch ms, in list order. */
export const messageTimes = (messages: readonly WarroomMessage[]): number[] => messages.map((m) => Date.parse(m.created_at))

/** The playhead kept inside the day: [00:00, 23:59:59.999]. */
export function clampAt(at: number, dayStart: number): number {
  return Math.max(dayStart, Math.min(dayStart + DAY_MS - 1, at))
}

/** How many messages were created at or before `at` (times ascending). */
export function shownCount(times: readonly number[], at: number): number {
  let lo = 0
  let hi = times.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (times[mid]! <= at) lo = mid + 1
    else hi = mid
  }
  return lo
}

/**
 * The message an arrow key steps to: the first one after the playhead, or the last one before
 * the messages already shown at it. Null at either end of the day.
 */
export function stepIndex(times: readonly number[], at: number, dir: 1 | -1): number | null {
  const n = shownCount(times, at)
  if (dir > 0) return n < times.length ? n : null
  // The newest shown message sits at the playhead after a step; the one before it is the target.
  let i = n - 1
  while (i >= 0 && times[i]! >= at) i--
  return i >= 0 ? i : null
}

/** Messages per ten-minute bucket of the day; times outside the day are ignored. */
export function densityBuckets(times: readonly number[], dayStart: number): number[] {
  const out = new Array<number>(BUCKETS).fill(0)
  const size = DAY_MS / BUCKETS
  for (const t of times) {
    const b = Math.floor((t - dayStart) / size)
    if (b >= 0 && b < BUCKETS) out[b]! += 1
  }
  return out
}

/** A time of day as a percentage of the line from its start edge (inline-start). */
export const linePct = (at: number, dayStart: number): number => ((clampAt(at, dayStart) - dayStart) / DAY_MS) * 100

/** The playhead's minute of the day, for the slider's aria-valuenow (0–1439). */
export const minuteOfDay = (at: number, dayStart: number): number => Math.floor((clampAt(at, dayStart) - dayStart) / MIN_MS)

/** A pointer's position along the track as a time; `rtl` makes the line run right to left. */
export function atFromPointer(clientX: number, rect: { left: number; width: number }, rtl: boolean, dayStart: number): number {
  let f = rect.width > 0 ? (clientX - rect.left) / rect.width : 0
  if (rtl) f = 1 - f
  return clampAt(dayStart + Math.max(0, Math.min(1, f)) * DAY_MS, dayStart)
}
