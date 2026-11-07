import { Activity, CircleDot, Minus, MoveHorizontal, TrendingDown, TrendingUp, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { useNow } from '../portfolio/hooks'
import { KNOWN_REGIMES } from './historian'
import { fillSlots, slot } from './slots'
import { baghdadDay, formatDate, formatFullTime, formatTime } from './time'

/* Small pieces shared by the Historian cards (HistoryVerdictCard, RegimeTableCard). */

/** Marks a card figure when its path is untraced. */
export type MarkFn = (path: string, node: ReactNode) => ReactNode

export function Fig({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `fig num ${className}` : 'fig num'}>
      {children}
    </bdi>
  )
}

/** "Data as of 14:33" today in Baghdad, "Data as of 27 Oct, 18:40" on another day. */
export function AsOf({ ts, lang }: { ts: string; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const now = useNow(60_000)
  const today = baghdadDay(ts) === baghdadDay(now)
  const when = today ? formatTime(ts, lang) : `${formatDate(ts, lang)}${lang === 'ar' ? '، ' : ', '}${formatTime(ts, lang)}`
  return (
    <time className="hv-asof" dateTime={ts} tabIndex={0} title={formatFullTime(ts, lang)} data-testid="hv-asof">
      {fillSlots(t('hv.asOf', { time: slot('time') }), { time: <span className="num">{when}</span> })}
    </time>
  )
}

// ----------------------------------------------------------------------------------- regime

const REGIME_ICON: Record<string, { Icon: LucideIcon; dir?: boolean }> = {
  trend_up: { Icon: TrendingUp, dir: true },
  trend_down: { Icon: TrendingDown, dir: true },
  range: { Icon: MoveHorizontal },
  high_vol: { Icon: Activity },
  low_vol: { Icon: Minus },
}

/** A regime label with its icon; an unknown label is shown raw in an LTR island. */
export function RegimeLabel({ label }: { label: string }) {
  const { t } = useTranslation('warroom')
  const { Icon, dir } = REGIME_ICON[label] ?? { Icon: CircleDot }
  const known = KNOWN_REGIMES.has(label) || label === 'no_data' || label === 'insufficient_data'
  return (
    <span className="hv-rg">
      <Icon aria-hidden="true" strokeWidth={1.5} className={dir ? 'icon-dir' : undefined} />
      {known ? <span>{t(`rg.label.${label}`)}</span> : <bdi dir="ltr">{label}</bdi>}
    </span>
  )
}

export function RuleBased() {
  const { t } = useTranslation('warroom')
  return (
    <span className="hv-rule" tabIndex={0} title={t('rg.ruleTip')}>
      {t('rg.rule')}
    </span>
  )
}
