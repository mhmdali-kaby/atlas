import { Tooltip } from 'radix-ui'
import type { ReactElement, ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { formatClock, formatDayTime } from '../portfolio/time'
import { useLang } from '../portfolio/hooks'
import { dayOf } from './model'

export const NS = 'agents'

/** A figure, count, version or time: an LTR island (design .fig). */
export function Fig({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `fig num ${className}` : 'fig num'}>
      {children}
    </bdi>
  )
}

const BASE: Record<string, ReactElement> = {
  f: <Fig />,
  cmd: <code dir="ltr" />,
  t: <span className="ag-trig" />,
}

/** A translated sentence whose <f> become figures, <cmd> command chips and <t> unbroken phrases. */
export function Tr({
  k,
  values,
  components,
}: {
  k: string
  values?: Record<string, unknown>
  components?: Record<string, ReactElement>
}) {
  const { t } = useTranslation(NS)
  return <Trans t={t} i18nKey={k} values={values} components={{ ...BASE, ...components }} />
}

/** A small frosted tooltip on hover and keyboard focus (the trigger must be focusable). */
export function Tip({ label, children }: { label: string; children: ReactElement }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content side="top" sideOffset={6} className="ag-pop vibrancy-popover">
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

/** "14:32" today, "Yesterday, 14:32", else "Oct 26, 14:32" — Baghdad time; the full date for a tooltip. */
export function useWhen(now: number) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return {
    short: (ms: number) => {
      const day = dayOf(ms, now)
      if (day === 'today') return formatClock(ms, lang, false)
      if (day === 'yesterday') return t('yesterday', { time: formatClock(ms, lang, false) })
      return formatDayTime(ms, lang)
    },
    full: (ms: number) => formatDayTime(ms, lang, true),
  }
}
