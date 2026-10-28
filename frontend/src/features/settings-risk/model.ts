import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, useApiQuery } from '../../lib/api'
import { LIMIT_DEFS, PRESET_IDS, settingsKey, type PresetId, type RiskLimits, type RiskProfile, type SettingsChanges, type SettingsDoc } from '../onboarding'

/*
 * Settings → Risk & Autonomy (brief §14.3, §14.6; design js/pages/settings.js `risk`).
 * The draft keeps every limit as the text in its field, so nothing the owner types is lost or
 * re-rounded while they edit; the server is the judge of ranges (its 422 shows under the field).
 */

export type LimitField = keyof RiskLimits
export type LimitDef = (typeof LIMIT_DEFS)[number]

/** Stepper step and button bounds per limit (design data.js limitDefs). Typing is not clamped. */
export const STEPPER: Record<LimitField, { step: number; min: number; max: number }> = {
  max_loss_per_trade_pct: { step: 0.1, min: 0.1, max: 5 },
  max_position_pct: { step: 1, min: 1, max: 25 },
  max_daily_loss_pct: { step: 0.5, min: 0.5, max: 15 },
  max_weekly_loss_pct: { step: 1, min: 1, max: 30 },
  max_leverage: { step: 1, min: 1, max: 20 },
  max_open_positions: { step: 1, min: 1, max: 20 },
  max_share_per_instrument_pct: { step: 1, min: 5, max: 60 },
  max_correlated_exposure_pct: { step: 1, min: 5, max: 80 },
  min_expected_r: { step: 0.1, min: 1, max: 5 },
  max_consecutive_losses: { step: 1, min: 1, max: 10 },
}

/** Fields the API takes as JSON integers; the rest are decimal strings (ATL-3). */
export const INTEGER_FIELDS: ReadonlySet<LimitField> = new Set(['max_leverage', 'max_open_positions', 'max_consecutive_losses'])

export const LIMIT_FIELDS: readonly LimitField[] = LIMIT_DEFS.map((d) => d.field)

export type LimitTexts = Record<LimitField, string>

export interface Draft {
  profile: RiskProfile
  /** The preset the limits were last taken from; "Balanced: 5×" hints compare with it. */
  base: PresetId
  values: LimitTexts
  autonomy: number
}

const digitsOf = (field: LimitField) => LIMIT_DEFS.find((d) => d.field === field)?.digits ?? 0

/** The value as it sits in its field: "1.0", "5", "1.8". */
export function inputText(field: LimitField, value: string | number): string {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digitsOf(field)) : String(value)
}

/** The typed text as a number, or null when it is empty or not a number. */
export function parseLimit(text: string): number | null {
  const raw = text.trim().replace(/[,٫]/, '.')
  if (raw === '' || !/^[+-]?(\d+\.?\d*|\.\d+)$/.test(raw)) return null
  return Number(raw)
}

export function draftFrom(doc: SettingsDoc): Draft {
  const s = doc.settings
  const values = Object.fromEntries(LIMIT_FIELDS.map((f) => [f, inputText(f, s[f])])) as LimitTexts
  return {
    profile: s.risk_profile,
    base: s.risk_profile === 'custom' ? 'balanced' : s.risk_profile,
    values,
    autonomy: s.autonomy_level,
  }
}

const sameNumber = (a: string | number, b: string | number) => {
  const x = typeof a === 'number' ? a : parseLimit(a)
  const y = typeof b === 'number' ? b : parseLimit(b)
  return x !== null && y !== null && x === y
}

/** The preset whose ten limits equal these values, if any (numeric equality, like the server). */
export function matchingPreset(values: LimitTexts, presets: Record<PresetId, RiskLimits>): PresetId | null {
  return PRESET_IDS.find((p) => LIMIT_FIELDS.every((f) => sameNumber(values[f], presets[p][f]))) ?? null
}

export function presetDraft(draft: Draft, preset: PresetId, presets: Record<PresetId, RiskLimits>): Draft {
  const values = Object.fromEntries(LIMIT_FIELDS.map((f) => [f, inputText(f, presets[preset][f])])) as LimitTexts
  return { ...draft, profile: preset, base: preset, values }
}

/** After a limit changes: back to a preset if all ten match one, otherwise Custom. */
export function withLimit(draft: Draft, field: LimitField, text: string, presets: Record<PresetId, RiskLimits>): Draft {
  const values = { ...draft.values, [field]: text }
  const match = matchingPreset(values, presets)
  return match ? { ...draft, values, profile: match, base: match } : { ...draft, values, profile: 'custom' }
}

