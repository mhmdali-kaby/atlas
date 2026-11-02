import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import type { Bar, BacktestRequest, Side } from './model'

/*
 * ATL-208's /api/backtests (docs/handoff/ATL-208-backtests-api.md). Runs belong to the active env
 * and the owner; there is no broadcast, so a queued or running run is polled every POLL_MS until
 * it is done or failed. The list never carries `result`; the detail carries it unchanged from
 * trading_core (rendered by ATL-210).
 */

export type RunStatus = 'queued' | 'running' | 'done' | 'failed'

/** The design's poll interval ("Atlas checks every 2 seconds"). */
export const POLL_MS = 2000

export interface RunSummary {
  id: string
  env: TradingEnv
  status: RunStatus
  rule_name: string
  side: Side
  inst_ids: string[]
  bar: Bar
  period: { start: string; end: string }
  n_trades: number | null
  overfit_warning: boolean | null
  data_gap: boolean | null
  /** invalid | too_many_bars | no_data | insufficient_data | timeout | trading_core_error | trading_core_unavailable | job_failed */
  error: string | null
  error_reason: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface RunDetail extends RunSummary {
  request: BacktestRequest
  /** trading_core's 200 body, unchanged (ATL-205 handoff); ATL-210 renders it. */
  result: Record<string, unknown> | null
}

export interface StartResponse {
  ok: true
  id: string
  status: RunStatus
  created_at: string
}

export const isActive = (s: RunStatus | undefined) => s === 'queued' || s === 'running'

export const runsKey = (env: TradingEnv) => ['backtests', env] as const
export const runKey = (env: TradingEnv, id: string) => ['backtests', env, id] as const

/** Recent runs, newest first. Polls while one of them is still going, so the sidebar's spinner turns into a tick. */
export function useRuns(env: TradingEnv) {
  return useQuery<{ env: TradingEnv; data: RunSummary[] }, ApiError>({
    queryKey: runsKey(env),
    queryFn: ({ signal }) => api.get('/api/backtests', { signal }),
    refetchInterval: (q) => (q.state.data?.data.some((r) => isActive(r.status)) ? POLL_MS : false),
    retry: false,
  })
}

/** One run, polled every POLL_MS while it is queued or running. */
export function useRun(env: TradingEnv, id: string | null) {
  const client = useQueryClient()
  return useQuery<RunDetail, ApiError>({
    queryKey: runKey(env, id ?? ''),
    enabled: id !== null && id !== '',
    queryFn: async ({ signal }) => {
      const run = await api.get<RunDetail>(`/api/backtests/${encodeURIComponent(id ?? '')}`, { signal })
      // a finished run changes its row in the list too
      if (!isActive(run.status)) void client.invalidateQueries({ queryKey: runsKey(env), exact: true })
      return run
    },
    refetchInterval: (q) => (q.state.error ? false : isActive(q.state.data?.status) || q.state.data === undefined ? POLL_MS : false),
    retry: false,
  })
}

export function useStartRun(env: TradingEnv) {
  const client = useQueryClient()
  return useMutation<StartResponse, ApiError, BacktestRequest>({
    mutationFn: (body) => api.post<StartResponse>('/api/backtests', body),
    onSuccess: () => void client.invalidateQueries({ queryKey: runsKey(env), exact: true }),
  })
}
