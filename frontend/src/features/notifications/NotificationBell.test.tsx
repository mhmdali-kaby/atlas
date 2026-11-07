import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessagesSquare, Wallet } from 'lucide-react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../app/AppShell'
import { buildShellRegistry, type FeatureModule } from '../../app/feature'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { Toaster, useToasts } from '../../ui'
import { RequireAuth } from '../auth/RequireAuth'
import type { NotificationRow } from './api'

/*
 * ATL-217: notification.created on private-system becomes one toast, one more unread on the bell,
 * and a browser Notification only when its Browser switch was on, permission is granted and Atlas
 * is in the background. ATL-180's proposal event no longer toasts by itself.
 */

type Listener = (e: unknown) => void
const listeners = new Map<string, Set<Listener>>()

vi.mock('../../lib/echo', () => ({
  getEcho: () => ({
    private: (name: string) => ({
      listen(event: string, cb: Listener) {
        const key = `${name}:${event}`
        if (!listeners.has(key)) listeners.set(key, new Set())
        listeners.get(key)!.add(cb)
      },
      stopListening(event: string, cb: Listener) {
        listeners.get(`${name}:${event}`)?.delete(cb)
      },
    }),
  }),
  useLiveStatus: () => 'connected',
  hasConnected: () => true,
  reconnectEcho() {},
}))

function emit(channel: string, event: string, payload: unknown) {
  act(() => {
    for (const cb of listeners.get(`${channel}:${event}`) ?? []) cb(payload)
  })
}

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const HEALTH = {
  env: 'demo',
  as_of: '2026-10-31T10:00:00Z',
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

function row(id: string, over: Partial<NotificationRow> = {}): NotificationRow {
  return {
    id,
    env: 'demo',
    event: 'order_filled',
    severity: 'info',
    title_key: 'notifications.events.order_filled.title',
    params: { order_id: 'o1', proposal_id: 'p1', inst_id: 'XAU-USDT-SWAP', side: 'buy', purpose: 'entry', filled_sz: '3', avg_px: '2650.1' },
    link: '/portfolio/orders?inst_id=XAU-USDT-SWAP',
    channels: { in_app: true, browser: true, telegram: false },
    read_at: null,
    created_at: new Date().toISOString(),
    ...over,
  }
}

function fakeBackend(initial: NotificationRow[], unread = initial.filter((r) => !r.read_at).length) {
  const state = { list: initial, unread, waiting: [] as unknown[] }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(API_URL, '')
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/system/health') return reply(200, { data: HEALTH })
      if (path.startsWith('/api/proposals?')) return reply(200, { data: state.waiting, links: {}, meta: {} })
      if (path.startsWith('/api/notifications?')) {
        return reply(200, { data: state.list, meta: { env: 'demo', per_page: 50, unread_count: state.unread, next_cursor: null, prev_cursor: null } })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

const FEATURES: FeatureModule[] = [
  { routes: [], area: 'warroom', nav: { path: '/warroom', labelKey: 'shell:area.warroom', order: 1, icon: MessagesSquare } },
  { routes: [], area: 'portfolio', nav: { path: '/portfolio', labelKey: 'shell:area.portfolio', order: 10, icon: Wallet } },
]
const SHELL = buildShellRegistry(FEATURES)

function mount(url = '/portfolio') {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <RequireAuth>
            <AppShell shell={SHELL} />
          </RequireAuth>
        ),
        children: [
          { path: 'warroom', element: <p>War Room page</p> },
          { path: 'portfolio', element: <p>Portfolio page</p> },
          { path: 'portfolio/orders', element: <p>Orders page</p> },
        ],
      },
    ],
    { initialEntries: [url] },
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>,
  )
  return router
}

const sleep = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)))

class FakeNotification {
  static permission: NotificationPermission = 'granted'
  static requestPermission = vi.fn(async () => FakeNotification.permission)
  static sent: { title: string; body?: string; tag?: string }[] = []
  onclick: (() => void) | null = null
  constructor(title: string, options?: { body?: string; tag?: string }) {
    FakeNotification.sent.push({ title, body: options?.body, tag: options?.tag })
  }
  close() {}
}
const NotificationSpy = vi.fn()

