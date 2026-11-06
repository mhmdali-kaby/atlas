import { api, useApiQuery } from '../../lib/api'
import { useMutation } from '@tanstack/react-query'

/* ATL-231's backup API (docs/handoff/ATL-231-backup-api.md). */

export interface BackupRun {
  id: string
  file: string
  path: string | null
  bytes: number | null
  status: 'ok' | 'failed'
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface BackupDoc {
  env: 'demo' | 'live'
  settings: { enabled: boolean; folder: string; time: string; timezone: string; keep: number }
  /** Newest first, at most backup.runs_listed (10). */
  runs: BackupRun[]
  last_good: (BackupRun & { age_seconds: number }) | null
  stale_after_hours: number
  stale: boolean
}

export const backupKey = ['backup'] as const

/** GET /api/backup; `pollMs` while a requested run has not appeared yet. */
export function useBackup(pollMs: number | false = false) {
  return useApiQuery<{ data: BackupDoc }>(backupKey, '/api/backup', { refetchInterval: pollMs, staleTime: 10_000 })
}

/** POST /api/backup/run: 202, queued. Throttled to 1 a minute app-wide (429). */
export function useRunBackup() {
  return useMutation({
    mutationFn: () => api.post<{ data: { queued: boolean; message: string } }>('/api/backup/run'),
  })
}