/** One stepper press: snap to the step grid, keep inside the stepper's bounds. */
export function stepped(field: LimitField, text: string, dir: 1 | -1): string {
  const { step, min, max } = STEPPER[field]
  const scale = 10 ** digitsOf(field) * 10
  const current = parseLimit(text) ?? min
  const units = Math.round(step * scale)
  const next = (Math.round((Math.round(current * scale) + dir * units) / units) * units) / scale
  return inputText(field, Math.min(max, Math.max(min, next)))
}

/** The value as the API takes it: an integer for counts and leverage, a decimal string otherwise. */
export function apiValue(field: LimitField, text: string): string | number {
  const raw = text.trim().replace(/[,٫]/, '.')
  if (INTEGER_FIELDS.has(field)) {
    const n = parseLimit(raw)
    return n === null ? raw : n
  }
  return raw.replace(/^\+/, '')
}

/** The PATCH `changes` that turn the saved settings into the draft. */
export function changesFor(draft: Draft, doc: SettingsDoc): SettingsChanges {
  const s = doc.settings
  const changes: SettingsChanges = {}
  if (draft.profile !== s.risk_profile) changes.risk_profile = draft.profile
  for (const f of LIMIT_FIELDS) {
    if (!sameNumber(draft.values[f], s[f])) changes[f] = apiValue(f, draft.values[f])
  }
  if (draft.autonomy !== s.autonomy_level) changes.autonomy_level = draft.autonomy
  return changes
}

export const isDirty = (draft: Draft, doc: SettingsDoc) => Object.keys(changesFor(draft, doc)).length > 0

/** Fields whose text is empty or not a number: Save stays disabled until they are fixed. */
export function emptyFields(draft: Draft): LimitField[] {
  return LIMIT_FIELDS.filter((f) => parseLimit(draft.values[f]) === null)
}

/** A 422 body's `changes.<field>` errors, keyed by field. */
export function fieldErrors(err: unknown): Partial<Record<string, string>> {
  if (!(err instanceof ApiError) || err.status !== 422) return {}
  const errors = (err.body as { errors?: Record<string, string[]> } | null)?.errors ?? {}
  const out: Partial<Record<string, string>> = {}
  for (const [key, messages] of Object.entries(errors)) {
    if (key.startsWith('changes.') && messages[0]) out[key.slice('changes.'.length)] = messages[0]
  }
  return out
}

/** The bounds named in the server's range message ("greater than 0 and at most 100", "between 1 and 125"). */
export function serverBounds(message: string): { kind: 'exclusive' | 'inclusive'; min: string; max: string } | null {
  const ex = /greater than (\d+(?:\.\d+)?) and at most (\d+(?:\.\d+)?)/.exec(message)
  if (ex?.[1] && ex[2]) return { kind: 'exclusive', min: ex[1], max: ex[2] }
  const inc = /between (\d+(?:\.\d+)?) and (\d+(?:\.\d+)?)/.exec(message)
  if (inc?.[1] && inc[2]) return { kind: 'inclusive', min: inc[1], max: inc[2] }
  return null
}

/** PATCH /api/settings with no automatic retry: a 409 is shown to the owner as "changed elsewhere". */
export function useSaveSettings(env: SettingsDoc['env'] = 'demo') {
  const client = useQueryClient()
  return useMutation<SettingsDoc, ApiError, { version: number; changes: SettingsChanges }>({
    mutationFn: async ({ version, changes }) => (await api.patch<{ data: SettingsDoc }>('/api/settings', { env, version, changes })).data,
    onSuccess: (doc) => {
      client.setQueryData(settingsKey(env), { data: doc })
      void client.invalidateQueries({ queryKey: historyKey(env) })
    },
  })
}

export interface HistoryEntry {
  id: string
  version: number
  diff: Record<string, { from: string | number | null; to: string | number | null }>
  changed_by: { id: string; name: string } | null
  created_at: string
}

export const historyKey = (env: SettingsDoc['env'] = 'demo') => ['settings', env, 'history'] as const

export function useSettingsHistory(env: SettingsDoc['env'] = 'demo') {
  return useApiQuery<{ data: HistoryEntry[] }>(historyKey(env), `/api/settings/history?env=${env}&per_page=20`, { staleTime: 30_000 })
}
