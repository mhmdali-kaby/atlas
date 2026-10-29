import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import { withEnabled, type ApiSource, type SourcesReport } from './model'

/*
 * ATL-66/ATL-71's Data sources API (docs/handoff/ATL-66-data-sources-api.md). GET lists every
 * source with its health and the per-category summary; PATCH {enabled} turns one on or off.
 */

export const SOURCES_KEY = ['data-sources'] as const

/** Design §7.3: refetch every 30 s while the page is visible, and right after every action. */
export const POLL_MS = 30_000

export function useSources() {
  return useQuery({
    queryKey: SOURCES_KEY,
    queryFn: () => api.get<SourcesReport>('/api/data-sources'),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  })
}

/** How a toggle failed: throttled (429) or anything else ("the server didn't answer"). */
export type ToggleFailure = 'limited' | 'down'

export function failureOf(err: unknown): ToggleFailure {
  return err instanceof ApiError && err.status === 429 ? 'limited' : 'down'
}

export interface ToggleVars {
  key: string
  enabled: boolean
}

/**
 * Optimistic toggle (design §5.4): the cache flips at once (row status and the summary's all_down
 * are recomputed), the PATCH goes out, and a failure restores the snapshot. Always refetches after.
 */
export function useToggleSource(onFailed: (vars: ToggleVars, failure: ToggleFailure) => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ key, enabled }: ToggleVars) => api.patch<unknown>(`/api/data-sources/${encodeURIComponent(key)}`, { enabled }),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: SOURCES_KEY })
      const previous = qc.getQueryData<SourcesReport>(SOURCES_KEY)
      if (previous) qc.setQueryData<SourcesReport>(SOURCES_KEY, withEnabled(previous, vars.key, vars.enabled, Date.now()))
      return { previous }
    },
    onError: (err, vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(SOURCES_KEY, ctx.previous)
      onFailed(vars, failureOf(err))
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SOURCES_KEY })
    },
  })
}

/** How a key save or removal failed (design §5.6): refused (422), throttled (429) or anything else. */
export type KeyFailure = 'invalid' | 'limited' | 'down'

export function keyFailureOf(err: unknown): KeyFailure {
  if (err instanceof ApiError && err.status === 422) return 'invalid'
  return failureOf(err)
}

/*
 * The key calls are plain functions, not useMutation: a mutation keeps its variables in the
 * MutationCache, and the typed key must never sit in any TanStack cache (ATL-69). The caller passes
 * the value straight from the uncontrolled input and drops it.
 */

/** PUT /api/data-sources/{key}/key — provider-wide; the response carries key_last4, never the key. */
export function putKey(sourceKey: string, apiKey: string) {
  return api.put<{ data: ApiSource }>(`/api/data-sources/${encodeURIComponent(sourceKey)}/key`, { api_key: apiKey })
}

/** DELETE /api/data-sources/{key}/key → 204, provider-wide. */
export function deleteKey(sourceKey: string) {
  return api.delete<null>(`/api/data-sources/${encodeURIComponent(sourceKey)}/key`)
}
