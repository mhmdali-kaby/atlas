import { FIGURE_LOCALE, formatFixed, formatter, typographicMinus, type Lang } from '../../../lib/format'
import type { MetricSpec, Weight } from './model'

/*
 * Figures on the scorecards, built on src/lib/format (its formatter cache and U+2212 minus).
 * Scorer values are JSON numbers (rates, R, seconds), none of them money.
 */

/** A 0–1 rate as a percentage: 0.42 → "42%". */
export const fmtPct = (v: number, lang: Lang, digits = 0) =>
  typographicMinus(formatter(FIGURE_LOCALE[lang], { style: 'percent', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v))
export const fmtInt = (v: number, lang: Lang) => formatFixed(Math.round(v), 0, lang)
/** Fixed digits, signed except zero: "+0.42", "−1.10". */
const fmtSigned = (v: number, lang: Lang, digits: number) =>
  typographicMinus(
    formatter(FIGURE_LOCALE[lang], { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: 'exceptZero' }).format(v),
  )

export const fmtR = (v: number, lang: Lang) => `${fmtSigned(v, lang, 2)}R`
export const fmtAtr = (v: number, lang: Lang) => `${fmtSigned(v, lang, 2)} ATR`
export const fmtWeight = (v: number, lang: Lang) => `${formatFixed(v, 2, lang)}×`
export const fmtBrier = (v: number, lang: Lang) => formatFixed(v, 3, lang)

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
      return formatFixed(v, 2, lang)
  }
}

/** "26 of 42 votes right", "8 scored votes, needs 10". */
export function weightDetail(w: Weight, t: (k: string, o?: Record<string, unknown>) => string, lang: 'en' | 'ar'): string {
  if (w.n === null || w.n === 0) return t('w.none')
  if (w.few) return t('w.few', { n: fmtInt(w.n, lang) })
  return t('w.right', { right: fmtInt(w.right ?? 0, lang), n: fmtInt(w.n, lang) })
}

/** A metric's value as text for aria labels and target words; seconds get the unit word after the number. */
export const valueAria = (spec: MetricSpec, v: number, lang: Lang, unit: string) =>
  spec.unit === 'sec' ? `${fmtInt(v, lang)} ${unit}` : figureText(spec, v, lang)
