import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MessagesSquare, Wallet } from 'lucide-react'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../app/AppShell'
import { buildShellRegistry, type FeatureModule } from '../../app/feature'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { Toaster, useToasts } from '../../ui'
import { RequireAuth } from '../auth/RequireAuth'
import { formatTime } from '../warroom/time'
import { proposalParam } from '../warroom/useProposalFocus'
import { type AwaitingProposal } from './api'

/*
 * ATL-180: the approval pill in the toolbar and the "trade waiting for your approval" toast, on any
 * signed-in page. Real timers: motion's frame loop does not survive fake ones (see KillSwitch.test).
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

function waiting(id: string, msLeft: number, over: Partial<AwaitingProposal> = {}): AwaitingProposal {
  return {
    id,
    env: 'demo',
    version: 1,
    parent_id: null,
    author: 'strategist',
    inst_id: 'SOL-USDT-SWAP',
    side: 'buy',
    status: 'awaiting_owner',
    approval_expires_at: new Date(Date.now() + msLeft).toISOString(),
    ...over,
  }
}

function fakeBackend(initial: AwaitingProposal[]) {
  const state = { list: initial }
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
      if (path.startsWith('/api/proposals?')) {
        expect(path).toContain('status=awaiting_owner')
        return reply(200, { data: state.list, links: {}, meta: {} })
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

function WarRoomProbe() {
  const location = useLocation()
  return (
    <p data-testid="warroom-page" data-proposal={proposalParam(location.search) ?? ''}>
      War Room page
    </p>
  )
}

function mount(url: string) {
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
          { path: 'warroom', element: <WarRoomProbe /> },
          { path: 'portfolio', element: <p>Portfolio page</p> },
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

beforeEach(async () => {
  resetCsrfForTests()
  localStorage.clear()
  listeners.clear()
  useToasts.setState({ toasts: [] })
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('the approval pill', () => {
  it('reads "1 to approve 5:54" for one proposal and counts down', async () => {
    // 5:54 left, less a margin for the render, so the rounded-up seconds read 354.
    fakeBackend([waiting('p1', 354_000 - 300)])
    mount('/portfolio')
    const pill = await screen.findByTestId('approval-pill')
    expect(pill).toHaveTextContent('1 to approve5:54')
    expect(screen.getByTestId('approval-pill-label')).toHaveTextContent('1 to approve')
    expect(screen.getByTestId('approval-pill-time')).toHaveTextContent('5:54')
    expect(pill).toHaveAccessibleName('A trade is waiting for your approval, 5:54 left. Open it in the War Room.')
    expect(pill).not.toHaveClass('is-warn')
    // In the toolbar, before Stop (design: the pill, then Stop).
    const toolbar = screen.getByRole('banner')
    const stop = within(toolbar).getByTestId('kill-switch')
    expect(pill.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await sleep(1100)
    expect(screen.getByTestId('approval-pill-time')).toHaveTextContent('5:53')
  })

  it('is not rendered when nothing waits', async () => {
    fakeBackend([])
    mount('/portfolio')
    await screen.findByTestId('kill-switch')
    await sleep(50)
    expect(screen.queryByTestId('approval-pill')).not.toBeInTheDocument()
  })

  it('is not rendered when the only proposal is past its deadline', async () => {
    fakeBackend([waiting('p1', -5_000)])
    mount('/portfolio')
    await screen.findByTestId('kill-switch')
    await sleep(50)
    expect(screen.queryByTestId('approval-pill')).not.toBeInTheDocument()
  })

  it('counts every waiting proposal and times the nearest deadline', async () => {
    fakeBackend([waiting('p1', 600_000), waiting('p2', 120_000 - 300, { inst_id: 'XAU-USDT-SWAP' })])
    mount('/portfolio')
    const pill = await screen.findByTestId('approval-pill')
    expect(screen.getByTestId('approval-pill-label')).toHaveTextContent('2 to approve')
    expect(screen.getByTestId('approval-pill-time')).toHaveTextContent('2:00')
    expect(pill).toHaveAccessibleName('2 trades are waiting for your approval; the first closes in 2:00. Open it in the War Room.')
  })

  it('switches to the warning style under 60 s, with words as well as colour', async () => {
    fakeBackend([waiting('p1', 45_000)])
    mount('/portfolio')
    const pill = await screen.findByTestId('approval-pill')
    expect(pill).toHaveClass('is-warn')
    expect(pill).toHaveAttribute('data-warn', 'true')
    expect(screen.getByTestId('approval-pill-label')).toHaveTextContent('Last minute: 1 to approve')
    expect(pill.getAttribute('aria-label')).toMatch(/^Last minute: a trade is waiting for your approval, 0:4\d left/)
  })

  it('crosses into the warning style as the minute starts', async () => {
    fakeBackend([waiting('p1', 61_000)])
    mount('/portfolio')
    const pill = await screen.findByTestId('approval-pill')
    expect(pill).not.toHaveClass('is-warn')
    await sleep(1_600)
    await waitFor(() => expect(screen.getByTestId('approval-pill')).toHaveClass('is-warn'))
    expect(screen.getByTestId('approval-pill-label')).toHaveTextContent('Last minute')
  }, 8_000)

  it('opens the War Room with that proposal focused', async () => {
    fakeBackend([waiting('p-later', 600_000), waiting('p-first', 300_000)])
    const router = mount('/portfolio')
    const user = userEvent.setup()
    await user.click(await screen.findByTestId('approval-pill'))
    const page = await screen.findByTestId('warroom-page')
    expect(router.state.location.pathname).toBe('/warroom')
    expect(page).toHaveAttribute('data-proposal', 'p-first')
  })

  it('reads in Arabic', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend([waiting('p1', 354_000 - 300)])
    mount('/portfolio')
    const pill = await screen.findByTestId('approval-pill')
    expect(screen.getByTestId('approval-pill-label')).toHaveTextContent('صفقة تنتظر موافقتك')
    expect(screen.getByTestId('approval-pill-time')).toHaveTextContent('5:54')
    expect(screen.getByTestId('approval-pill-time')).toHaveAttribute('dir', 'ltr')
    expect(pill).toHaveAccessibleName('صفقة تنتظر موافقتك، والوقت المتبقي 5:54. افتحها في غرفة العمليات.')
  })
})

describe('the approval toast', () => {
  const expires = () => new Date(Date.now() + 360_000).toISOString()
  const event = (id: string, at: string) => ({
    id: 'evt-' + id,
    type: 'proposal.awaiting_owner',
    env: 'demo',
    source: 'system:orchestrator',
    payload: { proposal_id: id, version: 1, inst_id: 'XAU-USDT-SWAP', side: 'sell', decision: 'awaiting_owner', approval_expires_at: at },
  })

  it.each([
    ['a Portfolio page', '/portfolio'],
    ['the War Room', '/warroom'],
  ])('shows the instrument, side and approve-by time on %s', async (_, url) => {
    const state = fakeBackend([])
    mount(url)
    await screen.findByTestId('kill-switch')
    await sleep(50)
    const at = expires()
    state.list = [waiting('p9', 360_000, { inst_id: 'XAU-USDT-SWAP', side: 'sell', approval_expires_at: at })]
    emit('warroom', '.proposal.awaiting_owner', event('p9', at))
    const toast = await screen.findByText('Trade waiting for your approval')
    const card = toast.closest('li') ?? toast.parentElement!
    expect(card).toHaveTextContent(`XAU-USDT-SWAP short. Approve by ${formatTime(at, 'en')} in the War Room.`)
    // The event also refetches the list, so the pill appears without waiting for the poll.
    expect(await screen.findByTestId('approval-pill')).toHaveTextContent('1 to approve')
    // One proposal, one toast: the refetch that finds it does not toast again.
    await sleep(100)
    expect(useToasts.getState().toasts).toHaveLength(1)
  })

  it('toasts a proposal the poll finds, but not what was already waiting when the app opened', async () => {
    const state = fakeBackend([waiting('p-old', 300_000)])
    mount('/portfolio')
    await screen.findByTestId('approval-pill')
    expect(useToasts.getState().toasts).toHaveLength(0)
    state.list = [waiting('p-old', 300_000), waiting('p-new', 400_000, { inst_id: 'BTC-USDT-SWAP' })]
    // Any proposal event refetches the list (here one that is not itself a new proposal).
    emit('warroom', '.proposal.rejected', { type: 'proposal.rejected', env: 'demo', payload: { proposal_id: 'p-x', status: 'rejected' } })
    expect(await screen.findByText(/^BTC-USDT-SWAP long\. Approve by/)).toBeInTheDocument()
    expect(useToasts.getState().toasts).toHaveLength(1)
  })

  it("does not toast the owner's own edit", async () => {
    const state = fakeBackend([waiting('p1', 300_000)])
    mount('/portfolio')
    await screen.findByTestId('approval-pill')
    state.list = [waiting('p2', 300_000, { version: 2, parent_id: 'p1', author: 'owner' })]
    emit('warroom', '.proposal.revised', { type: 'proposal.revised', env: 'demo', payload: { proposal_id: 'p2', parent_id: 'p1', status: 'awaiting_owner' } })
    await sleep(150)
    expect(useToasts.getState().toasts).toHaveLength(0)
  })

  it('sends a browser notification only when permission is already granted, and never asks', async () => {
    const sent: { title: string; body?: string }[] = []
    const requestPermission = vi.fn()
    class FakeNotification {
      static permission = 'granted'
      static requestPermission = requestPermission
      constructor(title: string, options?: { body?: string }) {
        sent.push({ title, body: options?.body })
      }
    }
    vi.stubGlobal('Notification', FakeNotification)
    fakeBackend([])
    mount('/portfolio')
    await screen.findByTestId('kill-switch')
    await sleep(50)
    const at = expires()
    emit('warroom', '.proposal.awaiting_owner', event('p9', at))
    await screen.findByText('Trade waiting for your approval')
    expect(sent).toEqual([{ title: 'Trade waiting for your approval', body: `XAU-USDT-SWAP short. Approve by ${formatTime(at, 'en')} in the War Room.` }])

    FakeNotification.permission = 'default'
    emit('warroom', '.proposal.awaiting_owner', event('p10', at))
    await waitFor(() => expect(useToasts.getState().toasts).toHaveLength(2))
    expect(sent).toHaveLength(1)
    expect(requestPermission).not.toHaveBeenCalled()
  })
})
