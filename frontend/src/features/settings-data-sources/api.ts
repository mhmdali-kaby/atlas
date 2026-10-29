import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import { withEnabled, type SourcesReport } from './model'

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
