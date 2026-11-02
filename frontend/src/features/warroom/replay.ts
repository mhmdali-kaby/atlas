import { useQuery } from '@tanstack/react-query'
import { api, type ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { baghdadDay } from './time'

/*
 * War Room Replay (ATL-206, brief §12): GET /api/warroom/replay?date=YYYY-MM-DD&env= returns every
 * message of one Baghdad day, oldest first (WarroomController::replay). A finished day never
 * changes, so it is fetched once and kept; its own query key means a live `.warroom.message` (which
 * upserts into ['warroom', 'messages', …]) can never append to it.
 */

export interface ReplayPayload {
  data: WarroomMessage[]
  meta: { date: string; timezone: string; from: string; to: string }
}

const DAY_MS = 86_400_000
const BAGHDAD_OFFSET_MS = 3 * 3_600_000
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

export const replayKey = (env: TradingEnv, date: string) => ['warroom', 'replay', env, date] as const

export function useReplay(env: TradingEnv, date: string) {
  return useQuery<ReplayPayload, ApiError>({
    queryKey: replayKey(env, date),
    queryFn: ({ signal }) => api.get<ReplayPayload>(`/api/warroom/replay?${new URLSearchParams({ date, env }).toString()}`, { signal }),
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}

/** The instant 00:00 Baghdad starts the day "2026-10-28" (Baghdad is UTC+3 all year). */
export function dayStartMs(day: string): number {
  return Date.parse(`${day}T00:00:00Z`) - BAGHDAD_OFFSET_MS
}

/** The Baghdad day `n` days after `day`. */
export function shiftDay(day: string, n: number): string {
  return baghdadDay(dayStartMs(day) + n * DAY_MS + 12 * 3_600_000)
}

/** The last day Replay shows: yesterday in Baghdad. Today is still live. */
export function lastReplayDay(now: number): string {
  return baghdadDay(now - DAY_MS)
}

/** A real calendar day before today in Baghdad. */
export function isReplayable(day: string | undefined, now: number): day is string {
  if (!day || !ISO_DAY.test(day) || Number.isNaN(dayStartMs(day))) return false
  // "2026-02-31" parses to a different day; only a day that round-trips is real.
  if (baghdadDay(dayStartMs(day)) !== day) return false
  return day < baghdadDay(now)
}

export function replayPath(day: string): string {
  return `/warroom/replay/${day}`
}

/** The kinds the day's moments list shows (design v18 Replay inspector), with their icon. */
export const MOMENT_KINDS = ['watchlist', 'proposal', 'order', 'fill', 'system_alert', 'post_mortem'] as const
export type MomentKind = (typeof MOMENT_KINDS)[number]
export const isMoment = (m: WarroomMessage): m is WarroomMessage & { kind: MomentKind } => (MOMENT_KINDS as readonly string[]).includes(m.kind)

const nameFmt = new Map<string, Intl.DateTimeFormat>()

/** "Monday 26 October" (design v18 Replay): a day's full name; the short form is "Mon 26 Oct". */
export function replayDayName(day: string, lang: Lang, short = false): string {
  const key = `${lang}|${short}`
  let f = nameFmt.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, {
      timeZone: 'Asia/Baghdad',
      weekday: short ? 'short' : 'long',
      day: 'numeric',
      month: short ? 'short' : 'long',
    })
    nameFmt.set(key, f)
  }
  return f.format(new Date(dayStartMs(day) + 12 * 3_600_000))
}


export interface ReplayDayCount {
  date: string
  count: number
}

/** Days the Replay sidebar lists under "Recent days" (design v18: the last seven finished days). */
export const RECENT_DAYS = 7

/**
 * The message count of each of the RECENT_DAYS Baghdad days ending `to` (newest first), from
 * GET /api/warroom/replay/days. Finished days don't change, so this is cached like the replay.
 */
export function useReplayDays(env: TradingEnv, to: string) {
  return useQuery<{ data: ReplayDayCount[] }, ApiError>({
    queryKey: ['warroom', 'replay-days', env, to] as const,
    queryFn: ({ signal }) =>
      api.get<{ data: ReplayDayCount[] }>(`/api/warroom/replay/days?${new URLSearchParams({ to, env, days: String(RECENT_DAYS) }).toString()}`, { signal }),
    staleTime: Infinity,
    gcTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}

/** The War Room sidebar sections that belong to Replay pages only (ATL-207), and those that don't. */
export const isReplayPath = (pathname: string) => pathname === '/warroom/replay' || pathname.startsWith('/warroom/replay/')
