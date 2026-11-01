import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, type ApiError, useApiQuery } from '../../lib/api'
import { getEcho } from '../../lib/echo'

/*
 * The kill switch and the trading banner (ATL-149) talk to ATL-148's system API
 * (docs/handoff/ATL-148-system-api.md). Health is polled every 10 s and refetched on the
 * atlas.system events atlas:bridge broadcasts on private-system.
 */

export const HEALTH_KEY = ['system', 'health'] as const
export const HEALTH_POLL_MS = 10_000
/** Press-and-hold time for "stop and close all" (decision #251). */
export const HOLD_MS = 1500
/** Sent by the sheet, never typed by the owner (decision #251; the API refuses a kill without it). */
export const KILL_CONFIRM = 'KILL'

export interface Breaker {
  name: string
  reason: string
  since: string | null
  self_clearing: boolean
  detail?: Record<string, unknown> | null
}

export interface SystemHealth {
  env: 'demo' | 'live'
  as_of: string
  trading: { paused: boolean; reason: string | null; paused_at: string | null; accepting_new_orders: boolean }
  breakers: Breaker[]
}

export type KillAction = 'cancelled' | 'closed' | 'kept' | 'already_done' | 'failed'

export interface KillItem {
  kind: 'order' | 'algo' | 'position' | 'read'
  inst_id: string
  ref: string
  action: KillAction
  attempts: number
  code?: string
  message?: string
  pos_side?: string
  algo_kind?: 'sl' | 'tp' | 'oco' | 'trigger'
}

export interface KillReport {
  ok: boolean
  env: string
  flatten: boolean
  at: string
  paused: boolean
  cancelled: number
  cancelled_orders: number
  cancelled_algos: number
  already_done: number
  closed: number
  stops_kept: number
  failed: KillItem[]
  items: KillItem[]
}

export function useSystemHealth() {
  return useApiQuery<{ data: SystemHealth }>(HEALTH_KEY, '/api/system/health', {
    refetchInterval: HEALTH_POLL_MS,
    retry: false,
  })
}

/** Refetches health on the trading-state, kill and breaker events (ATL-147/ATL-145). */
export function useSystemEvents() {
  const qc = useQueryClient()
  useEffect(() => {
    let channel: ReturnType<ReturnType<typeof getEcho>['private']>
    try {
      channel = getEcho().private('system')
    } catch {
      return undefined
    }
    const refresh = () => void qc.invalidateQueries({ queryKey: HEALTH_KEY })
    const events = ['.system.trading_state', '.system.kill', '.system.circuit_breaker']
    for (const e of events) channel.listen(e, refresh)
    return () => {
      for (const e of events) channel.stopListening(e, refresh)
    }
  }, [qc])
}

/** What the sheet counts before the press: open orders, take-profits, stops and positions. */
export interface KillPreview {
  openOrders: number
  takeProfits: number
  stops: number
  positions: { id: string; inst_id: string; side: 'long' | 'short'; upl_usd: string | null }[]
}

interface OrdersPayload {
  data: { orders: unknown[]; algo_orders: { kind: string; protects: boolean }[] }
}
interface PositionsPayload {
  data: { items: { id: string; inst_id: string; side: 'long' | 'short'; upl_usd: string | null }[] }
}

export function useKillPreview(enabled: boolean, env: string) {
  return useQuery<KillPreview, ApiError>({
    queryKey: ['system', 'kill-preview', env],
    enabled,
    staleTime: 0,
    retry: false,
    queryFn: async ({ signal }) => {
      const [orders, positions] = await Promise.all([
        api.get<OrdersPayload>('/api/portfolio/orders', { signal }),
        api.get<PositionsPayload>(`/api/portfolio/positions?env=${env}`, { signal }),
      ])
      const algos = orders.data.algo_orders
      return {
        openOrders: orders.data.orders.length,
        stops: algos.filter((a) => a.protects).length,
        takeProfits: algos.filter((a) => !a.protects).length,
        positions: positions.data.items.map(({ id, inst_id, side, upl_usd }) => ({ id, inst_id, side, upl_usd })),
      }
    },
  })
}

export function postKill(flatten: boolean): Promise<KillReport> {
  return api.post<KillReport>('/api/system/kill', { confirm: KILL_CONFIRM, flatten })
}

export function postResume(): Promise<unknown> {
  return api.post('/api/system/resume')
}
