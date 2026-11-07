import type { Holding } from './api'

/* Pure helpers for the Holdings and Cash flows pages (no floats touch a value). */

/**
 * Largest value first; a row whose value is unknown sinks to the bottom. The API already
 * sorts this way; sorting again keeps the page right if a poll ever returns another order.
 * Compared as decimals without floats: by integer-part length, then lexically, padded.
 */
export function byValueDesc(a: Holding, b: Holding): number {
  if (a.value_usd === null || b.value_usd === null) return a.value_usd === null ? (b.value_usd === null ? 0 : 1) : -1
  return compareDecimal(b.value_usd, a.value_usd)
}

function compareDecimal(x: string, y: string): number {
  const nx = x.trim().startsWith('-')
  const ny = y.trim().startsWith('-')
  if (nx !== ny) return nx ? -1 : 1
  const [xi = '', xf = ''] = x.trim().replace(/^[-+]/, '').split('.')
  const [yi = '', yf = ''] = y.trim().replace(/^[-+]/, '').split('.')
  const ai = xi.replace(/^0+(?=\d)/, '')
  const bi = yi.replace(/^0+(?=\d)/, '')
  const len = Math.max(xf.length, yf.length)
  const px = `${ai}.${xf.padEnd(len, '0')}`
  const py = `${bi}.${yf.padEnd(len, '0')}`
  const sign = ai.length !== bi.length ? Math.sign(ai.length - bi.length) : px === py ? 0 : px > py ? 1 : -1
  return nx ? -sign : sign
}

export type FlowState = 'completed' | 'pending' | 'failed'

/**
 * OKX's raw state code (ATL-11 stores it verbatim). "2" is success for both kinds;
 * deposit/withdrawal "-1" failed and withdrawal "-2" cancelled count as failed; every other
 * code (waiting, confirming, review, "-3" cancelling, …) is still pending.
 */
export function flowState(code: string): FlowState {
  if (code === '2') return 'completed'
  if (code === '-1' || code === '-2') return 'failed'
  return 'pending'
}

/** "0x9f3a…c41e": the head and tail of a long transaction id. */
export function shortTx(tx: string): string {
  return tx.length > 12 ? `${tx.slice(0, 6)}…${tx.slice(-4)}` : tx
}
