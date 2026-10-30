import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query'
import { api, useApiQuery, type ApiError } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * The ATL-38 War Room API (docs/handoff/ATL-38-warroom-api.md). Every message is a
 * WarroomMessageResource; timestamps are ISO-8601 UTC with microseconds.
 */

export type ThreadKind = 'main' | 'instrument' | 'agent'

export interface WarroomThread {
  id: string
  kind: ThreadKind
  /** `main`, the instId, or the agent slug. */
  key: string
  title: string
  last_message_at: string | null
  created_at: string
}

export interface SystemAlertCard {
  severity?: 'info' | 'warning' | 'critical'
  title?: string
}

/**
 * One tool call of an agent run (ATL-88 final-message shape). `input` is the JSON object, or a
 * string cut by the runtime when `input_truncated`; `output` is the tool's result text.
 */
export interface ToolCall {
  id?: string | null
  name: string
  input: unknown
  input_truncated?: boolean
  as_of?: string | null
  output?: string | null
  output_truncated?: boolean
  is_error?: boolean | null
}

export interface WarroomMessage {
  id: string
  /** The agent run that wrote it, when the API carries it (the envelope does; ATL-93). */
  run_id?: string | null
  thread_id: string
  author_type: 'agent' | 'human' | 'system'
  author: string
  kind: string
  content_md: string
  card: (SystemAlertCard & Record<string, unknown>) | null
  reply_to: string | null
  mentions: string[]
  reasoning_md: string | null
  tool_calls: ToolCall[] | null
  tokens_in: number | null
  tokens_out: number | null
  cost_usd: string | null
  created_at: string
}

export interface MessagesPage {
  data: WarroomMessage[]
  meta: { next_cursor: string | null }
}

/** One page of the feed: 50 messages (the API's default; its maximum is 100). */
export const PAGE_SIZE = 50

export const threadsKey = (env: TradingEnv) => ['warroom', 'threads', env] as const
export const messagesKey = (env: TradingEnv, threadId: string) => ['warroom', 'messages', env, threadId] as const

export function useThreads(env: TradingEnv) {
  return useApiQuery<{ data: WarroomThread[] }>(threadsKey(env), `/api/warroom/threads?env=${env}`, {
    staleTime: 15_000,
  })
}

/**
 * The thread's messages, newest page first (the API's order). Each page is newest-first too;
 * `flattenMessages` turns the pages into one oldest-first list for the feed.
 */
export function useMessages(env: TradingEnv, threadId: string | undefined) {
  return useInfiniteQuery<MessagesPage, ApiError, InfiniteData<MessagesPage, string | null>, readonly unknown[], string | null>({
    queryKey: messagesKey(env, threadId ?? ''),
    enabled: Boolean(threadId),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) => {
      const query = new URLSearchParams({ env, thread_id: threadId ?? '', limit: String(PAGE_SIZE) })
      if (pageParam) query.set('cursor', pageParam)
      return api.get<MessagesPage>(`/api/warroom/messages?${query.toString()}`, { signal })
    },
    getNextPageParam: (last) => last.meta.next_cursor ?? undefined,
    staleTime: 15_000,
  })
}

/** Oldest first, each message once (a page boundary can never repeat a row, but be safe). */
export function flattenMessages(data: InfiniteData<MessagesPage, string | null> | undefined): WarroomMessage[] {
  if (!data) return []
  const seen = new Set<string>()
  const out: WarroomMessage[] = []
  for (let p = data.pages.length - 1; p >= 0; p--) {
    const page = data.pages[p]!.data
    for (let i = page.length - 1; i >= 0; i--) {
      const m = page[i]!
      if (seen.has(m.id)) continue
      seen.add(m.id)
      out.push(m)
    }
  }
  return out
}
