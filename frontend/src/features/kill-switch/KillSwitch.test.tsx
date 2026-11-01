import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessagesSquare, Plug, Wallet } from 'lucide-react'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../app/AppShell'
import { buildShellRegistry, type FeatureModule } from '../../app/feature'
import { RequireAuth } from '../auth/RequireAuth'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { HOLD_MS, type KillReport, type SystemHealth } from './api'
import { KillSwitchButton } from './KillSwitch'
import { TradingBanner } from './TradingBanner'

vi.mock('../../lib/echo', () => ({
  getEcho: () => ({ private: () => ({ listen() {}, stopListening() {} }) }),
  useLiveStatus: () => 'connected',
  hasConnected: () => true,
  reconnectEcho() {},
}))

const OWNER = {
  id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad',
  name: 'Mohammed Ali',
  email: 'owner@atlas.test',
  locale: 'en',
  theme: 'system',
}

const RUNNING: SystemHealth = {
  env: 'demo',
  as_of: '2026-10-31T10:00:00Z',
  trading: {
    paused: false,
    reason: null,
    paused_at: null,
    accepting_new_orders: true,
  },
  breakers: [],
}

const DAILY_LOSS: SystemHealth = {
  ...RUNNING,
  trading: {
    paused: true,
    reason: 'Circuit breaker: daily_loss',
    paused_at: '2026-10-31T09:55:00Z',
    accepting_new_orders: false,
  },
  breakers: [
    {
      name: 'daily_loss',
      reason: 'Today’s loss reached 4.1% of equity, over the 4% daily limit.',
      since: '2026-10-31T09:55:00Z',
      self_clearing: false,
      detail: {},
    },
  ],
}

const REPORT: KillReport = {
  ok: false,
  env: 'demo',
  flatten: true,
  at: '2026-10-31T10:00:00Z',
  paused: true,
  cancelled: 3,
  cancelled_orders: 2,
  cancelled_algos: 1,
  already_done: 0,
  closed: 2,
  stops_kept: 0,
  failed: [
    {
      kind: 'position',
      inst_id: 'AAPL-USDC-SWAP',
      ref: 'c1',
      action: 'failed',
      attempts: 3,
      code: '51008',
      message: 'Not enough margin to place this order.',
    },
    {
      kind: 'algo',
      inst_id: 'TSLA-USDT-SWAP',
      ref: '7009',
      action: 'failed',
      attempts: 3,
      code: '50001',
      message: 'The service is temporarily unavailable.',
      algo_kind: 'tp',
    },
  ],
  items: [
    {
      kind: 'order',
      inst_id: 'SOL-USDT-SWAP',
      ref: '1001',
      action: 'cancelled',
      attempts: 1,
    },
    {
      kind: 'order',
      inst_id: 'ETH-USDT-SWAP',
      ref: '1002',
      action: 'cancelled',
      attempts: 1,
    },
    {
      kind: 'algo',
      inst_id: 'BTC-USDT-SWAP',
      ref: '7001',
      action: 'cancelled',
      attempts: 1,
      algo_kind: 'tp',
    },
    {
      kind: 'position',
      inst_id: 'BTC-USDT-SWAP',
      ref: 'c2',
      action: 'closed',
      attempts: 1,
      pos_side: 'net',
    },
    {
      kind: 'position',
      inst_id: 'XAU-USDT-SWAP',
      ref: 'c3',
      action: 'closed',
      attempts: 1,
      pos_side: 'net',
    },
  ],
}

const KEPT_REPORT: KillReport = {
  ...REPORT,
  ok: true,
  flatten: false,
  closed: 0,
  cancelled: 2,
  stops_kept: 2,
  failed: [],
  items: [
    {
      kind: 'order',
      inst_id: 'SOL-USDT-SWAP',
      ref: '1001',
      action: 'cancelled',
      attempts: 1,
    },
    {
      kind: 'algo',
      inst_id: 'BTC-USDT-SWAP',
      ref: '7001',
      action: 'cancelled',
      attempts: 1,
      algo_kind: 'tp',
    },
    {
      kind: 'algo',
      inst_id: 'BTC-USDT-SWAP',
      ref: '7002',
      action: 'kept',
      attempts: 0,
      algo_kind: 'sl',
    },
    {
      kind: 'algo',
      inst_id: 'XAU-USDT-SWAP',
      ref: '7003',
      action: 'kept',
      attempts: 0,
      algo_kind: 'oco',
    },
  ],
}

