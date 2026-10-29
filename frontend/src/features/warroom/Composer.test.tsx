import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { LiveStatus } from '../../lib/echo'
import type { WarroomMessage, WarroomThread } from './api'
import { drafts, localMessages, useOutbox } from './live'
import warroom from './routes'

/*
 * ATL-40: the composer, optimistic send with failure and retry, live inserts over Echo (faked
 * here; the real socket is covered by e2e/flows/warroom.live.spec.ts) and the connection banner.
 */

const echo = vi.hoisted(() => {
  type Listener = (payload: unknown) => void
  const listeners = new Map<string, Set<Listener>>()
  const statusListeners = new Set<() => void>()
  const state = { status: 'connected' as LiveStatus, everConnected: true, reconnects: 0 }
  return {
    state,
    emit(event: string, payload: unknown) {
      for (const l of listeners.get(event) ?? []) l(payload)
    },
    setStatus(next: LiveStatus) {
      state.status = next
      if (next === 'connected') state.everConnected = true
      for (const l of statusListeners) l()
    },
    reset() {
      listeners.clear()
      state.status = 'connected'
      state.everConnected = true
      state.reconnects = 0
    },
    module: {
      getEcho: () => ({
        private: () => ({
          listen(event: string, cb: Listener) {
            if (!listeners.has(event)) listeners.set(event, new Set())
            listeners.get(event)!.add(cb)
          },
          stopListening(event: string, cb: Listener) {
            listeners.get(event)?.delete(cb)
          },
        }),
      }),
      hasConnected: () => state.everConnected,
      reconnectEcho: () => {
        state.reconnects += 1
      },
    },
    subscribe(cb: () => void) {
      statusListeners.add(cb)
      return () => {
        statusListeners.delete(cb)
      }
    },
  }
})

vi.mock('../../lib/echo', async () => {
  const { useSyncExternalStore } = await import('react')
  return { ...echo.module, useLiveStatus: () => useSyncExternalStore(echo.subscribe, () => echo.state.status) }
})

const NOW = Date.parse('2026-10-29T08:00:00Z')
const MAIN: WarroomThread = {
  id: 'main-1',
  kind: 'main',
  key: 'main',
  title: 'Main',
  last_message_at: '2026-10-29T05:00:00.123456Z',
  created_at: '2026-07-14T15:02:00.000000Z',
}

const msg = (over: Partial<WarroomMessage>): WarroomMessage => ({
  id: 'm-1',
  thread_id: 'main-1',
  author_type: 'agent',
  author: 'historian',
  kind: 'text',
  content_md: 'History weakly supports it.',
  card: null,
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: null,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  created_at: '2026-10-29T05:00:00.123456Z',
  ...over,
})

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

interface Backend {
  /** Stored messages, oldest first. */
  stored: WarroomMessage[]
  /** Status for each POST in turn (the last one repeats). */
  posts: number[]
}

