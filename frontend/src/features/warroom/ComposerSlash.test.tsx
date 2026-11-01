import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { LiveStatus } from '../../lib/echo'
import type { WarroomThread } from './api'
import { drafts, useOutbox } from './live'
import warroom from './routes'

/*
 * ATL-163: the composer's slash commands. Each test types a command into the real War Room
 * composer and checks which API calls went out (fetch is faked).
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


const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const HEALTH = {
  data: {
    env: 'demo',
    as_of: '2026-10-29T08:00:00Z',
    trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
    breakers: [],
    market_data: { max_age_seconds: 30, redis_available: true, stale: [], instruments: [] },
    private_ws: { redis_available: true, connected: true, last_msg_at: null, login_at: null, age_seconds: 1, stale: false },
    reconciliation: { last_success_at: null, reconciled_at: null, last_error: null },
    account_sync: { last_success_at: null, reconciled_at: null, last_error: null },
  },
}

const WATCHLIST = {
  id: 'wl-1',
  date: '2026-10-29',
  version: 1,
  status: 'published',
  created_at: '2026-10-29T05:00:00Z',
  edit_window_ends_at: '2026-10-29T08:10:00Z',
  edit_window_open: true,
  message_id: null,
  items: [],
  removed_items: [],
}

interface Call {
  method: string
  path: string
  search: string
  body: unknown
}

function fakeBackend(over: { settings?: Record<string, unknown> } = {}) {
  const calls: Call[] = []
  let version = 7
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : null
    if (url.pathname !== '/sanctum/csrf-cookie') calls.push({ method, path: url.pathname, search: url.search, body })
    switch (`${method} ${url.pathname}`) {
      case 'GET /sanctum/csrf-cookie':
        return reply(204)
      case 'GET /api/warroom/threads':
        return reply(200, { data: [MAIN] })
      case 'GET /api/warroom/messages':
        return reply(200, { data: [], meta: { next_cursor: null } })
      case 'POST /api/warroom/messages': {
        const b = body as { content_md: string; thread_id: string }
        return reply(201, {
          data: {
            id: 'srv-1', thread_id: b.thread_id, author_type: 'human', author: 'Mhmd', kind: 'text', content_md: b.content_md, card: null,
            reply_to: null, mentions: [], reasoning_md: null, tool_calls: null, tokens_in: null, tokens_out: null, cost_usd: null,
            created_at: '2026-10-29T08:00:01.000000Z',
          },
        })
      }
      case 'GET /api/system/health':
        return reply(200, HEALTH)
      case 'POST /api/system/pause':
        return reply(200, { ok: true, paused: true, reason: null, paused_at: '2026-10-29T08:00:00Z', by: 'owner', active_breakers: [], breakers_cleared: [] })
      case 'POST /api/cycles':
        return reply(202, { data: { request_id: 'r-1', env: 'demo', event_id: 'e-1' } })
      case 'GET /api/portfolio/orders':
        return reply(200, { data: [] })
      case 'GET /api/portfolio/positions':
        return reply(200, { data: [] })
      case 'GET /api/watchlists':
        return reply(200, { env: 'demo', date: '2026-10-29', watchlist: WATCHLIST })
      case 'POST /api/watchlists/wl-1/pin':
        return reply(200, { watchlist: WATCHLIST })
      case 'GET /api/settings':
        return reply(200, { data: { id: 's-1', env: 'demo', version, settings: { blocklist: ['DOGE-USDT-SWAP'], blocklist_until: [], ...over.settings }, presets: {}, updated_at: '2026-10-29T05:00:00Z' } })
      case 'PATCH /api/settings':
        version += 1
        return reply(200, { data: { id: 's-1', env: 'demo', version, settings: {}, presets: {}, updated_at: '2026-10-29T08:00:00Z' } })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return calls
}

const writes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET')

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

async function typeAndSend(text: string) {
  const u = user()
  await waitFor(() => expect(input()).not.toBeDisabled())
  // Escape closes the autocomplete, so Return sends exactly what was typed.
  await u.type(input(), text)
  await u.keyboard('{Escape}{Enter}')
  return u
}

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
  echo.reset()
  useOutbox.setState({ items: [] })
  drafts.clear()
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

describe('Composer slash commands', () => {
  it('/pause calls POST /api/system/pause, sends no message, and says trading is paused', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/pause')

    await waitFor(() => expect(writes(calls).map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/system/pause']))
    expect(input().value).toBe('')
    expect(await screen.findByTestId('wr-slash-note')).toHaveTextContent(i18n.t('warroom:slash.done.pause'))
  })

  it('/kill flatten opens the Stop sheet with close-all selected and never calls the kill endpoint', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/kill flatten')

    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByTestId('kill-flatten')).toBeChecked()
    // Close-all goes through the 1.5 s hold: there is no one-press Stop button.
    expect(within(sheet).queryByTestId('kill-go')).toBeNull()
    await new Promise((r) => setTimeout(r, 50))
    expect(calls.some((c) => c.path === '/api/system/kill')).toBe(false)
    expect(writes(calls)).toEqual([])
  })

  it('/kill opens the Stop sheet with close-all off', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/kill')

    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByTestId('kill-flatten')).not.toBeChecked()
    expect(within(sheet).getByTestId('kill-go')).toBeInTheDocument()
    expect(calls.some((c) => c.path === '/api/system/kill')).toBe(false)
  })

  it('/block XAU-USDT-SWAP today PATCHes the blocklist with an expiry at 23:59:59 Baghdad today', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/block xau-usdt-swap today')

    await waitFor(() => expect(writes(calls)).toHaveLength(1))
    const patch = writes(calls)[0]!
    expect(`${patch.method} ${patch.path}`).toBe('PATCH /api/settings')
    // NOW is 2026-10-29 11:00 Baghdad: the block ends at the end of that Baghdad day.
    expect(patch.body).toEqual({
      env: 'demo',
      version: 7,
      changes: {
        blocklist: ['DOGE-USDT-SWAP', 'XAU-USDT-SWAP'],
        blocklist_until: { 'XAU-USDT-SWAP': '2026-10-29T23:59:59+03:00' },
      },
    })
    expect(await screen.findByTestId('wr-slash-note')).toHaveTextContent('XAU-USDT-SWAP is blocked until 23:59 Baghdad today.')
  })

  it('/block uses the Baghdad day, not the UTC day, just after Baghdad midnight', async () => {
    vi.setSystemTime(Date.parse('2026-10-29T21:30:00Z')) // 00:30 on 30 Oct in Baghdad
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/block XAU-USDT-SWAP today')

    await waitFor(() => expect(writes(calls)).toHaveLength(1))
    expect((writes(calls)[0]!.body as { changes: { blocklist_until: Record<string, string> } }).changes.blocklist_until).toEqual({
      'XAU-USDT-SWAP': '2026-10-30T23:59:59+03:00',
    })
  })

  it('/pin SOL-USDT-SWAP calls the pin API on today’s watchlist', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/pin SOL-USDT-SWAP')

    await waitFor(() => expect(writes(calls)).toHaveLength(1))
    expect(writes(calls)[0]).toMatchObject({ method: 'POST', path: '/api/watchlists/wl-1/pin', body: { inst_id: 'SOL-USDT-SWAP' } })
    expect(calls.find((c) => c.path === '/api/watchlists')?.search).toContain('date=2026-10-29')
    expect(await screen.findByTestId('wr-slash-note')).toHaveTextContent('SOL-USDT-SWAP is pinned to today')
  })

  it('/cycle now calls POST /api/cycles', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/cycle now')

    await waitFor(() => expect(writes(calls).map((c) => `${c.method} ${c.path}`)).toEqual(['POST /api/cycles']))
  })

  it('/why, /research and /brief post owner messages to the right agents', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    const sent = () => writes(calls).filter((c) => c.path === '/api/warroom/messages').map((c) => (c.body as { content_md: string }).content_md)

    await typeAndSend('/why 5f0c2a8e-1111-4222-8333-944455556666')
    await waitFor(() => expect(sent()).toEqual(['/why 5f0c2a8e-1111-4222-8333-944455556666']))

    await typeAndSend('/research nvda-usdt-swap')
    await waitFor(() => expect(sent()[1]).toBe('@Technical @News Research NVDA-USDT-SWAP: the technical setup and the news brief.'))

    await typeAndSend('/brief')
    await waitFor(() => expect(sent()[2]).toMatch(/^@Orchestrator /))
  })

  it('"/foo" shows the help text from the locale file and sends nothing', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/foo')

    const help = await screen.findByTestId('wr-slash-help')
    expect(help).toHaveTextContent(i18n.t('warroom:slash.help.unknown', { cmd: '/foo' }).replace(/<\/?c>/g, ''))
    expect(help).toHaveTextContent(i18n.t('warroom:slash.cmd.block'))
    expect(input().value).toBe('/foo')
    await new Promise((r) => setTimeout(r, 50))
    expect(writes(calls)).toEqual([])
  })

  it('a known command with wrong arguments shows its usage and sends nothing', async () => {
    const calls = fakeBackend()
    renderWarRoom()
    await typeAndSend('/block XAU-USDT-SWAP')

    expect(await screen.findByTestId('wr-slash-help')).toHaveTextContent('Type it as /block <instId> today.')
    expect(writes(calls)).toEqual([])
  })

  it('autocompletes: "/ki" lists /kill and /kill flatten; Tab writes the highlighted one', async () => {
    fakeBackend()
    renderWarRoom()
    const u = user()
    await waitFor(() => expect(input()).not.toBeDisabled())
    await u.type(input(), '/ki')

    const list = screen.getByTestId('wr-slash-picker')
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual([
      `/kill${i18n.t('warroom:slash.cmd.kill')}`,
      `/kill flatten${i18n.t('warroom:slash.cmd.killFlatten')}`,
    ])
    await u.keyboard('{ArrowDown}{Tab}')
    expect(input().value).toBe('/kill flatten')
    expect(screen.queryByTestId('wr-slash-picker')).toBeNull()
  })

  it('shows the help in Arabic from the Arabic locale', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend()
    renderWarRoom()
    await typeAndSend('/foo')
    expect(await screen.findByTestId('wr-slash-help')).toHaveTextContent('ليس أمرًا')
  })
})