/*
 * Real timers throughout: faking them leaves motion's frame loop waiting on a timer that
 * vi.useRealTimers() drops, and every later sheet then never finishes closing.
 */
const sleep = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)))

interface Call {
  method: string
  path: string
  body: unknown
}

function fakeBackend({
  health = RUNNING,
  report = KEPT_REPORT,
  killDown = false,
}: { health?: SystemHealth; report?: KillReport; killDown?: boolean } = {}) {
  const calls: Call[] = []
  const state = { health }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const body = typeof init.body === 'string' ? (JSON.parse(init.body) as unknown) : null
      calls.push({ method, path, body })
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), {
          status,
          headers: { 'Content-Type': 'application/json' },
        })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/system/health') return reply(200, { data: state.health })
      if (path === '/api/portfolio/orders') {
        return reply(200, {
          data: {
            env: 'demo',
            orders: [{ id: 'o1' }, { id: 'o2' }],
            algo_orders: [
              { kind: 'tp', protects: false },
              { kind: 'sl', protects: true },
              { kind: 'oco', protects: true },
            ],
          },
        })
      }
      if (path.startsWith('/api/portfolio/positions')) {
        return reply(200, {
          data: {
            taken_at: null,
            items: [
              {
                id: 'p1',
                inst_id: 'BTC-USDT-SWAP',
                side: 'long',
                upl_usd: '6561.25',
              },
              {
                id: 'p2',
                inst_id: 'XAU-USDT-SWAP',
                side: 'short',
                upl_usd: '-860',
              },
            ],
          },
        })
      }
      if (path === '/api/system/kill' && method === 'POST') {
        return killDown
          ? reply(503, {
              ok: false,
              error: 'trading_core_unavailable',
              message: 'trading_core is not answering.',
            })
          : reply(200, report)
      }
      if (path === '/api/system/resume' && method === 'POST') {
        state.health = RUNNING
        return reply(200, {
          ok: true,
          paused: false,
          active_breakers: [],
          breakers_cleared: ['daily_loss'],
        })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return {
    calls,
    kills: () => calls.filter((c) => c.path === '/api/system/kill'),
    resumes: () => calls.filter((c) => c.path === '/api/system/resume'),
  }
}

function withClient(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}

beforeEach(async () => {
  resetCsrfForTests()
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('the Stop trading button', () => {
  const FEATURES: FeatureModule[] = [
    {
      routes: [],
      area: 'warroom',
      nav: {
        path: '/warroom',
        labelKey: 'shell:area.warroom',
        order: 1,
        icon: MessagesSquare,
      },
    },
    {
      routes: [],
      area: 'portfolio',
      nav: {
        path: '/portfolio',
        labelKey: 'shell:area.portfolio',
        order: 10,
        icon: Wallet,
      },
    },
    {
      routes: [],
      area: 'settings',
      nav: {
        path: '/settings/connections',
        labelKey: 'shell:area.settings',
        order: 100,
        icon: Plug,
      },
    },
  ]
  const SHELL = buildShellRegistry(FEATURES)

  it.each([
    ['War Room', '/warroom'],
    ['Portfolio', '/portfolio'],
    ['Settings', '/settings/connections'],
  ])('is on the %s page', async (_, url) => {
    fakeBackend()
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
            {
              path: 'settings/connections',
              element: <p>Settings page</p>,
            },
          ],
        },
      ],
      { initialEntries: [url] },
    )
    withClient(<RouterProvider router={router} />)
    const toolbar = await screen.findByRole('banner')
    const button = within(toolbar).getByRole('button', {
      name: 'Stop trading: cancel orders and pause Atlas',
    })
    expect(button).toHaveTextContent('Stop trading')
    expect(button).toHaveAttribute('aria-haspopup', 'dialog')
  })
})

