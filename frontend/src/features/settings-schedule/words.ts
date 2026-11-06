import { useQuery } from '@tanstack/react-query'
import { api, type ApiError } from '../../lib/api'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { TEAM_TZ, WEEKDAYS, type Weekday } from './model'

export const NS = 'settings-schedule'
const LRI = '\u2066'
const PDI = '\u2069'
/** A time inside a sentence: an LTR isolate so "07:30" never reorders in Arabic. */
export const iso = (s: string | number) => `${LRI}${s}${PDI}`

/**
 * The owner's display zone. Reads GET /api/me/preferences (ATL-226) under the same query key
 * ATL-227's usePreferences uses; Baghdad until it answers or when it fails.
 */
export function useOwnerZone(): string {
  const q = useQuery<{ timezone?: string }, ApiError>({
    queryKey: ['me', 'preferences'],
    queryFn: async ({ signal }) => (await api.get<{ data: { timezone?: string } }>('/api/me/preferences', { signal })).data,
    staleTime: 60_000,
    retry: false,
  })
  const tz = q.data?.timezone
  if (!tz) return TEAM_TZ
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return tz
  } catch {
    return TEAM_TZ
  }
}

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
