import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api, ApiError } from '../../lib/api'
import { getEcho } from '../../lib/echo'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * Notifications (ATL-217, brief §17, decision 254 (2)) over ATL-214's API
 * (docs/handoff/ATL-214-notifications.md): GET /api/notifications (cursor pages, newest first,
 * active env, meta.unread_count), POST /api/notifications/{id}/read and POST …/read-all, and the
 * `notification.created` broadcast on private-system whose payload is the same row.
 */

export type NotificationEvent =
  | 'proposal_awaiting_approval'
  | 'order_filled'
  | 'stop_hit'
  | 'target_hit'
  | 'circuit_breaker'
  | 'data_stale'
  | 'reconciliation_mismatch'
  | 'kill_switch'
  | 'agent_degraded'
  | 'daily_report'
  | 'news_alert'

export type Severity = 'info' | 'warning' | 'critical'

export interface NotificationRow {
  id: string
  env: string
  event: NotificationEvent | string
  severity: Severity | string
  title_key: string
  /** Allow-listed per event; decimals stay strings, strings are plain text (two are untrusted). */
  params: Record<string, unknown>
  link: string
  channels: { in_app: boolean; browser: boolean; telegram: boolean }
  source_event_id?: string
  read_at: string | null
  created_at: string
}

export interface NotificationPage {
  data: NotificationRow[]
  meta: { env: string; per_page: number; unread_count: number; next_cursor: string | null; prev_cursor: string | null }
}

export const PAGE_SIZE = 50

export const notificationsKey = (env: TradingEnv) => ['notifications', env, 'list'] as const

type ListData = InfiniteData<NotificationPage, string | null>

export function useNotificationList(env: TradingEnv) {
  return useInfiniteQuery<NotificationPage, ApiError, ListData, ReturnType<typeof notificationsKey>, string | null>({
    queryKey: notificationsKey(env),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) => {
      const q = new URLSearchParams({ limit: String(PAGE_SIZE) })
      if (pageParam) q.set('cursor', pageParam)
      return api.get<NotificationPage>(`/api/notifications?${q.toString()}`, { signal })
    },
    getNextPageParam: (last) => last.meta.next_cursor,
    retry: false,
    staleTime: 30_000,
  })
}

/** Every loaded row, newest first, once each. */
export function rowsOf(data: ListData | undefined): NotificationRow[] {
  const seen = new Set<string>()
  const out: NotificationRow[] = []
  for (const page of data?.pages ?? []) {
    for (const row of page.data) {
      if (seen.has(row.id)) continue
      seen.add(row.id)
      out.push(row)
    }
  }
  return out
}

export function unreadCountOf(data: ListData | undefined): number {
  return data?.pages[0]?.meta.unread_count ?? 0
}

function update(qc: QueryClient, env: TradingEnv, fn: (d: ListData) => ListData) {
  qc.setQueryData<ListData>(notificationsKey(env), (d) => (d ? fn(d) : d))
}

/** A row from the broadcast joins the top of the list and the unread count, once. */
export function addRow(qc: QueryClient, env: TradingEnv, row: NotificationRow): boolean {
  const data = qc.getQueryData<ListData>(notificationsKey(env))
  if (!data) return true
  if (rowsOf(data).some((r) => r.id === row.id)) return false
  update(qc, env, (d) => {
    const [first, ...rest] = d.pages
    if (!first) return d
    const meta = { ...first.meta, unread_count: first.meta.unread_count + (row.read_at ? 0 : 1) }
    return { ...d, pages: [{ ...first, data: [row, ...first.data], meta }, ...rest] }
  })
  return true
}

function markLocally(qc: QueryClient, env: TradingEnv, id: string | null, at: string, unread?: number) {
  update(qc, env, (d) => {
    let changed = 0
    const pages = d.pages.map((p) => ({
      ...p,
      data: p.data.map((r) => {
        if ((id === null || r.id === id) && !r.read_at) {
          changed += 1
          return { ...r, read_at: at }
        }
        return r
      }),
    }))
    if (pages[0]) {
      const count = unread ?? Math.max(0, pages[0].meta.unread_count - (id === null ? pages[0].meta.unread_count : Math.min(changed, 1)))
      pages[0] = { ...pages[0], meta: { ...pages[0].meta, unread_count: count } }
    }
    return { ...d, pages }
  })
}

/** POST /api/notifications/{id}/read, shown read at once. */
export function useMarkRead(env: TradingEnv) {
  const qc = useQueryClient()
  return useMutation<{ data: NotificationRow }, ApiError, string>({
    mutationFn: (id) => api.post<{ data: NotificationRow }>(`/api/notifications/${encodeURIComponent(id)}/read`),
    onMutate: (id) => markLocally(qc, env, id, new Date().toISOString()),
  })
}

/** POST /api/notifications/read-all; the count comes back from the server. */
export function useMarkAllRead(env: TradingEnv) {
  const qc = useQueryClient()
  return useMutation<{ data: { env: string; marked: number; unread_count: number } }, ApiError, void>({
    mutationFn: () => api.post('/api/notifications/read-all'),
    onMutate: () => markLocally(qc, env, null, new Date().toISOString(), 0),
    onSuccess: (res) => markLocally(qc, env, null, new Date().toISOString(), res.data.unread_count),
    onError: () => void qc.invalidateQueries({ queryKey: notificationsKey(env) }),
  })
}

/**
 * Listens to `notification.created` on private-system and hands each new row of the active env to
 * `onRow` once (a reconnect may deliver a row again; the list already has it then).
 */
export function useNotificationEvents(env: TradingEnv, onRow: (row: NotificationRow) => void) {
  const qc = useQueryClient()
  const handler = useRef(onRow)
  useEffect(() => {
    handler.current = onRow
  }, [onRow])
  useEffect(() => {
    let channel: ReturnType<ReturnType<typeof getEcho>['private']>
    try {
      channel = getEcho().private('system')
    } catch {
      return undefined
    }
    const seen = new Set<string>()
    const listener = (e: NotificationRow | undefined) => {
      if (!e || typeof e.id !== 'string' || typeof e.event !== 'string') return
      if (e.env && e.env !== env) return
      if (seen.has(e.id)) return
      seen.add(e.id)
      if (!addRow(qc, env, e)) return
      if (!qc.getQueryData(notificationsKey(env))) void qc.invalidateQueries({ queryKey: notificationsKey(env) })
      handler.current(e)
    }
    channel.listen('.notification.created', listener)
    return () => {
      channel.stopListening('.notification.created', listener)
    }
  }, [qc, env])
}