function inBackground(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  vi.spyOn(document, 'hasFocus').mockReturnValue(!hidden)
}

beforeEach(async () => {
  // jsdom lacks pointer capture, which Radix Toast's swipe handler reads on pointer up.
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => undefined
  resetCsrfForTests()
  localStorage.clear()
  listeners.clear()
  useToasts.setState({ toasts: [] })
  FakeNotification.permission = 'granted'
  FakeNotification.sent = []
  NotificationSpy.mockReset()
  vi.stubGlobal(
    'Notification',
    new Proxy(FakeNotification, {
      construct(target, args: [string, { body?: string; tag?: string }?]) {
        NotificationSpy(...args)
        return new target(...args)
      },
    }),
  )
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  inBackground(false)
  await i18n.changeLanguage('en')
})

async function ready() {
  const bell = await screen.findByTestId('nt-bell')
  await sleep(50)
  return bell
}

describe('a notification.created event', () => {
  it('shows one toast and increments the unread count', async () => {
    fakeBackend([row('n1', { read_at: '2026-11-01T10:00:00Z' }), row('n2')])
    mount()
    const bell = await ready()
    await waitFor(() => expect(bell).toHaveAccessibleName('Notifications, 1 unread'))
    expect(screen.getByTestId('nt-badge')).toHaveTextContent('1')
    // Rows already there when the app opens are not toasted.
    expect(useToasts.getState().toasts).toHaveLength(0)

    emit('system', '.notification.created', row('n3', { params: { ...row('n3').params, filled_sz: '300', avg_px: '262.40', inst_id: 'AAPL-USDC-SWAP' } }))
    await screen.findByText('Order filled')
    expect(useToasts.getState().toasts).toHaveLength(1)
    expect(document.body).toHaveTextContent('Bought ⁦300⁩ contracts of ⁦AAPL-USDC-SWAP⁩ at ⁦262.40⁩.')
    expect(bell).toHaveAccessibleName('Notifications, 2 unread')
    expect(screen.getByTestId('nt-badge')).toHaveTextContent('2')

    // The same row again (a reconnect) changes nothing.
    emit('system', '.notification.created', row('n3'))
    await sleep(50)
    expect(useToasts.getState().toasts).toHaveLength(1)
    expect(bell).toHaveAccessibleName('Notifications, 2 unread')
  })

  it('does not toast when its In Atlas switch was off, but still counts it', async () => {
    fakeBackend([])
    mount()
    const bell = await ready()
    emit('system', '.notification.created', row('n1', { channels: { in_app: false, browser: false, telegram: false } }))
    await waitFor(() => expect(bell).toHaveAccessibleName('Notifications, 1 unread'))
    expect(useToasts.getState().toasts).toHaveLength(0)
  })

  it('a critical row stays until closed and says Urgent; the bell counts it as urgent', async () => {
    fakeBackend([])
    mount()
    const bell = await ready()
    emit(
      'system',
      '.notification.created',
      row('n1', { event: 'circuit_breaker', severity: 'critical', params: { name: 'daily_loss', reason: 'x', since: '', self_clearing: true }, link: '/warroom' }),
    )
    await screen.findByText('Trading paused by a circuit breaker')
    expect(screen.getByText('Today’s losses reached your daily limit. It clears by itself.')).toBeInTheDocument()
    expect(screen.getByText('Urgent')).toBeInTheDocument()
    expect(useToasts.getState().toasts[0]?.durationMs).toBeNull()
    expect(bell).toHaveAccessibleName('Notifications, 1 unread, 1 urgent')
  })

  it("ATL-180's proposal event and its notification make one toast, not two", async () => {
    const state = fakeBackend([])
    mount()
    await ready()
    const at = new Date(Date.now() + 354_000).toISOString()
    state.waiting = [
      { id: 'p9', env: 'demo', version: 1, parent_id: null, author: 'strategist', inst_id: 'SOL-USDT-SWAP', side: 'buy', status: 'awaiting_owner', approval_expires_at: at },
    ]
    emit('warroom', '.proposal.awaiting_owner', {
      type: 'proposal.awaiting_owner',
      env: 'demo',
      payload: { proposal_id: 'p9', version: 1, inst_id: 'SOL-USDT-SWAP', side: 'buy', decision: 'awaiting_owner', approval_expires_at: at },
    })
    emit(
      'system',
      '.notification.created',
      row('n9', {
        event: 'proposal_awaiting_approval',
        severity: 'warning',
        params: { proposal_id: 'p9', inst_id: 'SOL-USDT-SWAP', side: 'buy', kind: 'entry', final_notional_usd: '2997.79', approval_expires_at: at },
        link: '/warroom?proposal=p9',
      }),
    )
    await screen.findByText('Trade waiting for your approval')
    expect(await screen.findByTestId('approval-pill')).toHaveTextContent('1 to approve')
    await sleep(150)
    expect(useToasts.getState().toasts).toHaveLength(1)
    expect(document.body).toHaveTextContent(/Long ⁦SOL-USDT-SWAP⁩, ⁦\$2,997\.79⁩\. Approve by/)
    expect(await screen.findByText(/^⁦5:5\d⁩ left$/)).toBeInTheDocument()
  })

  it('the toast action opens the link and marks the row read', async () => {
    fakeBackend([])
    const router = mount()
    const bell = await ready()
    emit('system', '.notification.created', row('n1'))
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'View order' }))
    await waitFor(() => expect(router.state.location.pathname + router.state.location.search).toBe('/portfolio/orders?inst_id=XAU-USDT-SWAP'))
    expect(bell).toHaveAccessibleName('Notifications, none unread')
    expect(useToasts.getState().toasts).toHaveLength(0)
    const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST').map(([u]) => String(u).replace(API_URL, ''))
    expect(posts).toContain('/api/notifications/n1/read')
  })
})

