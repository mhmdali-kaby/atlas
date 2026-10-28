import { DATE_LOCALE, FIGURE_LOCALE, formatMoney, formatPercent, formatSigned, NO_VALUE, signOf } from './format'

const MINUS = '−'
const WESTERN_ONLY = /^[^٠-٩۰-۹]*$/

describe('formatSigned', () => {
  it('formats a negative USD amount with the typographic minus in English', () => {
    expect(formatSigned('-1234.5', 'USD', 'en')).toBe(`${MINUS}$1,234.50`)
  })

  it('keeps Western digits and the sign before the amount in Arabic', () => {
    const ar = formatSigned('-1234.5', 'USD', 'ar')
    expect(ar).toMatch(WESTERN_ONLY)
    expect(ar).toContain('1,234.50')
    expect(ar.startsWith(MINUS)).toBe(true)
    expect(ar).toBe(`${MINUS}$1,234.50`)
  })

  it('puts "+" on a positive value, in both languages', () => {
    expect(formatSigned('2104.18', 'USD', 'en')).toBe('+$2,104.18')
    expect(formatSigned('2104.18', 'USD', 'ar')).toBe('+$2,104.18')
    expect(formatSigned('0.76', 'percent')).toBe('+0.76%')
  })

  it('gives zero no sign', () => {
    expect(formatSigned('0', 'USD')).toBe('$0.00')
    expect(formatSigned('-0.00', 'USD', 'ar')).toBe('$0.00')
    expect(formatSigned('0', 'percent')).toBe('0.00%')
  })

  it('keeps every digit of a long decimal string (no float rounding)', () => {
    expect(formatSigned('-9007199254740993.25', 'USD')).toBe(`${MINUS}$9,007,199,254,740,993.25`)
  })

  it('renders a missing value as an em dash, never as zero', () => {
    expect(formatSigned(null, 'USD')).toBe(NO_VALUE)
    expect(formatSigned(undefined, 'percent')).toBe(NO_VALUE)
    expect(formatSigned('', 'USD')).toBe(NO_VALUE)
  })
})

describe('formatMoney and formatPercent', () => {
  it('sign only negatives', () => {
    expect(formatMoney('5068412.56', 'USD')).toBe('$5,068,412.56')
    expect(formatMoney('-214.07', 'USD', 'ar')).toBe(`${MINUS}$214.07`)
    expect(formatPercent('-1.2')).toBe(`${MINUS}1.20%`)
    expect(formatPercent('12.345', 'ar')).toBe('12.35%')
    expect(formatMoney(null, 'USD')).toBe(NO_VALUE)
  })

  it('falls back to a code suffix for non-ISO currencies', () => {
    expect(formatMoney('1500.5', 'USDT')).toBe('1,500.50 USDT')
  })
})

describe('locale constants', () => {
  it('never use a bare "ar", and Arabic dates keep Western digits', () => {
    for (const locale of [...Object.values(FIGURE_LOCALE), ...Object.values(DATE_LOCALE)]) {
      expect(locale).not.toBe('ar')
    }
    expect(DATE_LOCALE.ar).toBe('ar-IQ-u-nu-latn')
    const date = new Intl.DateTimeFormat(DATE_LOCALE.ar, { day: 'numeric', year: 'numeric' }).format(
      new Date(Date.UTC(2026, 9, 28)),
    )
    expect(date).toMatch(WESTERN_ONLY)
    expect(date).toMatch(/2026/)
  })
})

describe('signOf', () => {
  it.each([
    ['-1234.5', -1],
    ['0', 0],
    ['-0.000', 0],
    ['0.01', 1],
    ['+3', 1],
    [-2, -1],
    [null, null],
    ['abc', null],
  ] as const)('%s → %s', (value, sign) => {
    expect(signOf(value)).toBe(sign)
  })
})
