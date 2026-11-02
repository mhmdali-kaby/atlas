import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, useApiQuery } from '../../lib/api'
import type { SettingsDoc } from '../onboarding'

/*
 * Settings → Notifications (brief §14.9, decision 254 (2)(3); design v18 §6,
 * js/pages/settings-notifications.js). The 11 brief §17 events × three channels, stored as the
 * flat settings keys notifications.<event>.<channel> (ATL-214), and the Telegram bot behind
 * GET/PUT/DELETE /api/notifications/telegram + POST …/test (ATL-215).
 */

export const GROUPS = ['needs', 'trades', 'team'] as const
export type Group = (typeof GROUPS)[number]

export const EVENTS = [
  { id: 'proposal_awaiting_approval', group: 'needs', tone: 'accent' },
  { id: 'circuit_breaker', group: 'needs', tone: 'critical' },
  { id: 'kill_switch', group: 'needs', tone: 'critical' },
  { id: 'reconciliation_mismatch', group: 'needs', tone: 'critical' },
  { id: 'order_filled', group: 'trades', tone: 'neutral' },
  { id: 'stop_hit', group: 'trades', tone: 'warning' },
  { id: 'target_hit', group: 'trades', tone: 'profit' },
  { id: 'news_alert', group: 'team', tone: 'warning' },
  { id: 'agent_degraded', group: 'team', tone: 'warning' },
  { id: 'data_stale', group: 'team', tone: 'warning' },
  { id: 'daily_report', group: 'team', tone: 'neutral' },
] as const satisfies readonly { id: string; group: Group; tone: string }[]

export type EventId = (typeof EVENTS)[number]['id']
export type Tone = (typeof EVENTS)[number]['tone']

export const CHANNELS = ['in_app', 'browser', 'telegram'] as const
export type Channel = (typeof CHANNELS)[number]

/** The four "Needs you" events, which the first-save notice can send to Telegram in one go. */
export const NEEDS: readonly EventId[] = EVENTS.filter((e) => e.group === 'needs').map((e) => e.id)

export const prefKey = (event: EventId, channel: Channel) => `notifications.${event}.${channel}`

/** config/defaults.yaml notifications.channel_defaults: in_app and browser on, telegram off. */
const DEFAULT: Record<Channel, boolean> = {
  in_app: true,
  browser: true,
  telegram: false,
}

export type Prefs = Record<string, boolean>

/** Every switch's saved value; a key an older row lacks falls back to the default. */
export function prefsFrom(doc: SettingsDoc): Prefs {
  const s = doc.settings as unknown as Record<string, unknown>
  const out: Prefs = {}
  for (const e of EVENTS) {
    for (const ch of CHANNELS) {
      const v = s[prefKey(e.id, ch)]
      out[prefKey(e.id, ch)] = typeof v === 'boolean' ? v : DEFAULT[ch]
    }
  }
  return out
}

/** PATCH /api/settings with dotted boolean keys; a 409 refetches and sends the same change once more. */
export function useSaveSwitches(env: SettingsDoc['env'] = 'demo') {
  const client = useQueryClient()
  const key = ['settings', env] as const
  return useMutation<SettingsDoc, ApiError, Record<string, boolean>>({
    mutationFn: async (changes) => {
      const send = (version: number) =>
        api.patch<{ data: SettingsDoc }>('/api/settings', {
          env,
          version,
          changes,
        })
      let doc = client.getQueryData<{ data: SettingsDoc }>(key)?.data
      doc ??= (await api.get<{ data: SettingsDoc }>(`/api/settings?env=${env}`)).data
      try {
        return (await send(doc.version)).data
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err
        const fresh = await api.get<{ data: SettingsDoc }>(`/api/settings?env=${env}`)
        return (await send(fresh.data.version)).data
      }
    },
    onSuccess: (doc) => {
      // Only ever move forward: two switches saved at once answer in any order.
      const prev = client.getQueryData<{ data: SettingsDoc }>(key)?.data
      if (!prev || doc.version >= prev.version) client.setQueryData(key, { data: doc })
    },
  })
}

/* ------------------------------------------------------------------ Telegram */

/** ATL-215's validation rules, checked here first so the owner sees the message before a round trip. */
export const TOKEN_RE = /^\d{3,20}:[A-Za-z0-9_-]{20,100}$/
export const CHAT_RE = /^-?\d{1,20}$/

