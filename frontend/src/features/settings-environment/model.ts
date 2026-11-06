import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  environmentKey,
  langHeaders,
  type EnvironmentResponse,
  type PreflightKey,
} from '../../app/shell/tradingEnv'
import { api, type ApiError } from '../../lib/api'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { ownerDateFormat } from '../../lib/ownerZone'

/*
 * Settings → Environment (ATL-239) against ATL-236's API:
 *   GET  /api/environment                (app/shell/tradingEnv.ts, shared with the toolbar)
 *   PUT  /api/environment/attestations   {ip_allowlist|sub_account|demo_reviewed: bool}
 *   POST /api/environment/switch         {target: live, password, confirmation} | {target: demo, confirm: true}
 */

export type AttestedKey = 'ip_allowlist' | 'sub_account' | 'demo_reviewed'

export const ATTESTED: readonly AttestedKey[] = ['ip_allowlist', 'sub_account', 'demo_reviewed']

/** Where each failing computed item is fixed (design §4.3); Risk, Autonomy and Money open on the live row. */
export const FIX: Partial<Record<PreflightKey, string>> = {
  live_connection: '/settings/connections',
  live_risk_profile: '/settings/risk?env=live',
  live_autonomy: '/settings/autonomy?env=live',
  live_capital_cap: '/settings/money?env=live',
}

function useLangCode(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/**
 * One tick saves at once, optimistically (design §4.3). On failure the box goes back and the
 * caller shows the toast; on success the server's body replaces the page's data.
 */
export function useAttest(onFail: (key: AttestedKey) => void) {
  const qc = useQueryClient()
  const lang = useLangCode()
  const key = environmentKey(lang)
  return useMutation<EnvironmentResponse, ApiError, { key: AttestedKey; on: boolean }, { before?: EnvironmentResponse }>({
    mutationFn: ({ key: item, on }) =>
      api.put<EnvironmentResponse>('/api/environment/attestations', { [item]: on }, { headers: langHeaders(lang) }),
    onMutate: async ({ key: item, on }) => {
      await qc.cancelQueries({ queryKey: key })
      const before = qc.getQueryData<EnvironmentResponse>(key)
      if (before) {
        const items = before.data.preflight.items.map((i) =>
          i.key === item ? { ...i, ok: on, confirmed_at: on ? (i.confirmed_at ?? new Date().toISOString()) : null } : i,
        )
        qc.setQueryData<EnvironmentResponse>(key, {
          data: { ...before.data, preflight: { ok: items.every((i) => i.ok), items } },
        })
      }
      return { before }
    },
    onError: (_err, { key: item }, ctx) => {
      if (ctx?.before) qc.setQueryData(key, ctx.before)
      onFail(item)
    },
    onSuccess: (res) => qc.setQueryData(key, res),
  })
}

export type SwitchBody = { target: 'live'; password: string; confirmation: string } | { target: 'demo'; confirm: true }

/**
 * POST /api/environment/switch. On 200 the page takes the server's body at once, then every
 * query in the app refetches, so the toolbar, footer and every page turn without a reload.
 */
export function useSwitchEnv() {
  const qc = useQueryClient()
  const lang = useLangCode()
  return useMutation<EnvironmentResponse, ApiError, SwitchBody>({
    mutationFn: (body) => api.post<EnvironmentResponse>('/api/environment/switch', body, { headers: langHeaders(lang) }),
    onSuccess: (res) => {
      qc.setQueryData(environmentKey(lang), res)
      void qc.invalidateQueries({ predicate: (q) => !(q.queryKey[0] === 'environment' && q.queryKey[1] === lang) })
    },
  })
}

/** The 422 body of a refused switch (ATL-236 handoff). */
export interface SwitchRefusal {
  errors: Partial<Record<'password' | 'confirmation' | 'preflight' | 'target' | 'confirm', string[]>>
  failing_items: PreflightKey[]
}

export function refusal(err: ApiError): SwitchRefusal {
  const body = (err.body ?? {}) as Partial<SwitchRefusal>
  return { errors: body.errors ?? {}, failing_items: Array.isArray(body.failing_items) ? body.failing_items : [] }
}

function dtf(lang: Lang, o: Intl.DateTimeFormatOptions) {
  return ownerDateFormat(DATE_LOCALE[lang], o)
}

/** "27 Oct" (short) or "24 November" (long), in the owner's zone, Western digits. */
export const dayText = (ts: string, lang: Lang, long = false) =>
  dtf(lang, { day: 'numeric', month: long ? 'long' : 'short' }).format(new Date(ts))

/** "21:40" */
export const timeText = (ts: string, lang: Lang) =>
  dtf(lang, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts))
