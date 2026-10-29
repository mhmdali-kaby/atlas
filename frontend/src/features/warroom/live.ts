import type { InfiniteData, QueryClient } from '@tanstack/react-query'
import { create } from 'zustand'
import { api } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { messagesKey, threadsKey, type MessagesPage, type WarroomMessage, type WarroomThread } from './api'

/*
 * How messages reach the feed without a reload (ATL-40):
 * - `upsertMessage` puts one WarroomMessageResource into the thread's infinite query by id — the
 *   POST's 201 body and the `.warroom.message` broadcast of the same row land once, whichever
 *   comes first — and moves the thread's `last_message_at`.
 * - The outbox holds the owner's messages that are sending or failed. They are drawn after the
 *   cached messages and never written into the query cache, so a refetch cannot lose a failed one.
 */

type Messages = InfiniteData<MessagesPage, string | null>

/** Newest first, like the API: created_at desc, then id desc. */
function newerFirst(a: WarroomMessage, b: WarroomMessage): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0
}

/**
 * Inserts `m` into its thread's cached pages (replacing a copy with the same id) and updates the
 * thread list. Returns false when the message is not for this env's threads, or already cached.
 */
export function upsertMessage(qc: QueryClient, env: TradingEnv, m: WarroomMessage): boolean {
  const threads = qc.getQueryData<{ data: WarroomThread[] }>(threadsKey(env))
  const thread = threads?.data.find((t) => t.id === m.thread_id)
  if (threads && !thread) {
    // A thread the bridge created on demand, or another env's: the list decides.
    void qc.invalidateQueries({ queryKey: threadsKey(env) })
    return false
  }

  let inserted = false
  qc.setQueryData<Messages>(messagesKey(env, m.thread_id), (data) => {
    if (!data || data.pages.length === 0) return data
    let found = false
    const pages = data.pages.map((page) => {
      if (!page.data.some((x) => x.id === m.id)) return page
      found = true
      return { ...page, data: page.data.map((x) => (x.id === m.id ? m : x)) }
    })
    if (found) return { ...data, pages }
    inserted = true
    const first = [...pages[0]!.data, m].sort(newerFirst)
    return { ...data, pages: [{ ...pages[0]!, data: first }, ...pages.slice(1)] }
  })

  if (thread && (!thread.last_message_at || thread.last_message_at < m.created_at)) {
    qc.setQueryData<{ data: WarroomThread[] }>(threadsKey(env), (d) =>
      d ? { ...d, data: d.data.map((t) => (t.id === m.thread_id ? { ...t, last_message_at: m.created_at } : t)) } : d,
    )
  }
  return inserted
}

/** Every message id currently cached for the env's threads. */
export function cachedIds(qc: QueryClient, env: TradingEnv): Set<string> {
  const ids = new Set<string>()
  for (const [, data] of qc.getQueriesData<Messages>({ queryKey: ['warroom', 'messages', env] })) {
    for (const page of data?.pages ?? []) for (const m of page.data) ids.add(m.id)
  }
  return ids
}

/** Composer drafts by `env:threadId`: they survive switching threads, for this page load. */
export const drafts = new Map<string, string>()

// ---- The outbox ---------------------------------------------------------------------------

export type SendState = 'sending' | 'failed'

export interface OutboxItem {
  tempId: string
  env: TradingEnv
  threadId: string
  content: string
  replyTo: string | null
  state: SendState
  /** Client clock, ISO; only orders the item among other local items. */
  createdAt: string
}

interface OutboxStore {
  items: OutboxItem[]
  add: (item: OutboxItem) => void
  set: (tempId: string, state: SendState) => void
  remove: (tempId: string) => void
}

export const useOutbox = create<OutboxStore>((setState) => ({
  items: [],
  add: (item) => setState((s) => ({ items: [...s.items, item] })),
  set: (tempId, state) => setState((s) => ({ items: s.items.map((i) => (i.tempId === tempId ? { ...i, state } : i)) })),
  remove: (tempId) => setState((s) => ({ items: s.items.filter((i) => i.tempId !== tempId) })),
}))

let counter = 0

async function post(qc: QueryClient, item: OutboxItem): Promise<void> {
  const { add, set, remove } = useOutbox.getState()
  if (!useOutbox.getState().items.some((i) => i.tempId === item.tempId)) add(item)
  else set(item.tempId, 'sending')
  try {
    const res = await api.post<{ data: WarroomMessage }>('/api/warroom/messages', {
      env: item.env,
      thread_id: item.threadId,
      content_md: item.content,
      reply_to: item.replyTo,
    })
    upsertMessage(qc, item.env, res.data)
    remove(item.tempId)
  } catch {
    set(item.tempId, 'failed')
  }
}

/** Posts an owner message: an optimistic bubble at once, the server's row when the API answers. */
export function sendMessage(qc: QueryClient, input: { env: TradingEnv; threadId: string; content: string; replyTo?: string | null }): Promise<void> {
  counter += 1
  return post(qc, {
    tempId: `local-${Date.now().toString(36)}-${counter}`,
    env: input.env,
    threadId: input.threadId,
    content: input.content,
    replyTo: input.replyTo ?? null,
    state: 'sending',
    createdAt: new Date().toISOString(),
  })
}

/** Posts a failed message again, once: a message already sending is left alone. */
export function retryMessage(qc: QueryClient, tempId: string): Promise<void> {
  const item = useOutbox.getState().items.find((i) => i.tempId === tempId)
  if (!item || item.state !== 'failed') return Promise.resolve()
  return post(qc, item)
}

export function discardMessage(tempId: string): void {
  useOutbox.getState().remove(tempId)
}

/** A message the feed draws for an outbox item: an owner `text` with its send state. */
export interface LocalMessage extends WarroomMessage {
  local: { tempId: string; state: SendState }
}

export function isLocal(m: WarroomMessage): m is LocalMessage {
  return 'local' in m && (m as LocalMessage).local !== undefined
}

/**
 * The thread's outbox items as feed messages. A sending item whose row already arrived over the
 * socket (the broadcast can beat the 201) is hidden, so the owner never sees it twice.
 */
export function localMessages(items: OutboxItem[], env: TradingEnv, threadId: string, cached: WarroomMessage[], ownerName: string): LocalMessage[] {
  const recent = cached.filter((m) => m.author_type === 'human').slice(-20)
  // Server and browser clocks differ; two minutes of slack only matters for a repeated text.
  const since = (i: OutboxItem) => new Date(Date.parse(i.createdAt) - 120_000).toISOString()
  return items
    .filter((i) => i.env === env && i.threadId === threadId)
    .filter((i) => i.state === 'failed' || !recent.some((m) => m.content_md === i.content.trim() && m.created_at >= since(i)))
    .map((i) => ({
      id: i.tempId,
      thread_id: i.threadId,
      author_type: 'human',
      author: ownerName,
      kind: 'text',
      content_md: i.content,
      card: null,
      reply_to: i.replyTo,
      mentions: [],
      reasoning_md: null,
      tool_calls: null,
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      created_at: i.createdAt,
      local: { tempId: i.tempId, state: i.state },
    }))
}
