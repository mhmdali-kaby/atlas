import { CircleAlert, CircleCheck, CirclePause, Clock } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import type { ReactElement, ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Spinner } from '../../ui'
import { formatClock, formatDay } from '../portfolio/time'
import { DATASET, type DatasetKey, type Status } from './model'

export const NS = 'settings-data'

export function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** A figure, percent, count, size, time or code: always an LTR island (design §8). */
export function Fig({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `fig ${className}` : 'fig'}>
      {children}
    </bdi>
  )
}

const CMD = <code className="dt-cmd" dir="ltr" />

/** A translated sentence whose <f>, <b> and <cmd> tags become LTR islands, bold and command chips. */
export function Tr({ k, values, count }: { k: string; values?: Record<string, unknown>; count?: number }) {
  const { t } = useTranslation(NS)
  const components: Record<string, ReactElement> = { f: <Fig />, b: <b />, cmd: CMD }
  return <Trans t={t} i18nKey={k} values={values} count={count} components={components} />
}

/** BTC-USDT-SWAP with the base in full weight and the rest secondary (design §5.6). */
export function InstId({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span className="inst-id" dir="ltr">
      <span className="inst-base">{cut < 0 ? id : id.slice(0, cut)}</span>
      {cut >= 0 && <span className="inst-rest">{id.slice(cut)}</span>}
    </span>
  )
}

export function StatusIcon({ status }: { status: Status }) {
  if (status === 'done') return <CircleCheck aria-hidden="true" strokeWidth={2} />
  if (status === 'fetching') return <Spinner className="dt-spin" />
  if (status === 'failed') return <CircleAlert aria-hidden="true" strokeWidth={2} />
  if (status === 'paused') return <CirclePause aria-hidden="true" strokeWidth={2} />
  return <Clock aria-hidden="true" strokeWidth={2} />
}

export function StatusPill({ status }: { status: Status }) {
  const { t } = useTranslation(NS)
  return (
    <span className={`dt-status is-${status}`} data-status={status}>
      <StatusIcon status={status} />
      <span>{t(`st.${status}`)}</span>
    </span>
  )
}

/** The design's hover/focus tooltip (Phase 0 popover). Wrap a focusable child. */
export function Tip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content side="top" sideOffset={6} className="dt-tip vibrancy-popover">
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

/** "2 years", "180 days" or "Full history", from a dataset's configured depth. */
export function useDepthText() {
  const { t } = useTranslation(NS)
  return (key: DatasetKey, depthDays: number | null): string => {
    if (DATASET[key].open) return t('depth.open')
    if (depthDays === null) return t('depth.full')
    return depthDays % 365 === 0
      ? t('depth.years', { count: depthDays / 365 })
      : t('depth.days', { count: depthDays })
  }
}

const BAGHDAY = (ms: number) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Baghdad' }).format(new Date(ms))

export function useTimeText() {
  const lang = useLang()
  const comma = lang === 'ar' ? '، ' : ', '
  /** A point in history: a date (with the year when it isn't this year), plus the time for intraday bars. */
  const when = (ts: string | number, withTime: boolean, nowMs: number): ReactNode => {
    const ms = typeof ts === 'number' ? ts : Date.parse(ts)
    const sameYear = new Date(ms).getUTCFullYear() === new Date(nowMs).getUTCFullYear()
    const date = formatDay(ms, lang, !sameYear)
    return withTime ? (
      <>
        {date}
        {comma}
        <Fig>{formatClock(ms, lang, false)}</Fig>
      </>
    ) : (
      date
    )
  }
  /** "28 Oct, 13:41–13:44", the range one LTR island. */
  const windowText = (from: string, to: string): ReactNode => {
    const a = Date.parse(from)
    const b = Date.parse(to)
    const range = a === b ? formatClock(a, lang, false) : `${formatClock(a, lang, false)}–${formatClock(b, lang, false)}`
    return (
      <>
        {formatDay(a, lang)}
        {comma}
        <Fig>{range}</Fig>
      </>
    )
  }
  /** Time only today, date and time otherwise. */
  const shortWhen = (ts: string, nowMs: number): ReactNode => {
    const ms = Date.parse(ts)
    return BAGHDAY(ms) === BAGHDAY(nowMs) ? <Fig>{formatClock(ms, lang, false)}</Fig> : when(ms, true, nowMs)
  }
  return { when, windowText, shortWhen, comma, lang }
}