export interface TelegramState {
  configured: boolean
  bot_token_last4: string | null
  chat_id_last4: string | null
  last_sent_at: string | null
  last_tested_at: string | null
  last_error: string | null
  updated_at: string | null
}

export const TELEGRAM_KEY = ['notifications', 'telegram'] as const

export function useTelegram() {
  return useApiQuery<{ data: TelegramState }>(TELEGRAM_KEY, '/api/notifications/telegram', { staleTime: 30_000, retry: false })
}

export type FieldError = 'tokenEmpty' | 'token' | 'chatEmpty' | 'chat'

export function validateBot(token: string, chat: string): { token?: FieldError; chat?: FieldError } {
  const out: { token?: FieldError; chat?: FieldError } = {}
  if (!token) out.token = 'tokenEmpty'
  else if (!TOKEN_RE.test(token)) out.token = 'token'
  if (!chat) out.chat = 'chatEmpty'
  else if (!CHAT_RE.test(chat)) out.chat = 'chat'
  return out
}

/** The API's own 422 message per field, when it refuses what the client let through. */
export function serverFieldErrors(err: unknown): {
  bot_token?: string
  chat_id?: string
} {
  if (!(err instanceof ApiError) || err.status !== 422) return {}
  const body = err.body as { errors?: Record<string, string[]> } | undefined
  const pick = (k: string) => body?.errors?.[k]?.[0]
  return { bot_token: pick('bot_token'), chat_id: pick('chat_id') }
}

export function useSaveBot() {
  const client = useQueryClient()
  return useMutation<{ data: TelegramState }, ApiError, { bot_token: string; chat_id: string }>({
    mutationFn: (body) => api.put<{ data: TelegramState }>('/api/notifications/telegram', body),
    onSuccess: (res) => client.setQueryData(TELEGRAM_KEY, res),
  })
}

export function useRemoveBot() {
  const client = useQueryClient()
  return useMutation<{ data: TelegramState }, ApiError, void>({
    mutationFn: () => api.delete<{ data: TelegramState }>('/api/notifications/telegram'),
    onSuccess: (res) => client.setQueryData(TELEGRAM_KEY, res),
  })
}

export interface TestResult {
  ok: boolean
  error: string | null
  message: string | null
  data?: TelegramState
}

/** POST …/test: 200 ok, 502 test_failed (with the scrubbed last_error in data), 409 not_configured. */
export function useTestBot() {
  const client = useQueryClient()
  return useMutation<TestResult, ApiError, void>({
    mutationFn: async () => {
      try {
        return await api.post<TestResult>('/api/notifications/telegram/test')
      } catch (err) {
        if (err instanceof ApiError && (err.status === 502 || err.status === 409) && err.body && typeof err.body === 'object') {
          return err.body as TestResult
        }
        throw err
      }
    },
    onSuccess: (res) => {
      if (res.data) client.setQueryData(TELEGRAM_KEY, { data: res.data })
    },
  })
}

/* ---------------------------------------------------------- browser permission */

export type Permission = 'granted' | 'denied' | 'default' | 'unsupported'

function readPermission(): Permission {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return window.Notification.permission
}

/**
 * The browser's notification permission, kept current when the owner changes it in site settings.
 * `ask()` is only ever called from a button press: nothing asks on page load (design §5.4).
 */
export function useBrowserPermission(): {
  permission: Permission
  ask: () => Promise<void>
} {
  const [permission, setPermission] = useState<Permission>(readPermission)
  useEffect(() => {
    let status: PermissionStatus | undefined
    const update = () => setPermission(readPermission())
    void navigator.permissions
      ?.query({ name: 'notifications' as PermissionName })
      .then((s) => {
        status = s
        s.addEventListener('change', update)
      })
      .catch(() => undefined)
    return () => status?.removeEventListener('change', update)
  }, [])
  const ask = useCallback(async () => {
    if (readPermission() !== 'default') return
    await window.Notification.requestPermission()
    setPermission(readPermission())
  }, [])
  return { permission, ask }
}

/* ------------------------------------------------------------------ text */

/** An LTR isolate inside running text (figures, times, IDs; design §7). */
export const iso = (s: string) => `⁦${s}⁩`
/** `•••• WXYZ` as one LTR isolate, so the dots stay with the last 4 in Arabic. */
export const masked = (last4: string) => iso(`•••• ${last4}`)