function fakeBackend(b: Backend) {
  let n = 0
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    if (url.pathname === '/sanctum/csrf-cookie') return reply(204)
    if (url.pathname === '/api/warroom/threads') return reply(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages' && (init?.method ?? 'GET') === 'GET') {
      return reply(200, { data: [...b.stored].reverse(), meta: { next_cursor: null } })
    }
    if (url.pathname === '/api/warroom/messages' && init?.method === 'POST') {
      const status = b.posts[Math.min(n, b.posts.length - 1)]!
      n += 1
      if (status !== 201) return reply(status, { message: 'Server Error' })
      const body = JSON.parse(String(init.body)) as { content_md: string; thread_id: string; reply_to: string | null }
      const row = msg({
        id: `srv-${n}`,
        thread_id: body.thread_id,
        author_type: 'human',
        author: 'Mhmd',
        content_md: body.content_md.trim(),
        reply_to: body.reply_to ?? null,
        mentions: /@Historian\b/.test(body.content_md) ? ['historian'] : [],
        created_at: `2026-10-29T07:59:${String(10 + n).padStart(2, '0')}.000000Z`,
      })
      b.stored.push(row)
      return reply(201, { data: row })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const posts = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.filter(([input, init]) => new URL(String(input)).pathname === '/api/warroom/messages' && init?.method === 'POST')

function renderWarRoom() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    warroom.routes.map((r) => ({ ...r, path: `/${r.path}`, element: <>{r.element}</> })),
    { initialEntries: ['/warroom'] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return client
}

const input = () => screen.getByTestId('wr-composer-input') as HTMLTextAreaElement
const user = () => userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
  echo.reset()
  useOutbox.setState({ items: [] })
  drafts.clear()
  // jsdom lays nothing out; give the virtualised feed a viewport and its rows a height.
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(800)
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(720)
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(60)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('Composer', () => {
  it('sends on Return, adds a line on Shift-Return, and shows the stored message once', async () => {
    const b: Backend = { stored: [msg({})], posts: [201] }
    const fetch = fakeBackend(b)
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())

    await u.type(input(), 'hello{Shift>}{Enter}{/Shift}team')
    expect(input().value).toBe('hello\nteam')
    await u.keyboard('{Enter}')

    expect(input().value).toBe('')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body))).toEqual({ env: 'demo', thread_id: 'main-1', content_md: 'hello\nteam', reply_to: null })
    const feed = screen.getByTestId('wr-feed')
    await waitFor(() => expect(within(feed).queryByTestId('wr-sending')).toBeNull())
    // The broadcast copy of the same row is deduplicated by id.
    act(() => echo.emit('.warroom.message', b.stored.at(-1)))
    expect(within(feed).getAllByText(/hello/)).toHaveLength(1)
  })

  it('shows a failed send with Try again, and retrying posts it exactly once', async () => {
    const fetch = fakeBackend({ stored: [msg({})], posts: [500, 201] })
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())

    await u.type(input(), 'hello team{Enter}')

    const failed = await screen.findByTestId('wr-failed')
    expect(failed).toHaveAttribute('role', 'alert')
    expect(failed).toHaveTextContent('Not sent')
    expect(failed.closest('[data-msg-id]')).toHaveClass('is-failed')
    expect(posts(fetch)).toHaveLength(1)

    const retry = within(failed).getByTestId('wr-retry')
    // A double press still sends once: the second finds the message already sending.
    fireEvent.click(retry)
    fireEvent.click(retry)

    await waitFor(() => expect(screen.queryByTestId('wr-failed')).toBeNull())
    await waitFor(() => expect(screen.queryByTestId('wr-sending')).toBeNull())
    expect(posts(fetch)).toHaveLength(2)
    expect(within(screen.getByTestId('wr-feed')).getAllByText('hello team')).toHaveLength(1)
  })

  it('counts from 3,500 characters and refuses to send over 4,000', async () => {
    fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    await waitFor(() => expect(input()).not.toBeDisabled())

    fireEvent.change(input(), { target: { value: 'a'.repeat(3499) } })
    expect(screen.getByTestId('wr-count')).toHaveTextContent('')
    fireEvent.change(input(), { target: { value: 'a'.repeat(3612) } })
    expect(screen.getByTestId('wr-count')).toHaveTextContent('3,612 / 4,000')
    expect(screen.getByTestId('wr-count')).toHaveClass('is-near')
    expect(screen.getByTestId('wr-send')).not.toBeDisabled()
    fireEvent.change(input(), { target: { value: 'a'.repeat(4092) } })
    expect(screen.getByTestId('wr-count')).toHaveTextContent('92 over the 4,000 limit')
    expect(screen.getByTestId('wr-send')).toBeDisabled()
    expect(screen.getByTestId('wr-composer')).toHaveClass('is-over')
  })

  it('opens the agent picker on "@", filters it, and inserts "@Historian " on Return', async () => {
    const fetch = fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())

    await u.type(input(), 'ask @His')
    const picker = screen.getByTestId('wr-picker')
    const options = within(picker).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual([expect.stringContaining('Historian')])
    expect(input()).toHaveAttribute('aria-activedescendant', options[0]!.id)

    await u.keyboard('{Enter}')
    expect(input().value).toBe('ask @Historian ')
    expect(screen.queryByTestId('wr-picker')).toBeNull()

    await u.type(input(), 'hi{Enter}')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body)).content_md).toBe('ask @Historian hi')
  })

  it('sends "/" as plain text', async () => {
    const fetch = fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())
    await u.type(input(), '/pause{Enter}')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body)).content_md).toBe('/pause')
  })
})

/*
 * ATL-42: choosing Reply (from the hover action bar, here revealed the same way the pointer does
 * in the browser) puts the ReplyChip above the composer and posts `reply_to`; Esc and the chip's
 * × both clear it. The full keyboard path (focus a message, Tab into its actions, visible focus
 * ring) and the Arabic mirroring are e2e (warroom-reply.appearance.spec.ts) — jsdom doesn't
 * evaluate :focus-visible the way a real browser does.
 */
