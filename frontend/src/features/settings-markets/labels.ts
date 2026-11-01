import { usdShort, type Eligibility } from './model'

/** Why an allowed instrument is still left out: the first failing filter, in the API's order. */
export function failText(e: Eligibility, t: (k: string, o?: Record<string, unknown>) => string): string | null {
  const code = e.fails[0]
  if (!code) return null
  if (code === 'asset_class_disabled' && e.market && e.market !== 'other') return t(`off.${e.market}`)
  if (code === 'asset_class_disabled') return t('fail.not_live')
  if (code === 'min_24h_volume') return e.min_24h_volume_usd ? t('fail.min_24h_volume', { min: usdShort(e.min_24h_volume_usd) }) : t('fail.min_24h_volume_plain')
  if (code === 'max_spread') return t('fail.max_spread', { v: `${e.spread_bps ?? '—'} bps`, max: `${e.max_spread_bps ?? '—'} bps` })
  return t(`fail.${code}`)
}
