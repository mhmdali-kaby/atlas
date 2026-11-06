import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { setLanguage } from '../i18n'
import { isLanguage, type Language } from '../i18n/language'
import { isThemeChoice, type ThemeChoice } from '../theme/theme'
import { useTheme } from '../theme/useTheme'
import { api, ApiError } from './api'
import { DEFAULT_OWNER_TZ, isKnownZone, setOwnerZone } from './ownerZone'

/*
 * The owner's preferences (decision 255 (1), ATL-226: GET/PATCH /api/me/preferences).
 *
 * One store for language, theme, time zone and name. The SPA applies them at boot
 * (PreferencesSync, in the shell) and every control that changes one (Settings → General, the
 * toolbar's language button and appearance menu, More) writes through useSavePreference, so the
 * page and the toolbar can never disagree.
 */

export interface Preferences {
  name: string
  locale: Language
  timezone: string
  theme: ThemeChoice
}

export type PreferenceField = keyof Preferences

export const PREFERENCES_KEY = ['me', 'preferences'] as const
const SESSION_KEY = ['auth', 'me'] as const

/** Puts a preference on screen at once: language and direction, theme, or the display zone. */
export function applyPreference<K extends PreferenceField>(field: K, value: Preferences[K]): void {
  if (field === 'locale' && isLanguage(value)) void setLanguage(value)
  else if (field === 'theme' && isThemeChoice(value)) {
    if (useTheme.getState().choice !== value) useTheme.getState().setChoice(value)
  } else if (field === 'timezone' && typeof value === 'string') setOwnerZone(isKnownZone(value) ? value : DEFAULT_OWNER_TZ)
}

export function applyPreferences(prefs: Preferences): void {
  applyPreference('locale', prefs.locale)
  applyPreference('theme', prefs.theme)
  applyPreference('timezone', prefs.timezone)
}

export function usePreferences(enabled = true) {
  return useQuery<Preferences, ApiError>({
    queryKey: PREFERENCES_KEY,
    queryFn: async ({ signal }) => (await api.get<{ data: Preferences }>('/api/me/preferences', { signal })).data,
    staleTime: 60_000,
    enabled,
  })
}

/** Applies the saved preferences whenever they load or change (once signed in). */
export function PreferencesSync({ enabled }: { enabled: boolean }) {
  const prefs = usePreferences(enabled)
  const data = prefs.data
  useEffect(() => {
    if (data) applyPreferences(data)
  }, [data])
  return null
}

function patchCache<K extends PreferenceField>(client: QueryClient, field: K, value: Preferences[K]): void {
  client.setQueryData<Preferences>(PREFERENCES_KEY, (old) => (old ? { ...old, [field]: value } : old))
  if (field === 'name') {
    client.setQueryData<{ name: string } | null>(SESSION_KEY, (old) => (old ? { ...old, name: value as string } : old))
  }
}

export interface SaveVariables<K extends PreferenceField = PreferenceField> {
  field: K
  value: Preferences[K]
  /** The value to put back if the save fails. */
  previous: Preferences[K]
}

/** What a failed save says: the server's own field message (422), or that nothing answered. */
export function saveErrorOf(err: unknown, field: PreferenceField): { kind: 'server'; message: string } | { kind: 'offline' } {
  if (err instanceof ApiError && !err.network && err.status !== 0) {
    const message = err.fieldErrors[field]?.[0] ?? err.message
    if (message) return { kind: 'server', message }
  }
  return { kind: 'offline' }
}

/**
 * PATCH one preference. It is applied (and cached) at once; if the server refuses it or does not
 * answer, the previous value is put back on screen and in the cache, and the mutation rejects.
 */
export function useSavePreference() {
  const client = useQueryClient()
  return useMutation<Preferences, ApiError, SaveVariables>({
    mutationFn: async ({ field, value, previous }) =>
      (
        await api.patch<{ data: Preferences }>(
          '/api/me/preferences',
          { [field]: value },
          // A language change is applied before it is sent; a refusal must still read in the
          // language the app flips back to.
          field === 'locale' ? { headers: { 'Accept-Language': String(previous) } } : undefined,
        )
      ).data,
    onMutate: ({ field, value }) => {
      applyPreference(field, value)
      patchCache(client, field, value)
    },
    onSuccess: (prefs, { field }) => {
      client.setQueryData(PREFERENCES_KEY, prefs)
      // The server may normalise (a trimmed name); keep the page on what was stored.
      applyPreference(field, prefs[field])
      if (field === 'name') patchCache(client, 'name', prefs.name)
    },
    onError: (_err, { field, previous }) => {
      applyPreference(field, previous)
      patchCache(client, field, previous)
    },
  })
}
