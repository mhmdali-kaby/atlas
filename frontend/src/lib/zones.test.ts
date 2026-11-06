import { describe, expect, it } from 'vitest'
import { diffFromTeam, spanWords, zoneCity, zoneOffset } from './zones'

const strip = (s: string) => s.replace(/[⁦-⁩]/g, '')
const T = Date.UTC(2026, 9, 28, 12)

describe('zones', () => {
  it('names cities in both languages, Latin for zones outside the table', () => {
    expect(zoneCity('Europe/London', 'en')).toBe('London')
    expect(zoneCity('Europe/London', 'ar')).toBe('لندن')
    expect(zoneCity('Pacific/Port_Moresby', 'ar')).toBe('Port Moresby')
  })

  it('writes offsets as figures with a true minus', () => {
    expect(strip(zoneOffset('Asia/Kolkata', 'en', T))).toBe('GMT+5:30')
    expect(strip(zoneOffset('America/New_York', 'en', T))).toBe('GMT−4')
    expect(strip(zoneOffset('Asia/Dubai', 'ar', T))).toBe('غرينتش +4')
  })

  it('measures the owner against the team clock', () => {
    expect(diffFromTeam('Europe/London', T)).toBe(-180)
    expect(diffFromTeam('Asia/Dubai', T)).toBe(60)
  })

  it('spells spans with plural forms', () => {
    expect(strip(spanWords(-180, 'en'))).toBe('3 hours')
    expect(strip(spanWords(150, 'en'))).toBe('2 hours 30 minutes')
    expect(spanWords(60, 'ar')).toBe('ساعة واحدة')
    expect(spanWords(120, 'ar')).toBe('ساعتين')
    expect(strip(spanWords(180, 'ar'))).toBe('3 ساعات')
    expect(strip(spanWords(660, 'ar'))).toBe('11 ساعة')
  })
})
