import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'

/*
 * ATL-48's Settings → Data API (docs/handoff/ATL-48-data-api.md). Reads come from Postgres; resync
 * and pause are signed calls to trading_core, throttled per minute (429), or 502 when it is down.
 */

export type JobStatus = 'pending' | 'running' | 'done' | 'failed' | 'paused'
export type IssueKind = 'gap' | 'duplicate' | 'zero_volume' | 'outlier'
export type AssetClass = 'crypto' | 'stock' | 'etf' | 'index' | 'commodity' | 'other'

export interface ApiJob {
  dataset: string
  status: JobStatus
  depth_days: number | null
  target_from: string | null
  cursor_ts: string | null
  rows_written: number
  expected: number | null
  /** One-decimal string ("50.0"); null for full-history datasets until done. */
  percent: string | null
  last_error: string | null
  updated_at: string
}

export interface ApiInstrument {
  inst_id: string
  /** null for the per-currency rubik rows (inst_id "BTC"). */
  asset_class: AssetClass | null
  open_issues: number
  datasets: ApiJob[]
}

export interface ApiIssue {
  id: string
  kind: IssueKind
  inst_id: string
  dataset: string
  from_ts: string
  to_ts: string
  detected_at: string
  repaired_at: string | null
  detail: Record<string, unknown> | null
}

export interface BackfillReport {
  env: 'demo' | 'live'
  as_of: string
  instruments: ApiInstrument[]
  totals: {
    instruments: number
    jobs: number
    by_status: Record<JobStatus, number>
    rows_written: number
    percent: string | null
  }
  quality: { open: number; open_by_kind: Record<IssueKind, number> }
  quality_issues: ApiIssue[]
}

export interface StorageReport {
  hypertables: { name: string; bytes: number }[]
  total_bytes: number
}

export const BACKFILL_KEY = ['data', 'backfill', 'demo'] as const
export const STORAGE_KEY = ['data', 'storage'] as const

/** While a job runs, progress is polled every 5 s (design §7); after an action, until the server reflects it. */
export const POLL_MS = 5_000
export const STORAGE_POLL_MS = 60_000

const isActive = (r: BackfillReport | undefined) =>
  Boolean(r?.instruments.some((i) => i.datasets.some((d) => d.status === 'running' || d.status === 'pending')))

/** Polls while any job runs or waits, and until `pollUntil` (ms) after an action. */
export function useBackfill(pollUntil: number) {
  return useQuery({
    queryKey: BACKFILL_KEY,
    queryFn: () => api.get<{ data: BackfillReport }>('/api/data/backfill?env=demo').then((r) => r.data),
    refetchInterval: (q) => (isActive(q.state.data) || Date.now() < pollUntil ? POLL_MS : false),
    refetchIntervalInBackground: false,
  })
}

export function useStorage() {
  return useQuery({
    queryKey: STORAGE_KEY,
    queryFn: () => api.get<{ data: StorageReport }>('/api/data/storage').then((r) => r.data),
    refetchInterval: STORAGE_POLL_MS,
    refetchIntervalInBackground: false,
  })
}

/** How a resync or pause answered: accepted, throttled (429), or the trading service down (502 or unreachable). */
export type ActionAnswer = 'ok' | 'limited' | 'down'

export function answerOf(err: unknown): Exclude<ActionAnswer, 'ok'> {
  if (err instanceof ApiError && err.status === 429) return 'limited'
  return 'down'
}

export type DataAction = { kind: 'resync' | 'pause'; instId?: string }

/** POST /api/data/resync or /api/data/pause; refetches progress and storage after a success. */
export function useDataAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ kind, instId }: DataAction) =>
      api.post<{ ok: true }>(`/api/data/${kind}`, instId ? { env: 'demo', inst_id: instId } : { env: 'demo' }),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: BACKFILL_KEY })
      void qc.invalidateQueries({ queryKey: STORAGE_KEY })
    },
  })
}
