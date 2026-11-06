import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { ownerDateFormat } from '../../lib/ownerZone'
import { formatTime } from '../warroom/time'
import { dayDiff } from './model'

export const NS = 'settings-backup'

/** Splits "{{a}} … {{b}}" on its placeholders and drops nodes into them. */
export function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>
  })
}

/** A figure, path or file name as an LTR island. */
export function Ltr({ children, className, testId }: { children: ReactNode; className?: string; testId?: string }) {
  return (
    <bdi dir="ltr" className={className} data-testid={testId}>
      {children}
    </bdi>
  )
}

export const Doctor = () => (
  <bdi dir="ltr">
    <code>./atlas doctor</code>
  </bdi>
)

const LRI = '⁦'
const PDI = '⁩'
export const isolate = (s: string) => `${LRI}${s}${PDI}`

/** "Tue, 27 Oct" / "Tuesday" in the owner's zone. */
export function dayName(at: number, lang: Lang, o: { long?: boolean; noDate?: boolean } = {}): string {
  return ownerDateFormat(DATE_LOCALE[lang], {
    weekday: o.long ? 'long' : 'short',
    day: o.noDate ? undefined : 'numeric',
    month: o.noDate ? undefined : 'short',
  }).format(new Date(at))
}

/** "Today at 03:00", "Yesterday at 03:00", "Monday at 03:00", "Tue 13 Oct at 03:00" (owner's zone). */
export function useWhen() {
  const { t } = useTranslation(NS)
  return (at: number, now: number, lang: Lang, lower = false): string => {
    const time = isolate(formatTime(at, lang))
    const diff = dayDiff(at, now)
    if (diff === 0) return t(lower ? 'when.todayLower' : 'when.today', { time })
    if (diff === 1) return t(lower ? 'when.tomorrowLower' : 'when.tomorrow', { time })
    if (diff === -1) return t('when.yesterday', { time })
    if (diff > -7 && diff < 0) return t('when.day', { day: dayName(at, lang, { long: true, noDate: true }), time })
    return t('when.day', { day: dayName(at, lang), time })
  }
}

export function copyText(text: string): void {
  try {
    void navigator.clipboard?.writeText(text).catch(() => undefined)
  } catch {
    /* no clipboard (insecure context): nothing to do */
  }
}