describe('the Stop sheet (decision 251)', () => {
  async function openSheet() {
    const user = userEvent.setup()
    await user.click(screen.getByTestId('kill-switch'))
    const sheet = await screen.findByRole('dialog', {
      name: 'Stop all trading?',
    })
    // The counts come from the orders and positions lists.
    await within(sheet).findByText(/Keep the/)
    return { user, sheet }
  }

  it('says what happens, then one press of Stop trading sends flatten false and keeps the stops', async () => {
    const api = fakeBackend()
    withClient(<KillSwitchButton />)
    const { user, sheet } = await openSheet()
    expect(sheet).toHaveTextContent('Cancel 2 open orders and 1 take-profit orders on the OKX demo account')
    expect(sheet).toHaveTextContent('Keep the 2 stops on OKX, so every open position stays protected')
    expect(sheet).toHaveTextContent('Pause new trades until you resume')
    // Focus starts on Cancel, never on the destructive action.
    expect(within(sheet).getByRole('button', { name: 'Cancel' })).toHaveFocus()

    await user.click(within(sheet).getByTestId('kill-go'))
    await waitFor(() => expect(api.kills()).toHaveLength(1))
    expect(api.kills()[0]!.body).toEqual({
      confirm: 'KILL',
      flatten: false,
    })
    const report = await screen.findByTestId('kill-report')
    expect(within(report).getByTestId('kill-kept')).toHaveTextContent('2 stops. Each open position keeps its stop on OKX.')
  })

  it('with close-all on, a release before 1.5 s sends nothing; holding the pointer 1.5 s sends flatten true', async () => {
    const api = fakeBackend()
    withClient(<KillSwitchButton />)
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('switch'))
    expect(sheet).toHaveTextContent('Also close all 2 positions now')
    expect(sheet).toHaveTextContent('Close all 2 positions with reduce-only market orders')
    expect(within(sheet).queryByTestId('kill-go')).toBeNull()
    const hold = within(sheet).getByTestId('kill-hold')
    expect(hold).toHaveTextContent('Hold to stop and close all')

    // A click is not a hold.
    await user.click(hold)
    fireEvent.pointerDown(hold, { button: 0, pointerId: 1 })
    await sleep(HOLD_MS - 300)
    fireEvent.pointerUp(hold, { button: 0, pointerId: 1 })
    await sleep(900)
    expect(sheet).toHaveTextContent('Released too early. Press and hold for 1.5 seconds.')
    expect(api.kills()).toHaveLength(0)

    fireEvent.pointerDown(hold, { button: 0, pointerId: 2 })
    expect(sheet).toHaveTextContent('Keep holding…')
    await sleep(HOLD_MS)
    await waitFor(() => expect(api.kills()).toHaveLength(1), {
      timeout: 1000,
    })
    expect(api.kills()[0]!.body).toEqual({
      confirm: 'KILL',
      flatten: true,
    })
  })

  it.each([' ', 'Enter'])('with close-all on, holding %j for 1.5 s sends flatten true; an early key release sends nothing', async (key) => {
    const api = fakeBackend()
    withClient(<KillSwitchButton />)
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('switch'))
    const hold = within(sheet).getByTestId('kill-hold')
    hold.focus()

    fireEvent.keyDown(hold, { key })
    await sleep(800)
    fireEvent.keyUp(hold, { key })
    await sleep(900)
    expect(api.kills()).toHaveLength(0)

    fireEvent.keyDown(hold, { key })
    // Auto-repeat while held does not restart the timer.
    await sleep(700)
    fireEvent.keyDown(hold, { key, repeat: true })
    await sleep(HOLD_MS - 700)
    await waitFor(() => expect(api.kills()).toHaveLength(1), {
      timeout: 1000,
    })
    expect(api.kills()[0]!.body).toEqual({
      confirm: 'KILL',
      flatten: true,
    })
  })

  it('reports the cancelled and closed counts and each failure’s message', async () => {
    fakeBackend({ report: REPORT })
    withClient(<KillSwitchButton />)
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByRole('switch'))
    fireEvent.pointerDown(within(sheet).getByTestId('kill-hold'), {
      button: 0,
      pointerId: 1,
    })
    await sleep(HOLD_MS)

    const report = await screen.findByTestId('kill-report')
    expect(screen.getByRole('dialog')).toHaveTextContent('Trading is paused')
    expect(within(report).getByTestId('kill-cancelled')).toHaveTextContent('Cancelled3 orders')
    expect(within(report).getByTestId('kill-closed')).toHaveTextContent('Closed2 positions')
    expect(within(report).getByTestId('kill-failed')).toHaveTextContent('Failed2 items')
    const failures = within(report).getAllByTestId('kill-failure')
    expect(failures).toHaveLength(2)
    expect(failures[0]).toHaveTextContent('Position close · AAPL-USDC-SWAP · 51008Not enough margin to place this order.')
    expect(failures[1]).toHaveTextContent('Take profit · TSLA-USDT-SWAP · 50001The service is temporarily unavailable.')
    expect(
      within(report).getByText('BTC-USDT-SWAP', {
        selector: '.ks-closed .ks-inst',
      }),
    ).toBeInTheDocument()

    // The cancelled list opens on demand.
    await user.click(within(report).getByRole('button', { name: /Show all/ }))
    expect(within(report).getByText('SOL-USDT-SWAP').closest('ul')).not.toHaveAttribute('hidden')
  })

  it('says plainly that nothing was stopped when trading_core is down', async () => {
    const api = fakeBackend({ killDown: true })
    withClient(<KillSwitchButton />)
    const { user, sheet } = await openSheet()
    await user.click(within(sheet).getByTestId('kill-go'))
    expect(await screen.findByText('Nothing was stopped')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveTextContent('Atlas couldn’t reach trading_core, so nothing was cancelled.')
    expect(screen.queryByTestId('kill-report')).toBeNull()
    expect(api.kills()).toHaveLength(1)
  })

  it('closes on Escape and returns focus to the button', async () => {
    fakeBackend()
    withClient(<KillSwitchButton />)
    const { user } = await openSheet()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), {
      timeout: 3000,
    })
    expect(screen.getByTestId('kill-switch')).toHaveFocus()
  })
})

