import { Fragment, type ReactNode } from 'react'
import { formatFixed, formatMoney, type Lang } from '../../lib/format'
import { fromCents } from './model'

/** Splits "{{a}} … {{b}}" on its placeholders and drops nodes into them (the template stays in the locale file). */
export function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>
  })
}

/** A figure as an LTR island with tabular numerals (design §12.1). */
export function Fig({ children, className, testId }: { children: ReactNode; className?: string; testId?: string }) {
  return (
    <bdi dir="ltr" className={`fig tabular-nums ${className ?? ''}`} data-testid={testId}>
      {children}
    </bdi>
  )
}

export const usd = (cents: bigint, lang: Lang) => formatMoney(fromCents(cents), 'USD', lang)

/** "1%", "5%", "0.5%": a settings percentage with only the decimals it has. */
export function pctText(value: string | number, lang: Lang): string {
  const decimals = (String(value).split('.')[1] ?? '').replace(/0+$/, '').length
  return `${formatFixed(String(value), decimals, lang)}%`
}
