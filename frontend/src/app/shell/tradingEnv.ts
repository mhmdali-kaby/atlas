import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ApiError } from '../../lib/api'
import { getEcho } from '../../lib/echo'

/**
 * The environment Atlas trades in (decision 255 (4)): `system_state.active_env`, served by
 * GET /api/environment (ATL-236). Every page reads it through this one query, so a switch on
 * Settings → Environment (ATL-239) turns the toolbar, the footer and every page at once.
 */
export type TradingEnv = 'demo' | 'live'

export type PreflightKey =
  | 'live_connection'
  | 'demo_history'
  | 'live_risk_profile'
  | 'live_autonomy'
  | 'live_capital_cap'
  | 'ip_allowlist'
  | 'sub_account'
  | 'demo_reviewed'

export interface PreflightItem {
  key: PreflightKey
  kind: 'computed' | 'attested'
  ok: boolean
  /** Translated by the server from Accept-Language. */
  detail: string
  /** Attested items only: when the owner ticked it, or null. */
  confirmed_at?: string | null
}

export interface EnvironmentDoc {
  active_env: TradingEnv
  live_started_at: string | null
  first_month_ends_at: string | null
  in_first_live_month: boolean
  switched_at: string | null
  confirmation_text: string
  preflight: { ok: boolean; items: PreflightItem[] }
}

export interface EnvironmentResponse {
  data: EnvironmentDoc
}

/** Prefix of every environment query; invalidate it to refetch in every language. */
export const ENVIRONMENT_KEY = ['environment'] as const

export const environmentKey = (lang: string) => [...ENVIRONMENT_KEY, lang] as const

/** Sends the UI language so the pre-flight detail lines come back translated. */
export const langHeaders = (lang: string) => ({ 'Accept-Language': lang === 'ar' ? 'ar' : 'en' })

export function useEnvironment() {
  const { i18n } = useTranslation()
  const lang = i18n.language === 'ar' ? 'ar' : 'en'
  return useQuery<EnvironmentResponse, ApiError>({
    queryKey: environmentKey(lang),
    queryFn: ({ signal }) => api.get<EnvironmentResponse>('/api/environment', { signal, headers: langHeaders(lang) }),
    staleTime: 30_000,
    retry: false,
  })
}

export function useTradingEnv(): TradingEnv {
  const { data } = useEnvironment()
  return data?.data.active_env === 'live' ? 'live' : 'demo'
}

/**
 * `system.env_changed` on private-system (ATL-236): a switch made in another tab or by another
 * session. Refetch the environment and every other query, so the badge, the footer and every
 * page follow with no reload (the same thing the Environment page does after its own switch).
 */
export function useEnvChangedListener() {
  const qc = useQueryClient()
  useEffect(() => {
    let channel: ReturnType<ReturnType<typeof getEcho>['private']>
    try {
      channel = getEcho().private('system')
    } catch {
      return undefined
    }
    const refresh = () => void qc.invalidateQueries()
    channel.listen('.system.env_changed', refresh)
    return () => {
      channel.stopListening('.system.env_changed', refresh)
    }
  }, [qc])
}