describe('the browser notification', () => {
  it('is raised once with permission granted and the Browser switch on, while Atlas is in the background', async () => {
    fakeBackend([])
    mount()
    await ready()
    inBackground(true)
    emit('system', '.notification.created', row('n1'))
    await screen.findByText('Order filled')
    expect(NotificationSpy).toHaveBeenCalledTimes(1)
    expect(FakeNotification.sent[0]).toEqual({
      title: 'Order filled',
      body: 'Bought ⁦3⁩ contracts of ⁦XAU-USDT-SWAP⁩ at ⁦2,650.1⁩.',
      tag: 'n1',
    })
  })

  it('is not raised when the Browser switch was off', async () => {
    fakeBackend([])
    mount()
    await ready()
    inBackground(true)
    emit('system', '.notification.created', row('n1', { channels: { in_app: true, browser: false, telegram: false } }))
    await screen.findByText('Order filled')
    expect(NotificationSpy).not.toHaveBeenCalled()
  })

  it('is not raised when permission is denied, and nothing asks for it', async () => {
    FakeNotification.permission = 'denied'
    fakeBackend([])
    mount()
    await ready()
    inBackground(true)
    emit('system', '.notification.created', row('n1'))
    await screen.findByText('Order filled')
    expect(NotificationSpy).not.toHaveBeenCalled()
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
  })

  it('is not raised while Atlas is in front (the toast shows it)', async () => {
    fakeBackend([])
    mount()
    await ready()
    inBackground(false)
    emit('system', '.notification.created', row('n1'))
    await screen.findByText('Order filled')
    expect(NotificationSpy).not.toHaveBeenCalled()
  })

  it('is only asked for from the list, by a button press', async () => {
    FakeNotification.permission = 'default'
    fakeBackend([row('n1')])
    mount()
    const bell = await ready()
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
    const user = userEvent.setup()
    await user.click(bell)
    await user.click(await screen.findByRole('button', { name: 'Allow browser alerts' }))
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1)
  })
})
