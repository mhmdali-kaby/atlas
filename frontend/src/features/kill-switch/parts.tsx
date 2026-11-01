import type { ReactElement, ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'

export const NS = 'kill-switch'

/** A figure, count or time: an LTR island (design .fig, §12.1). */
export function Fig({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `num fig ${className}` : 'num fig'}>
      {children}
    </bdi>
  )
}

/** An instrument id: whole, left-to-right. */
export function Inst({ id }: { id: string }) {
  return (
    <bdi dir="ltr" className="ks-inst">
      {id}
    </bdi>
  )
}

/** A translated sentence whose <f> become figures. */
export function Tr({
  k,
  values,
  count,
  className,
}: {
  k: string
  values?: Record<string, unknown>
  /** Picks the plural form (and fills {{count}}). */
  count?: number
  className?: string
}) {
  const { t } = useTranslation(NS)
  const components: Record<string, ReactElement> = {
    f: <Fig className={className} />,
  }
  return <Trans t={t} i18nKey={k} count={count} values={count === undefined ? values : { count, ...values }} components={components} />
}

const SCALE = 10n ** 8n

/** Sums decimal strings exactly (no float drift); null when none is a number. */
export function sumDecimals(values: readonly (string | null | undefined)[]): string | null {
  let total = 0n
  let any = false
  for (const raw of values) {
    if (raw === null || raw === undefined) continue
    const m = /^\s*(-?)(\d*)(?:\.(\d*))?\s*$/.exec(raw)
    if (!m || (m[2] === '' && (m[3] ?? '') === '')) continue
    const frac = (m[3] ?? '').slice(0, 8).padEnd(8, '0')
    const v = BigInt(m[2] || '0') * SCALE + BigInt(frac)
    total += m[1] === '-' ? -v : v
    any = true
  }
  if (!any) return null
  const neg = total < 0n
  const abs = neg ? -total : total
  const frac = (abs % SCALE).toString().padStart(8, '0').replace(/0+$/, '')
  return `${neg ? '-' : ''}${abs / SCALE}${frac ? `.${frac}` : ''}`
}
