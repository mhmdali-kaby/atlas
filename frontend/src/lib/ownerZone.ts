import { create } from 'zustand'

/*
 * Two clocks (decision 255 (1), design v21 core.js "Two clocks").
 *
 * - The OWNER's zone (Settings → General, GET /api/me/preferences → timezone) is the zone every
 *   clock time Atlas prints is shown in. Read it with ownerZone() or useOwnerZone().
 * - The TEAM keeps Baghdad time: its schedule, its business day ("today's watchlist", Replay's
 *   date, daily limits) and Python stay on TEAM_TZ. Use TEAM_TZ only to find a day's edges, never
 *   to print a time.
 *
 * This file is the one place in src/ that names the team's zone.
 */
export const TEAM_TZ = 'Asia/Baghdad'

/** The zone a fresh owner gets (ATL-226's default), and the fallback before preferences load. */
export const DEFAULT_OWNER_TZ = TEAM_TZ

const ZONE_STORAGE_KEY = 'atlas.tz'

/** True when this browser's Intl knows the zone. */
export function isKnownZone(zone: unknown): zone is string {
  if (typeof zone !== 'string' || zone === '') return false
  try {
    new Intl.DateTimeFormat('en', { timeZone: zone })
    return true
  } catch {
    return false
  }
}

function readStoredZone(): string {
  try {
    const value = typeof window === 'undefined' ? null : window.localStorage.getItem(ZONE_STORAGE_KEY)
    return isKnownZone(value) ? value : DEFAULT_OWNER_TZ
  } catch {
    return DEFAULT_OWNER_TZ
  }
}

interface OwnerZoneState {
  zone: string
}

/** The owner's zone as a store, so a page that shows times re-renders when it changes. */
export const useOwnerZoneStore = create<OwnerZoneState>()(() => ({ zone: readStoredZone() }))

/** The owner's display zone now. Formatters call this on every format, so a change applies at once. */
export function ownerZone(): string {
  return useOwnerZoneStore.getState().zone
}

/** React: the owner's zone, re-rendering on change. */
export function useOwnerZone(): string {
  return useOwnerZoneStore((s) => s.zone)
}

/** Sets the display zone (and remembers it for the next page load). Unknown zones are ignored. */
export function setOwnerZone(zone: string): void {
  if (!isKnownZone(zone)) return
  try {
    window.localStorage.setItem(ZONE_STORAGE_KEY, zone)
  } catch {
    // Private mode: the zone lasts for this page only.
  }
  if (useOwnerZoneStore.getState().zone !== zone) useOwnerZoneStore.setState({ zone })
}

/** Minutes east of UTC for `zone` at instant `at` (DST-aware). */
export function zoneOffsetMin(zone: string, at: number = Date.now()): number {
  const part = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' })
    .formatToParts(new Date(at))
    .find((p) => p.type === 'timeZoneName')?.value
  const m = /GMT([+-])(\d{2}):(\d{2})/.exec(part ?? '')
  if (!m) return 0
  return (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3]))
}

/** Milliseconds east of UTC for the owner's zone at `at`. */
export function ownerOffsetMs(at: number = Date.now()): number {
  return zoneOffsetMin(ownerZone(), at) * 60_000
}

/** "2026-10-28": the team's (Baghdad) calendar day of an instant. */
const teamDayFormat = new Intl.DateTimeFormat('en-CA', { timeZone: TEAM_TZ })
export function teamDay(at: number): string {
  return teamDayFormat.format(new Date(at))
}

/** "2026-10-28": the owner's calendar day of an instant. */
export function ownerDay(at: number): string {
  return ownerDateFormat('en-CA', {}).format(new Date(at))
}

const cache = new Map<string, Intl.DateTimeFormat>()

/**
 * A cached Intl.DateTimeFormat in the owner's zone. The cache is keyed by zone, so a new zone
 * gets new formatters and the old ones are never reused.
 */
export function ownerDateFormat(locale: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const zone = ownerZone()
  const key = `${zone}|${locale}|${JSON.stringify(options)}`
  let f = cache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { ...options, timeZone: zone })
    cache.set(key, f)
  }
  return f
}
