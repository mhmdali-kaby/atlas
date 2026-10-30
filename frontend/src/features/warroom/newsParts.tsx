import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDecimal, formatFixed, type Lang } from '../../lib/format'
import { Fig } from './historianParts'

/*
 * Parts shared by the news alert card (ATL-65) and News Intelligence's brief card (ATL-116), so
 * sentiment and importance are drawn the same way in both. Styles: `.news-signed` and
 * `.news-pips` in warroom.css.
 */

/**
 * Sentiment −1…1 as a signed value: sign + arrow + a spoken word, never colour alone; |s| < 0.05 is
 * neutral. `wrap` lets a caller decorate the figure (the number guard's untraced mark).
 */
export function SignedSentiment({ value, lang, wrap = (n) => n }: { value: number; lang: Lang; wrap?: (node: ReactNode) => ReactNode }) {
  const { t } = useTranslation('warroom')
  const tone = value >= 0.05 ? 'profit' : value <= -0.05 ? 'loss' : 'zero'
  const text = tone === 'zero' ? formatFixed(0, 2, lang) : formatDecimal(value.toFixed(2), lang, 2, 'exceptZero')
  const Icon = tone === 'profit' ? ArrowUpRight : ArrowDownRight
  return (
    <span className={`signed news-signed is-${tone}`} data-sign={tone}>
      {tone !== 'zero' && <Icon aria-hidden="true" strokeWidth={2.25} className="icon-dir" />}
      {wrap(<Fig>{text}</Fig>)}
      <span className="sr-only"> {t(tone === 'profit' ? 'news.positive' : tone === 'loss' ? 'news.negative' : 'news.neutral')}</span>
    </span>
  )
}

/** Importance 1…5 as five rising bars; decorative, so the caller gives the spoken label. */
export function ImportancePips({ n }: { n: number }) {
  return (
    <span className="news-pips" aria-hidden="true">
      {[1, 2, 3, 4, 5].map((i) => (
        <i key={i} className={i <= n ? 'is-on' : undefined} style={{ height: 2 + i * 2 }} />
      ))}
    </span>
  )
}
