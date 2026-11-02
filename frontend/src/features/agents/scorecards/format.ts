import { FIGURE_LOCALE, type Lang } from '../../../lib/format'
import type { MetricSpec, Weight } from './model'

/*
 * Figures on the scorecards. Scorer values are JSON numbers (rates, R, seconds), none of them
 * money, so they format straight through Intl. Minus is U+2212, as everywhere in Atlas.
 */

const cache = new Map<string, Intl.NumberFormat>()
function nf(lang: Lang, o: Intl.NumberFormatOptions): Intl.NumberFormat {
  const k = `${lang}|${JSON.stringify(o)}`
  let f = cache.get(k)
  if (!f) {
    f = new Intl.NumberFormat(FIGURE_LOCALE[lang], o)
    cache.set(k, f)
  }
  return f
}
const minus = (s: string) => s.replace(/-/g, '−')

export const fmtPct = (v: number, lang: Lang, digits = 0) =>
  minus(nf(lang, { style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v))
export const fmtInt = (v: number, lang: Lang) => nf(lang, { maximumFractionDigits: 0 }).format(Math.round(v))
export const fmtFixed = (v: number, lang: Lang, digits: number) =>
  minus(nf(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v))
const fmtSigned = (v: number, lang: Lang, digits: number) =>
  minus(nf(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: 'exceptZero' }).format(v))

export const fmtR = (v: number, lang: Lang) => `${fmtSigned(v, lang, 2)}R`
export const fmtAtr = (v: number, lang: Lang) => `${fmtSigned(v, lang, 2)} ATR`
export const fmtWeight = (v: number, lang: Lang) => `${fmtFixed(v, lang, 2)}×`
export const fmtBrier = (v: number, lang: Lang) => fmtFixed(v, lang, 3)

/**
 * A metric's figure as plain text (aria labels, the LTR island of a value). Seconds are the bare
 * number: the unit is added by the caller in the sentence's own direction (design §4).
 */
export function figureText(spec: MetricSpec, v: number, lang: Lang): string {
  switch (spec.unit) {
    case 'pct':
      return fmtPct(v, lang)
    case 'sec':
    case 'count':
      return fmtInt(v, lang)
    case 'atr':
      return fmtAtr(v, lang)
    case 'r':
      return fmtR(v, lang)
    case 'cal':
      return fmtBrier(v, lang)
    default:
      return fmtFixed(v, lang, 2)
  }
}

/** "26 of 42 votes right", "8 scored votes, needs 10". */
export function weightDetail(w: Weight, t: (k: string, o?: Record<string, unknown>) => string, lang: 'en' | 'ar'): string {
  if (w.n === null || w.n === 0) return t('w.none')
  if (w.few) return t('w.few', { n: fmtInt(w.n, lang) })
  return t('w.right', { right: fmtInt(w.right ?? 0, lang), n: fmtInt(w.n, lang) })
}
