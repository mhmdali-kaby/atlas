import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'
import { cx } from '../../ui/cx'
import { rText } from '../warroom/cards/rText'
import type { Outcome } from './api'
import { OUTCOME_ICON } from './format'
import type { MiniGeometry } from './model'

/** A figure: an LTR island with its unit inside (design §4). */
export function Fig({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={cx('fig num', className)}>
      {children}
    </bdi>
  )
}

/** An instrument id: the base in bold, the rest regular, always LTR. */
export function Inst({ id }: { id: string }) {
  const [base, ...rest] = id.split('-')
  return (
    <bdi dir="ltr" className="jr-inst">
      <b>{base}</b>
      {rest.length ? `-${rest.join('-')}` : ''}
    </bdi>
  )
}

/** Signed R with its direction icon and sign (never colour alone); "—" without a value. */
export function SignedR({ value, className, digits = 2 }: { value: string | null; className?: string; digits?: number }) {
  if (value === null) return <span className={cx('jr-signed is-none', className)}>—</span>
  const n = Number(value)
  const kind = Math.abs(n) < 0.005 ? 'zero' : n > 0 ? 'profit' : 'loss'
  const Icon = kind === 'profit' ? ArrowUpRight : ArrowDownRight
  return (
    <span className={cx('jr-signed', `is-${kind}`, className)} data-sign={kind}>
      {kind !== 'zero' && <Icon aria-hidden="true" strokeWidth={2.25} className="jr-dir" />}
      <Fig>{rText(value, digits)}</Fig>
    </span>
  )
}

/** The outcome badge: an arrow or "=" on its wash, mirrored in RTL. */
export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const Icon = OUTCOME_ICON[outcome]
  return (
    <span className="jr-row-out" aria-hidden="true">
      <Icon strokeWidth={2.25} />
    </span>
  )
}

const at = (a: number, b: number) => ({ '--a': a, '--b': b }) as CSSProperties
const x = (v: number) => ({ '--x': v }) as CSSProperties

/** The trade ladder in miniature (design §7): left to right in both languages. */
export function MiniLadder({ g, size = 'row' }: { g: MiniGeometry; size?: 'row' | 'wide' }) {
  return (
    <span className={cx('jr-mini', size === 'wide' && 'is-wide')} dir="ltr" aria-hidden="true">
      <i className="jr-mini-loss" style={at(g.loss[0], g.loss[1])} />
      <i className="jr-mini-gain" style={at(g.gain[0], g.gain[1])} />
      {g.band && <i className="jr-mini-band" style={at(g.band[0], g.band[1])} />}
      <i className="jr-mini-zero" style={x(g.zero)} />
      <i className="jr-mini-exit" style={x(g.exit)} />
    </span>
  )
}