describe('Reply', () => {
  async function hoverAndOpenReply(feed: HTMLElement) {
    const target = within(feed).getByText('History weakly supports it.').closest('[data-msg-id]') as HTMLElement
    fireEvent.pointerMove(target, { clientX: 10, clientY: 10 })
    const replyButton = await within(feed).findByTestId('wr-action-reply')
    fireEvent.click(replyButton)
  }

  it('shows the quoted chip; sending "agreed" posts reply_to and renders with the quoted parent', async () => {
    const b: Backend = { stored: [msg({})], posts: [201] }
    const fetch = fakeBackend(b)
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())
    const feed = screen.getByTestId('wr-feed')

    await hoverAndOpenReply(feed)
    const chip = screen.getByTestId('wr-reply-chip')
    expect(chip).toHaveTextContent('Replying to Historian')
    expect(chip).toHaveTextContent('History weakly supports it.')
    expect(input()).toHaveFocus()

    await u.type(input(), 'agreed{Enter}')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body))).toMatchObject({ content_md: 'agreed', reply_to: 'm-1' })
    expect(screen.queryByTestId('wr-reply-chip')).toBeNull()

    const mine = within(feed).getByText('agreed').closest('[data-msg-id]') as HTMLElement
    expect(within(mine).getByText('History weakly supports it.')).toBeInTheDocument()
  })

  it('Esc clears the reply: the next message posts with no reply_to', async () => {
    const fetch = fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())
    const feed = screen.getByTestId('wr-feed')

    await hoverAndOpenReply(feed)
    expect(screen.getByTestId('wr-reply-chip')).toBeInTheDocument()
    await u.keyboard('{Escape}')
    expect(screen.queryByTestId('wr-reply-chip')).toBeNull()

    await u.type(input(), 'no reply here{Enter}')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body))).toMatchObject({ reply_to: null })
  })

  it("the chip's × clears the reply: the next message posts with no reply_to", async () => {
    const fetch = fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())
    const feed = screen.getByTestId('wr-feed')

    await hoverAndOpenReply(feed)
    fireEvent.click(screen.getByTestId('wr-reply-cancel'))
    expect(screen.queryByTestId('wr-reply-chip')).toBeNull()

    await u.type(input(), 'still no reply{Enter}')
    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    expect(JSON.parse(String(posts(fetch)[0]![1]!.body))).toMatchObject({ reply_to: null })
  })
})

describe('Local bubbles', () => {
  it('carry the signed-in owner as author and their send state', () => {
    const item = { tempId: 'local-1', env: 'demo' as const, threadId: 'main-1', content: 'hi', replyTo: null, state: 'failed' as const, createdAt: '2026-10-29T08:00:00.000Z' }
    const [m] = localMessages([item, { ...item, tempId: 'local-2', threadId: 'other' }], 'demo', 'main-1', [], 'Mhmd')
    expect(m).toMatchObject({ id: 'local-1', author_type: 'human', author: 'Mhmd', local: { tempId: 'local-1', state: 'failed' } })
  })
})

describe('Live updates', () => {
  it('inserts a broadcast message into the open feed once and announces it', async () => {
    fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    await waitFor(() => expect(input()).not.toBeDisabled())

    const live = msg({ id: 'm-live', author: 'market_scout', content_md: 'Watchlist ready: 3 picks', created_at: '2026-10-29T07:59:59.000000Z' })
    act(() => echo.emit('.warroom.message', live))
    act(() => echo.emit('.warroom.message', live))

    const feed = screen.getByTestId('wr-feed')
    expect(within(feed).getAllByText(/Watchlist ready/)).toHaveLength(1)
    expect(screen.getByTestId('wr-live')).toHaveTextContent('Market Scout: Watchlist ready: 3 picks')
  })

  it('ignores a message for another env’s thread', async () => {
    fakeBackend({ stored: [msg({})], posts: [201] })
    renderWarRoom()
    await waitFor(() => expect(input()).not.toBeDisabled())
    act(() => echo.emit('.warroom.message', msg({ id: 'm-other', thread_id: 'live-main', content_md: 'other env' })))
    expect(screen.queryByText('other env')).toBeNull()
  })

  it('shows the banner while disconnected, then refetches on reconnect and reports what was missed', async () => {
    const backend = { stored: [msg({})], posts: [201] }
    fakeBackend(backend)
    renderWarRoom()
    await waitFor(() => expect(input()).not.toBeDisabled())

    act(() => echo.setStatus('connecting'))
    const down = await screen.findByTestId('wr-banner-down')
    expect(down).toHaveTextContent('Live updates paused. Reconnecting…')
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(within(down).getByRole('button', { name: 'Reconnect now' }))
    expect(echo.state.reconnects).toBe(1)

    // Published while the socket was down: only the refetch can bring it in.
    backend.stored.push(msg({ id: 'm-missed', content_md: 'Missed while offline', created_at: '2026-10-29T07:30:00.000000Z' }))
    act(() => echo.setStatus('connected'))

    expect(await screen.findByText('Missed while offline')).toBeInTheDocument()
    expect(screen.queryByTestId('wr-banner-down')).toBeNull()
    expect(screen.getByTestId('wr-banner-back')).toHaveTextContent('Live again. 1 missed message caught up.')
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.queryByTestId('wr-banner-back')).toBeNull()
  })
})

