import { useEffect, useState } from 'react'
import { useApiQuery } from '../../lib/api'
import type { SettingsChanges, SettingsDoc } from '../onboarding'

/*
 * Settings → Money limits (brief §14.4, ATL-130; design js/pages/settings-money.js).
 * Every field is kept as the text the owner typed, so nothing is re-rounded while they edit.
 * Client checks mirror the server's (UpdateSettingsRequest + SettingsSchema, ATL-124):
 * USDT fields are decimal strings /^\d{1,15}(\.\d{1,8})?$/, more than 0 and at most
 * 1,000,000,000,000; the bot capital cap may be empty (null = no cap, decision 250);
 * max trades per day is an integer from 1 to 1,000.
 * Money maths here is integer cents in BigInt, never floats (only bar widths are ratios).
 */

export const MONEY_FIELDS = ['bot_capital_cap_usdt', 'max_usdt_per_trade', 'max_new_exposure_per_day_usdt', 'max_trades_per_day'] as const
export type MoneyField = (typeof MONEY_FIELDS)[number]
export type MoneyTexts = Record<MoneyField, string>

export const USDT_MAX = '1000000000000'
export const TRADES_MIN = 1
export const TRADES_MAX = 1000
const DECIMAL = /^\d{1,15}(\.\d{1,8})?$/

export const isInteger = (f: MoneyField) => f === 'max_trades_per_day'
export const isNullable = (f: MoneyField) => f === 'bot_capital_cap_usdt'

/** The preview's query params: the three USDT drafts the risk-preview endpoint takes (ATL-129). */
const PREVIEW_PARAMS: readonly MoneyField[] = ['bot_capital_cap_usdt', 'max_usdt_per_trade', 'max_new_exposure_per_day_usdt']

const AR_DIGITS = '٠١٢٣٤٥٦٧٨٩'
const BIDI = /[⁦-⁩‎‏؜]/g

/** What the owner typed with bidi marks, Arabic digits, separators and spaces normalised. */
export function clean(raw: string | number | null | undefined): string {
  return String(raw ?? '')
    .replace(BIDI, '')
    .replace(/[٠-٩]/g, (d) => String(AR_DIGITS.indexOf(d)))
    .replace(/٫/g, '.')
    .replace(/[,\s٬]/g, '')
}

export type FieldErrorKey = 'empty' | 'digits' | 'decimals' | 'min' | 'max' | 'int'

/** The locale key of the client error for this text, or null when the server would take it. */
export function fieldError(field: MoneyField, raw: string): FieldErrorKey | null {
  const s = clean(raw)
  if (isNullable(field) && s === '') return null
  if (isInteger(field)) {
    if (!/^\d{1,4}$/.test(s)) return 'int'
    const n = Number(s)
    return n < TRADES_MIN || n > TRADES_MAX ? 'int' : null
  }
  if (s === '') return 'empty'
  if (!/^\d+(\.\d+)?$/.test(s)) return 'digits'
  if (/\.\d{9,}$/.test(s)) return 'decimals'
  if (!DECIMAL.test(s)) return 'max'
  if (compareDecimal(s, '0') <= 0) return 'min'
  if (compareDecimal(s, USDT_MAX) > 0) return 'max'
  return null
}

/** Compares two non-negative decimal strings exactly. */
export function compareDecimal(a: string, b: string): number {
  const scale = 8
  const big = (s: string) => {
    const [i = '0', f = ''] = s.split('.')
    return BigInt(i + f.padEnd(scale, '0').slice(0, scale))
  }
  const x = big(a)
  const y = big(b)
  return x === y ? 0 : x < y ? -1 : 1
}

/** A decimal string as whole cents, rounded down ("2500.509" → 250050n); null when not a number. */
export function toCents(value: string | null | undefined): bigint | null {
  if (value === null || value === undefined) return null
  const s = clean(value).replace(/^-/, '')
  if (!/^\d+(\.\d+)?$/.test(s)) return null
  const [i = '0', f = ''] = s.split('.')
  const cents = BigInt(i) * 100n + BigInt(f.padEnd(2, '0').slice(0, 2))
  return value.trim().startsWith('-') ? -cents : cents
}

