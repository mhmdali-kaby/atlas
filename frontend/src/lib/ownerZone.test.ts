import { afterEach, describe, expect, it } from 'vitest'
import { formatClock, formatDayTime } from '../features/portfolio/time'
import { baghdadDay, formatFullTime, formatTime } from '../features/warroom/time'
import { DEFAULT_OWNER_TZ, ownerZone, setOwnerZone, teamDay, TEAM_TZ, zoneOffsetMin } from './ownerZone'

/* ATL-227 AC3: every displayed time goes through the owner's zone; the team's day stays Baghdad. */

// 28 Oct 2026, 14:05:12 UTC: 17:05 in Baghdad (UTC+3), 14:05 in London (GMT, after the clocks went back).
const T = Date.UTC(2026, 9, 28, 14, 5, 12)

afterEach(() => setOwnerZone(DEFAULT_OWNER_TZ))

describe('owner zone', () => {
  it('defaults to the team clock', () => {
    expect(ownerZone()).toBe(TEAM_TZ)
    expect(formatTime(T, 'en')).toBe('17:05')
  })

  it('moves War Room and Portfolio times to Europe/London at once', () => {
    setOwnerZone('Europe/London')
    expect(ownerZone()).toBe('Europe/London')
    expect(formatTime(T, 'en')).toBe('14:05')
    expect(formatFullTime(T, 'en')).toContain('14:05:12')
    expect(formatClock(T, 'en')).toBe('14:05:12')
    expect(formatDayTime(T, 'en')).toContain('14:05')
    expect(formatTime(T, 'ar')).toBe('14:05')
  })

  it('ignores a zone the browser does not know', () => {
    setOwnerZone('Mars/Olympus')
    expect(ownerZone()).toBe(TEAM_TZ)
  })

  it('keeps the team day on Baghdad whatever the owner zone', () => {
    setOwnerZone('America/Los_Angeles')
    // 22:30 UTC on the 28th is 01:30 on the 29th in Baghdad, 15:30 on the 28th in Los Angeles.
    const late = Date.UTC(2026, 9, 28, 22, 30)
    expect(teamDay(late)).toBe('2026-10-29')
    expect(baghdadDay(late)).toBe('2026-10-29')
  })

  it('reads DST-aware offsets', () => {
    expect(zoneOffsetMin('Europe/London', Date.UTC(2026, 6, 1))).toBe(60)
    expect(zoneOffsetMin('Europe/London', T)).toBe(0)
    expect(zoneOffsetMin(TEAM_TZ, T)).toBe(180)
  })
})
