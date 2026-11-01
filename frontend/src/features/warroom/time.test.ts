import { describe, expect, it } from 'vitest'
import { mmss } from './time'

describe('mmss', () => {
  it('rounds seconds up, so 0:00 never shows while time remains', () => {
    expect(mmss(354_000)).toBe('5:54')
    expect(mmss(353_001)).toBe('5:54')
    expect(mmss(59_999)).toBe('1:00')
    expect(mmss(1)).toBe('0:01')
    expect(mmss(-5)).toBe('0:00')
  })
})
