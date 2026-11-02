import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import type { WarroomMessage } from './api'

/*
 * A notification link's `?message=<id>` (ATL-220, design v18 "Autonomy and notifications" §5.6):
 * the message may already be in the thread's loaded page, or it may be older. `useMessageById`
 * backs both the War Room page's thread redirect and the Feed's own "load the page that holds
 * it" lookup, sharing one cached request (GET /api/warroom/messages/{id}, added by ATL-220).
 */

export const MESSAGE_PARAM = 'message'

export function messageParam(search: string): string | null {
  const id = new URLSearchParams(search).get(MESSAGE_PARAM)
  return id && id.trim() !== '' ? id : null
}

export const messageByIdKey = (env: TradingEnv, id: string) => ['warroom', 'message', env, id] as const

/**
 * The target message by id, only when asked for (`id` non-null). 404 or another env's message
 * both surface as a 404 `ApiError`, which the caller treats as "not found" either way.
 */
export function useMessageById(env: TradingEnv, id: string | null) {
  return useQuery<WarroomMessage, ApiError>({
    queryKey: messageByIdKey(env, id ?? ''),
    enabled: id !== null,
    queryFn: ({ signal }) =>
      api.get<{ data: WarroomMessage }>(`/api/warroom/messages/${encodeURIComponent(id ?? '')}?env=${env}`, { signal }).then((r) => r.data),
    staleTime: 60_000,
    retry: (count, err) => err.status !== 404 && count < 2,
  })
}
