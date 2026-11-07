import { useTranslation } from 'react-i18next'
import { formatFixed, formatSigned, type Lang } from '../../lib/format'

/* Formats shared by the Historian cards (HistoryVerdictCard, RegimeTableCard). */

/** A signed return given as a fraction: 0.0094 → "+0.94%", −0.0383 → "−3.83%". */
export const pctSigned = (v: number, lang: Lang) => formatSigned(v * 100, 'percent', lang)
/** A 0–100 share → "58%". */
export const pctWhole = (v: number, lang: Lang) => `${formatFixed(v, 0, lang)}%`
/** A 0–1 probability → "71%". */
export const probability = (p: number, lang: Lang) => `${formatFixed(p * 100, 0, lang)}%`

/** days_in_regime → "6 days", "10 hours", "under an hour". */
export function useSpanText() {
  const { t } = useTranslation('warroom')
  return (d: number) => {
    if (d < 1 / 24) return t('rg.underHour')
    if (d < 1) return t('rg.hours', { count: Math.round(d * 24) })
    return t('rg.days', { count: Math.round(d) })
  }
}

