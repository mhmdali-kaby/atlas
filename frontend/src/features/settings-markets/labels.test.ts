import { describe, expect, it } from 'vitest'
import { failText } from './labels'
import type { Eligibility } from './model'

const base: Eligibility = {
  inst_id: 'XAU-USDT-SWAP',
  eligible: false,
  fails: [],
  market: 'commodity_perp',
  on_whitelist: true,
  volume_24h_usd: '1000000',
  min_24h_volume_usd: '5000000',
  spread_bps: '3',
  max_spread_bps: '8',
  notes: [],
}
const t = (k: string, o?: Record<string, unknown>) => (o ? `${k} ${JSON.stringify(o)}` : k)

describe('failText (ATL-131)', () => {
  it('is null for an eligible instrument', () => {
    expect(failText({ ...base, eligible: true }, t, 'demo')).toBeNull()
  })
  it('names only the first failing filter, blocked first', () => {
    expect(failText({ ...base, fails: ['blocked', 'min_24h_volume'] }, t, 'demo')).toBe('fail.blocked')
  })
  it('shows the minimum volume in short USD', () => {
    expect(failText({ ...base, fails: ['min_24h_volume'] }, t, 'demo')).toBe('fail.min_24h_volume {"min":"$5M"}')
    expect(failText({ ...base, min_24h_volume_usd: null, fails: ['min_24h_volume'] }, t, 'demo')).toBe('fail.min_24h_volume_plain')
  })
  it('uses the class-off line for a disabled class, and not_live when the market is unknown', () => {
    expect(failText({ ...base, fails: ['asset_class_disabled'] }, t, 'demo')).toBe('off.commodity_perp')
    expect(failText({ ...base, market: 'other', fails: ['asset_class_disabled'] }, t, 'demo')).toBe('fail.not_live')
  })
  it('gives spread and its limit in bps', () => {
    expect(failText({ ...base, spread_bps: '12', fails: ['max_spread'] }, t, 'demo')).toBe('fail.max_spread {"v":"12 bps","max":"8 bps"}')
  })
  it('names the active env’s universe for an unknown instrument (ATL-244)', () => {
    expect(failText({ ...base, fails: ['unknown_instrument'] }, t, 'demo')).toBe('fail.unknown_instrument.demo')
    expect(failText({ ...base, fails: ['unknown_instrument'] }, t, 'live')).toBe('fail.unknown_instrument.live')
  })
})
