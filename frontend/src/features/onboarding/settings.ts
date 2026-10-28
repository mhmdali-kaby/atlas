import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, useApiQuery } from '../../lib/api'

/** The three presets GET /api/settings carries (brief §14.3, ATL-3). */
export const PRESET_IDS = ['conservative', 'balanced', 'aggressive'] as const
export type PresetId = (typeof PRESET_IDS)[number]
export type RiskProfile = PresetId | 'custom'

/** The ten §14.3 fields a preset sets. Decimals are strings ("1.0"), counts are integers. */
export interface RiskLimits {
  max_loss_per_trade_pct: string
  max_position_pct: string
  max_daily_loss_pct: string
  max_weekly_loss_pct: string
  max_leverage: number
  max_open_positions: number
  max_share_per_instrument_pct: string
  max_correlated_exposure_pct: string
  min_expected_r: string
  max_consecutive_losses: number
}

export interface SettingsValues extends RiskLimits {
  risk_profile: RiskProfile
  autonomy_level: number
  /** 0 until the owner finishes onboarding, then 1 (ATL-19). */
  onboarding_completed: number
  [field: string]: string | number
}

export interface SettingsDoc {
  id: string
  env: 'demo' | 'live'
  version: number
  settings: SettingsValues
  presets: Record<PresetId, RiskLimits>
  updated_at: string
}

export type SettingsChanges = Partial<Record<keyof SettingsValues, string | number>>

/** Phase 0 enables autonomy levels 1 (Advise only) and 2 (Approve each trade). */
export const AUTONOMY_LEVELS = [1, 2, 3, 4] as const
export const ENABLED_AUTONOMY = [1, 2] as const
export const DEFAULT_AUTONOMY = 2
export const DEFAULT_PROFILE: PresetId = 'balanced'

export const settingsKey = (env: SettingsDoc['env'] = 'demo') => ['settings', env] as const

export function useSettings(env: SettingsDoc['env'] = 'demo', enabled = true) {
  return useApiQuery<{ data: SettingsDoc }>(settingsKey(env), `/api/settings?env=${env}`, { staleTime: 30_000, enabled })
}

export const isPreset = (value: unknown): value is PresetId => (PRESET_IDS as readonly unknown[]).includes(value)

/** Onboarding is done once the owner pressed Finish setup (which always bumps the version past 1). */
export const isOnboarded = (doc: SettingsDoc | undefined): boolean =>
  !!doc && doc.version > 1 && doc.settings.onboarding_completed === 1

/**
 * PATCH /api/settings with the version last read. A 409 (changed elsewhere) refetches the
 * settings and applies the same change once more on the fresh version; a second 409 throws.
 */
export function usePatchSettings(env: SettingsDoc['env'] = 'demo') {
  const client = useQueryClient()
  const key = settingsKey(env)

  return useMutation<SettingsDoc, ApiError, SettingsChanges>({
    mutationFn: async (changes) => {
      const send = (version: number) => api.patch<{ data: SettingsDoc }>('/api/settings', { env, version, changes })
      let current = client.getQueryData<{ data: SettingsDoc }>(key)?.data
      current ??= (await client.fetchQuery({ queryKey: key, queryFn: () => api.get<{ data: SettingsDoc }>(`/api/settings?env=${env}`) })).data
      try {
        return (await send(current.version)).data
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err
        const fresh = await api.get<{ data: SettingsDoc }>(`/api/settings?env=${env}`)
        client.setQueryData(key, fresh)
        return (await send(fresh.data.version)).data
      }
    },
    onSuccess: (doc) => client.setQueryData(key, { data: doc }),
  })
}