/** Cents as the decimal string the formatter takes ("1000.00"). */
export function fromCents(cents: bigint): string {
  const neg = cents < 0n
  const abs = neg ? -cents : cents
  return `${neg ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`
}

/** `pct`% of `cents`, rounded down to the cent. `pct` is a decimal string ("5", "1.0"). */
export function percentOf(cents: bigint, pct: string): bigint {
  const [i = '0', f = ''] = clean(pct).split('.')
  const scale = 10n ** BigInt(f.length)
  return (cents * BigInt(i + f)) / (scale * 100n)
}

/** The value as it sits in the field after blur: grouped, decimals only when there are any. */
export function groupText(field: MoneyField, raw: string): string {
  const s = clean(raw)
  if (s === '' || fieldError(field, raw)) return raw
  const [i = '0', f] = s.split('.')
  const grouped = BigInt(i).toLocaleString('en-US')
  return f ? `${grouped}.${f}` : grouped
}

/** The value as the API takes it: null for "no cap", an integer for trades, else a decimal string. */
export function apiValue(field: MoneyField, raw: string): string | number | null {
  const s = clean(raw)
  if (isNullable(field) && s === '') return null
  if (isInteger(field)) return Number(s)
  return s
}

const savedText = (doc: SettingsDoc, f: MoneyField): string => {
  const v = doc.settings[f] as string | number | null | undefined
  return v === null || v === undefined ? '' : String(v)
}

export function textsFrom(doc: SettingsDoc): MoneyTexts {
  return Object.fromEntries(MONEY_FIELDS.map((f) => [f, groupText(f, savedText(doc, f))])) as MoneyTexts
}

/** True when the typed text means the saved value (so "3,000" and "3000.00" are no change). */
export function sameAsSaved(field: MoneyField, raw: string, doc: SettingsDoc): boolean {
  const saved = clean(savedText(doc, field))
  const now = clean(raw)
  if (saved === '' || now === '') return saved === now
  if (fieldError(field, raw)) return false
  return compareDecimal(now, saved) === 0
}

export function changedFields(texts: MoneyTexts, doc: SettingsDoc): MoneyField[] {
  return MONEY_FIELDS.filter((f) => !sameAsSaved(f, texts[f], doc))
}

export function changesFor(texts: MoneyTexts, doc: SettingsDoc): SettingsChanges {
  const out: Record<string, string | number | null> = {}
  for (const f of changedFields(texts, doc)) out[f] = apiValue(f, texts[f])
  return out as SettingsChanges
}

export function clientErrors(texts: MoneyTexts): Partial<Record<MoneyField, FieldErrorKey>> {
  const out: Partial<Record<MoneyField, FieldErrorKey>> = {}
  for (const f of MONEY_FIELDS) {
    const e = fieldError(f, texts[f])
    if (e) out[f] = e
  }
  return out
}

/** The step for a USDT value: 10^(max(1, integer digits − 2)), so 3,000 steps by 100 (design §5.5). */
export function stepOf(cents: bigint): bigint {
  const whole = cents / 100n
  const digits = (whole > 0n ? whole : 1n).toString().length
  return 10n ** BigInt(Math.max(1, digits - 2)) * 100n
}

/** One press of + or −. From an empty cap, + starts at 100,000 and − does nothing. */
export function stepped(field: MoneyField, raw: string, dir: 1 | -1): string {
  if (isInteger(field)) {
    const n = fieldError(field, raw) ? TRADES_MIN : Number(clean(raw))
    return String(Math.min(TRADES_MAX, Math.max(TRADES_MIN, n + dir)))
  }
  if (isNullable(field) && clean(raw) === '') return dir > 0 ? '100,000' : raw
  const now = fieldError(field, raw) ? 100n : (toCents(raw) ?? 100n)
  const step = dir > 0 ? stepOf(now) : stepOf(now - 1n > 0n ? now - 1n : 1n)
  let next = dir > 0 ? (now / step) * step + step : ((now + step - 1n) / step) * step - step
  const max = BigInt(USDT_MAX) * 100n
  if (next < 100n) next = 100n
  if (next > max) next = max
  return groupText(field, fromCents(next).replace(/\.00$/, ''))
}