describe('the trading banner', () => {
  it('shows a daily-loss breaker with its reason, and Resume calls /api/system/resume only after confirming', async () => {
    const api = fakeBackend({ health: DAILY_LOSS })
    const user = userEvent.setup()
    withClient(
      <>
        <KillSwitchButton />
        <TradingBanner />
      </>,
    )
    const banner = await screen.findByRole('region', {
      name: 'Trading status',
    })
    expect(banner).toHaveTextContent('Circuit breaker: daily loss limit')
    expect(banner).toHaveTextContent('Today’s loss reached 4.1% of equity, over the 4% daily limit.')
    expect(banner).toHaveTextContent('Since')
    expect(screen.getByTestId('kill-switch')).toHaveTextContent('Stopped')

    await user.click(within(banner).getByRole('button', { name: 'Resume trading' }))
    const sheet = await screen.findByRole('dialog', {
      name: 'Resume trading?',
    })
    expect(sheet).toHaveTextContent('The loss limit is still exceeded, so the next trade will trip the breaker again.')
    expect(api.resumes()).toHaveLength(0)

    await user.click(within(sheet).getByTestId('resume-go'))
    await waitFor(() => expect(api.resumes()).toHaveLength(1))
    expect(api.resumes()[0]!.method).toBe('POST')
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Trading status' })).toBeNull())
  })

  it('cancelling the Resume sheet sends nothing', async () => {
    const api = fakeBackend({ health: DAILY_LOSS })
    const user = userEvent.setup()
    withClient(<TradingBanner />)
    await user.click(await screen.findByRole('button', { name: 'Resume trading' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', {
        name: 'Cancel',
      }),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), {
      timeout: 3000,
    })
    expect(api.resumes()).toHaveLength(0)
  })

  it('shows paused-by-you with the time, and nothing while trading runs', async () => {
    fakeBackend({
      health: {
        ...RUNNING,
        trading: {
          paused: true,
          reason: null,
          paused_at: '2026-10-31T09:55:00Z',
          accepting_new_orders: false,
        },
      },
    })
    const { unmount } = withClient(<TradingBanner />)
    const banner = await screen.findByRole('region', {
      name: 'Trading status',
    })
    // 09:55 UTC is 12:55 in Baghdad.
    expect(banner).toHaveTextContent('Trading paused by you at 12:55')
    unmount()

    fakeBackend()
    withClient(<TradingBanner />)
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled())
    await act(async () => {})
    expect(screen.queryByRole('region', { name: 'Trading status' })).toBeNull()
  })

  it('reads in Arabic', async () => {
    fakeBackend({ health: DAILY_LOSS })
    await i18n.changeLanguage('ar')
    withClient(<TradingBanner />)
    const banner = await screen.findByRole('region', {
      name: 'حالة التداول',
    })
    expect(banner).toHaveTextContent('قاطع الحماية: حدّ الخسارة اليومي')
    expect(within(banner).getByRole('button', { name: 'استئناف التداول' })).toBeInTheDocument()
  })
})
