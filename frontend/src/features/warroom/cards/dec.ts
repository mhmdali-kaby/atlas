/*
 * Exact decimal maths on the API's decimal strings (CLAUDE.md: money never passes through a
 * float). A value is a BigInt scaled by 10^SCALE. Used by the proposal card for the money at stake
 * and by the Edit sheet to compare stops and convert a USDT size to % of equity.
 */

const SCALE = 12
const TEN = 10n
const ONE = TEN ** BigInt(SCALE)
const DEC_RE = /^-?\d+(\.\d+)?$/

export type Dec = bigint

/** "248.60" → scaled BigInt; null for anything that is not a plain decimal. */
export function dec(v: string | number | null | undefined): Dec | null {
  if (v === null || v === undefined) return null
  const s = typeof v === 'number' ? (Number.isFinite(v) ? String(v) : '') : v.trim()
  if (!DEC_RE.test(s)) return null
  const neg = s.startsWith('-')
  const [int = '0', frac = ''] = (neg ? s.slice(1) : s).split('.')
  const f = (frac + '0'.repeat(SCALE)).slice(0, SCALE)
  const n = BigInt(int) * ONE + BigInt(f || '0')
  return neg ? -n : n
}

/** A scaled value back to a decimal string with at most `digits` decimals (rounded half up). */
export function decStr(n: Dec, digits = SCALE): string {
  const neg = n < 0n
  let a = neg ? -n : n
  if (digits < SCALE) {
    const step = TEN ** BigInt(SCALE - digits)
    a = ((a + step / 2n) / step) * step
  }
  const int = a / ONE
  const frac = (a % ONE).toString().padStart(SCALE, '0').slice(0, digits).replace(/0+$/, '')
  const out = frac ? `${int}.${frac}` : `${int}`
  return neg && out !== '0' ? `-${out}` : out
}

export const mul = (a: Dec, b: Dec): Dec => (a * b) / ONE
export const div = (a: Dec, b: Dec): Dec => (b === 0n ? 0n : (a * ONE) / b)
export const abs = (a: Dec): Dec => (a < 0n ? -a : a)
export const fromInt = (n: number): Dec => BigInt(n) * ONE

/** −1, 0 or 1. */
export function cmp(a: Dec, b: Dec): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** The most decimals among the strings: the tick precision when no spec is at hand. */
export function maxDecimals(values: Array<string | null | undefined>): number {
  let d = 0
  for (const v of values) {
    const i = v?.indexOf('.') ?? -1
    if (v && i >= 0) d = Math.max(d, v.length - i - 1)
  }
  return Math.min(d, 8)
}