/** The button bounds: − stops at 1 USDT (typing may go lower), + at the server maximum. */
export function atBound(field: MoneyField, raw: string, dir: 1 | -1): boolean {
  if (fieldError(field, raw)) return false
  if (isInteger(field)) {
    const n = Number(clean(raw))
    return dir < 0 ? n <= TRADES_MIN : n >= TRADES_MAX
  }
  if (clean(raw) === '') return dir < 0
  const c = toCents(raw) ?? 0n
  return dir < 0 ? c <= 100n : c >= BigInt(USDT_MAX) * 100n
}

/* ------------------------------------------------------------ the preview (ATL-129) */

export type NotionalLimit = 'max_position_pct' | 'max_usdt_per_trade' | 'max_new_exposure_per_day'

export interface RiskPreview {
  env: 'demo' | 'live'
  settings_version: number
  equity_usd: string | null
  equity_as_of: string | null
  sizing_equity_usd: string | null
  cap_binds: boolean
  max_risk_usd: string | null
  risk_binding_limit: 'max_loss_per_trade_pct' | null
  max_notional_usd: string | null
  notional_binding_limit: NotionalLimit | null
  used_exposure_today_usd: string
  trades_today: number
  reason: 'no_equity' | null
}

/** The query string for a draft: only the fields that differ from the saved settings. */
export function previewParams(texts: MoneyTexts, doc: SettingsDoc, env: SettingsDoc['env'] = 'demo'): string {
  const params = new URLSearchParams({ env })
  for (const f of changedFields(texts, doc)) {
    if (!PREVIEW_PARAMS.includes(f)) continue
    const v = apiValue(f, texts[f])
    params.set(f, v === null ? '' : String(v))
  }
  return params.toString()
}

export const previewKey = (env: SettingsDoc['env'], qs: string) => ['settings', env, 'risk-preview', qs] as const

/** `value` once it has been still for `ms`; a null (an invalid draft) keeps the last value held. */
export function useDebounced(value: string | null, ms: number): string | null {
  const [held, setHeld] = useState(value)
  useEffect(() => {
    if (value === null) return
    const id = setTimeout(() => setHeld(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return held
}

export function useRiskPreview(qs: string | null, env: SettingsDoc['env'] = 'demo') {
  return useApiQuery<{ data: RiskPreview }>(previewKey(env, qs ?? ''), `/api/settings/risk-preview?${qs ?? ''}`, {
    enabled: qs !== null,
    placeholderData: (prev) => prev,
    staleTime: 15_000,
  })
}

/** One bar of the cut-line chart: a limit on the next trade's size, in cents. */
export interface CutRow {
  key: NotionalLimit
  cents: bigint
  binding: boolean
}

/** Rows in the design's fixed order (max per trade, left today, max position), so only the line moves. */
export function cutRows(p: RiskPreview, texts: MoneyTexts, doc: SettingsDoc): CutRow[] | null {
  const sizing = toCents(p.sizing_equity_usd)
  const use = toCents(p.max_notional_usd)
  if (sizing === null || use === null) return null
  const trade = fieldError('max_usdt_per_trade', texts.max_usdt_per_trade) ? null : toCents(texts.max_usdt_per_trade)
  const daily = fieldError('max_new_exposure_per_day_usdt', texts.max_new_exposure_per_day_usdt) ? null : toCents(texts.max_new_exposure_per_day_usdt)
  const used = toCents(p.used_exposure_today_usd) ?? 0n
  const left = daily === null ? null : daily - used > 0n ? daily - used : 0n
  const position = percentOf(sizing, String(doc.settings.max_position_pct))
  const rows: { key: NotionalLimit; cents: bigint | null }[] = [
    { key: 'max_usdt_per_trade', cents: trade },
    { key: 'max_new_exposure_per_day', cents: left },
    { key: 'max_position_pct', cents: position },
  ]
  return rows.map((r) => {
    const cents = r.key === p.notional_binding_limit ? use : (r.cents ?? use)
    return { key: r.key, cents, binding: r.key === p.notional_binding_limit || cents === use }
  })
}

/** The form row a binding limit belongs to; the profile's max position is not on this page. */
export const FIELD_OF_LIMIT: Partial<Record<NotionalLimit, MoneyField>> = {
  max_usdt_per_trade: 'max_usdt_per_trade',
  max_new_exposure_per_day: 'max_new_exposure_per_day_usdt',
}
