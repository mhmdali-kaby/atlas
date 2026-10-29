import { Tooltip } from 'radix-ui'
import type { ReactElement, ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { formatAgo, formatClock, formatDayTime } from '../portfolio/time'
import type { ApiSource } from './model'

export const NS = 'settings-data-sources'

export function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** A figure, count, time, host or error: an LTR island (design §8). */
export function Fig({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `fig ${className}` : 'fig'}>
      {children}
    </bdi>
  )
}

const COMPONENTS: Record<string, ReactElement> = {
  f: <Fig />,
  n: <bdi />,
  cmd: <code className="src-cmd" dir="ltr" />,
}

/** A translated sentence whose <f>, <n> and <cmd> tags become LTR islands, name isolates and command chips. */
export function Tr({ k, values, count }: { k: string; values?: Record<string, unknown>; count?: number }) {
  const { t } = useTranslation(NS)
  return <Trans t={t} i18nKey={k} values={values} count={count} components={COMPONENTS} />
}

/** The source's translated name by key (design §8), else the API name. */
export function useSourceName() {
  const { t } = useTranslation(NS)
  return (s: Pick<ApiSource, 'key' | 'name'>) => t(`name.${s.key.replace(/\./g, '_')}`, { defaultValue: s.name })
}

const sameBaghdadDay = (a: number, b: number) => {
  const f = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Baghdad' })
  return f.format(new Date(a)) === f.format(new Date(b))
}

export function useTimes(now: number) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return {
    /** "14:35", or a date and time when it is not today in Baghdad. */
    when: (ts: number) => (sameBaghdadDay(ts, now) ? formatClock(ts, lang, false) : formatDayTime(ts, lang)),
    /** "just now" under a minute, else "2 minutes ago". */
    ago: (ts: number) => (now - ts < 60_000 ? t('justNow') : formatAgo(now - ts, lang)),
    /** "within 5 min" from a poll interval in seconds. */
    within: (sec: number) => {
      if (sec >= 86_400) return t('within.day')
      if (sec >= 3_600) return t('within.hour')
      const m = Math.max(1, Math.round(sec / 60))
      return t('within.min', { count: m })
    },
    clock: (ts: number, seconds = false) => formatClock(ts, lang, seconds),
  }
}

/** The design's hover/focus tooltip (Phase 0 popover). Wrap a focusable or hoverable child. */
export function Tip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content side="top" sideOffset={6} className="src-pop vibrancy-popover">
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

/** CSS-safe id fragment for a source key ("rss.coindesk" → "rss-coindesk"). */
export const cssKey = (k: string) => k.replace(/[^a-z0-9]/gi, '-')
