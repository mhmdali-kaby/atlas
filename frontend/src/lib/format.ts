/*
 * Figures: money, percentages and signs (design §5, brief §15).
 *
 * One locale constant per language. Figures use en-US in both languages so a
 * number reads identically in English and Arabic (design §5 decision "Figures
 * render identically in Arabic and English", which supersedes formatting
 * figures with ar-IQ-u-nu-latn: that locale prints "1,234.50 US$" and its sign
 * lands after the number in an RTL paragraph). Dates use ar-IQ-u-nu-latn in
 * Arabic: Iraqi month names with Western digits. Never pass a bare "ar".
 *
 * Values arrive as decimal strings from the API and are handed to Intl as
 * strings, so no money ever passes through a float. `null` is "no value yet"
 * and renders as an em dash, never as zero.
 */

export type Lang = 'en' | 'ar'

/** Locale for money, percentages, counts and signs. */
export const FIGURE_LOCALE: Readonly<Record<Lang, string>> = {
  en: 'en-US',
  ar: 'en-US',
}

/** Locale for dates and times (shown in Asia/Baghdad, 24-hour clock). */
export const DATE_LOCALE: Readonly<Record<Lang, string>> = {
  en: 'en-US',
  ar: 'ar-IQ-u-nu-latn',
}

/** What a missing value renders as. */
export const NO_VALUE = '—'

/** A decimal as the API sends it ("-1234.5"), or a number from local maths. */
export type Figure = string | number | null | undefined

/** `'percent'` formats a value already in percent units: "0.76" → "0.76%". */
export type FigureUnit = string | 'percent'

const HYPHEN_MINUS = /-/g
const TYPOGRAPHIC_MINUS = '−'

const formatters = new Map<string, Intl.NumberFormat>()

function formatter(locale: string, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`
  let nf = formatters.get(key)
  if (!nf) {
    nf = new Intl.NumberFormat(locale, options)
    formatters.set(key, nf)
  }
  return nf
}

function isMissing(value: Figure): value is null | undefined {
  return value === null || value === undefined || (typeof value === 'string' && value.trim() === '')
}

/** Intl accepts decimal strings exactly (ES2023 StringNumericLiteral). */
function asIntlValue(value: string | number): Intl.StringNumericLiteral | number {
  return typeof value === 'number' ? value : (value.trim() as Intl.StringNumericLiteral)
}

/** The design's minus is U+2212; Intl's en-US data emits U+002D. */
function typographicMinus(text: string): string {
  return text.replace(HYPHEN_MINUS, TYPOGRAPHIC_MINUS)
}

function unitOptions(unit: FigureUnit): Intl.NumberFormatOptions {
  if (unit === 'percent') {
    // style "unit" does not multiply by 100, so "0.76" stays 0.76%.
    return { style: 'unit', unit: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2 }
  }
  return { style: 'currency', currency: unit, currencyDisplay: 'narrowSymbol' }
}

function format(value: Figure, unit: FigureUnit, lang: Lang, signDisplay: Intl.NumberFormatOptions['signDisplay']): string {
  if (isMissing(value)) return NO_VALUE
  const locale = FIGURE_LOCALE[lang]
  const options = { ...unitOptions(unit), signDisplay }
  let nf: Intl.NumberFormat
  try {
    nf = formatter(locale, options)
  } catch {
    // Not an ISO 4217 code (USDT, USDC): the amount, then the code as a unit.
    nf = formatter(locale, { minimumFractionDigits: 2, maximumFractionDigits: 8, signDisplay })
    return typographicMinus(`${nf.format(asIntlValue(value))} ${unit}`)
  }
  return typographicMinus(nf.format(asIntlValue(value)))
}

/** "$5,068,412.56", "−$214.07" (only a negative carries a sign). */
export function formatMoney(value: Figure, currency: string, lang: Lang = 'en'): string {
  return format(value, currency, lang, 'auto')
}

/** "0.76%", "−1.20%". `value` is already in percent units. */
export function formatPercent(value: Figure, lang: Lang = 'en'): string {
  return format(value, 'percent', lang, 'auto')
}

/**
 * Always signed except zero: "+$2,104.18", "−$1,234.50", "$0.00", "+0.76%".
 * The sign comes from Intl's signDisplay, never from string concatenation.
 */
export function formatSigned(value: Figure, unit: FigureUnit, lang: Lang = 'en'): string {
  return format(value, unit, lang, 'exceptZero')
}

/**
 * The sign of a decimal string without converting it to a float:
 * -1, 0 or 1, or null when there is no value.
 */
export function signOf(value: Figure): -1 | 0 | 1 | null {
  if (isMissing(value)) return null
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return null
    return value > 0 ? 1 : value < 0 ? -1 : 0
  }
  const text = value.trim()
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text)) return null
  const mantissa = text.replace(/e.*$/i, '')
  if (!/[1-9]/.test(mantissa)) return 0
  return text.startsWith('-') ? -1 : 1
}

/**
 * A plain decimal string, grouped en-US in both languages and never rounded: at least
 * `minFrac` fraction digits and every digit the API sent ("5099121.5" → "5,099,121.50").
 * `signDisplay: 'exceptZero'` adds "+" to positives; negatives always carry "−".
 */
export function formatDecimal(
  value: Figure,
  lang: Lang = 'en',
  minFrac = 2,
  signDisplay: Intl.NumberFormatOptions['signDisplay'] = 'auto',
): string {
  if (isMissing(value)) return NO_VALUE
  const nf = formatter(FIGURE_LOCALE[lang], { minimumFractionDigits: minFrac, maximumFractionDigits: 20, signDisplay })
  return typographicMinus(nf.format(asIntlValue(value)))
}

/**
 * A plain figure with a fixed number of decimals and no unit: "1.0", "5", "1,250.5".
 * Decimal strings go to Intl as strings, so nothing passes through a float.
 */
export function formatFixed(value: Figure, digits: number, lang: Lang = 'en'): string {
  if (isMissing(value)) return NO_VALUE
  const nf = formatter(FIGURE_LOCALE[lang], { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return typographicMinus(nf.format(asIntlValue(value)))
}
