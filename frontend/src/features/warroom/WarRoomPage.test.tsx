import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildShellRegistry } from '../../app/feature'
import { HomeRedirect } from '../../app/AppShell'
import i18n from '../../i18n'
import type { Position, PositionsPayload } from '../portfolio/api'
import portfolio from '../portfolio/routes'
import type { WarroomMessage, WarroomThread } from './api'
import warroom from './routes'
import { AgentList, ThreadList } from './SidebarSections'

// No socket in these tests: the live side is covered by Composer.test.tsx and the e2e flow.
vi.mock('../../lib/echo', () => ({
  getEcho: () => ({ private: () => ({ listen: () => {}, stopListening: () => {} }) }),
  hasConnected: () => true,
  reconnectEcho: () => {},
  useLiveStatus: () => 'connected',
}))

/* The ATL-38 handoff's shapes: Main, one instrument thread, one private agent thread. */
const NOW = Date.parse('2026-10-29T08:00:00Z')
const MAIN: WarroomThread = {
  id: 'main-1',
  kind: 'main',
  key: 'main',
  title: 'Main',
  last_message_at: '2026-10-29T05:00:00.123456Z',
  created_at: '2026-07-14T15:02:00.000000Z',
}
const BTC: WarroomThread = { ...MAIN, id: 'btc-1', kind: 'instrument', key: 'BTC-USDT-SWAP', title: 'BTC-USDT-SWAP', last_message_at: null }
const HIST: WarroomThread = { ...MAIN, id: 'hist-1', kind: 'agent', key: 'historian', title: 'historian', last_message_at: null }

const OWNER_MSG: WarroomMessage = {
  id: 'm-1',
  thread_id: 'main-1',
  author_type: 'human',
  author: 'Mhmd',
  kind: 'text',
  content_md: '@Historian what usually happens after CPI?',
  card: null,
  reply_to: null,
  mentions: ['historian'],
  reasoning_md: null,
  tool_calls: null,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  created_at: '2026-10-29T05:00:00.123456Z',
}

type Messages = WarroomMessage[] | 'fail' | 'hang'

/* The ATL-12 handoff's /api/portfolio/positions example: PortfolioDemoSeeder's two positions. */
const BTC_POSITION: Position = {
  id: '809d7f30',
  inst_id: 'BTC-USDT-SWAP',
  side: 'long',
  pos_side: 'net',
  size: '25',
  leverage: '5',
  entry: '67250.5',
  mark: '68120.3',
  liq_px: '54112.8',
  margin: '3362.525',
  settle_ccy: 'USDT',
  upl_usd: '217.45',
  upl_pct: '6.466866',
  margin_mode: 'isolated',
  opened_at: '2026-10-28T08:36:00.000000Z',
  stop: null,
  take_profits: null,
  agent: null,
  proposal_id: null,
}
const XAU_POSITION: Position = {
  ...BTC_POSITION,
  id: '85242c4a',
  inst_id: 'XAU-USDT-SWAP',
  side: 'short',
  size: '400',
  leverage: '3',
  entry: '2741.8',
  mark: '2728.4',
  liq_px: '3618.9',
  margin: '3655.733333',
  upl_usd: '-53.6',
  upl_pct: '-1.46619',
  margin_mode: 'cross',
  opened_at: '2026-10-27T14:36:00.000000Z',
}

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** `positions` defaults to the two seeded positions; pass [] / 'fail' to test the other states. */
function fakeBackend(messages: Messages[], positions: PositionsPayload | 'fail' = { taken_at: '2026-10-28T17:36:00.000000Z', items: [BTC_POSITION, XAU_POSITION] }) {
  const queue = [...messages]
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/warroom/threads') return reply(200, { data: [MAIN, BTC, HIST] })
    if (url.pathname === '/api/warroom/messages') {
      const next = queue.length > 1 ? queue.shift()! : queue[0]!
      if (next === 'hang') return new Promise<Response>(() => {})
      if (next === 'fail') return reply(500, { message: 'Server Error' })
      return reply(200, { data: [...next].reverse(), meta: { next_cursor: null } })
    }
    if (url.pathname === '/api/portfolio/positions') {
      return positions === 'fail' ? reply(500, { message: 'Server Error' }) : reply(200, { data: positions })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const messageCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.filter(([input]) => new URL(String(input)).pathname === '/api/warroom/messages')
const positionCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.filter(([input]) => new URL(String(input)).pathname === '/api/portfolio/positions')

function renderWarRoom(path = '/warroom') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Sidebar = () => (
    <nav aria-label="War Room">
      <AgentList onNavigate={() => {}} />
      <ThreadList onNavigate={() => {}} />
    </nav>
  )
  const router = createMemoryRouter(
    warroom.routes.map((r) => ({
      ...r,
      path: `/${r.path}`,
      element: (
        <>
          <Sidebar />
          <div data-slot="toolbar-actions" />
          {r.element}
        </>
      ),
    })),
    { initialEntries: [path] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('War Room', () => {
  it('registers the War Room area with its agents and threads sections', () => {
    expect(warroom.area).toBe('warroom')
    const sections = Array.isArray(warroom.sidebar) ? warroom.sidebar : [warroom.sidebar]
    expect(sections.map((s) => s?.id)).toEqual(['agents', 'threads'])
    expect(warroom.routes.map((r) => r.path)).toEqual(['warroom', 'warroom/threads/:threadId'])
  })

  it('makes "/" open the War Room, the first area, when it is registered', async () => {
    const shell = buildShellRegistry([portfolio, warroom])
    expect(shell.areas.map((a) => a.id)).toEqual(['warroom', 'portfolio'])
    const router = createMemoryRouter(
      [
        { path: '/', element: <HomeRedirect shell={shell} /> },
        { path: '/warroom', element: <p>war room</p> },
      ],
      { initialEntries: ['/'] },
    )
    render(<RouterProvider router={router} />)
    await waitFor(() => expect(router.state.location.pathname).toBe('/warroom'))
  })

  it('lists the nine agents offline and the threads with Main selected', async () => {
    fakeBackend([[OWNER_MSG]])
    renderWarRoom()
    const agents = screen.getByTestId('wr-agents')
    expect(within(agents).getByText('All offline')).toBeInTheDocument()
    const rows = within(agents).getAllByRole('button')
    expect(rows).toHaveLength(9)
    expect(rows[0]).toHaveAccessibleName('Orchestrator, Offline')
    expect(rows[3]).toHaveAccessibleName('Historian, Offline')
    expect(within(rows[3]!).getByText('Answers with statistics from history')).toBeInTheDocument()

    const threads = await screen.findByTestId('wr-threads')
    const links = within(threads).getAllByRole('link')
    expect(links).toHaveLength(3)
    expect(links[0]).toHaveAccessibleName(/^Main/)
    expect(links[1]).toHaveAccessibleName('BTC-USDT-SWAP')
    expect(links[2]).toHaveAccessibleName('Historian')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
    expect(links[0]).toHaveAttribute('href', '/warroom')
    expect(links[1]).toHaveAttribute('href', '/warroom/threads/btc-1')
    // Main's last message, in Baghdad time: 05:00Z is 08:00 in Baghdad.
    expect(within(links[0]!).getByText('08:00')).toBeInTheDocument()
  })

  it('shows the loading skeleton while the thread loads', async () => {
    fakeBackend(['hang'])
    renderWarRoom()
    const skeleton = await screen.findByTestId('wr-feed-loading')
    expect(skeleton).toHaveAttribute('aria-busy', 'true')
    expect(skeleton).toHaveAccessibleName('Loading messages')
  })

  it('shows the empty state for a thread with no messages', async () => {
    fakeBackend([[]])
    renderWarRoom()
    const empty = await screen.findByTestId('wr-feed-empty')
    expect(within(empty).getByRole('heading', { name: 'No messages yet' })).toBeInTheDocument()
    expect(empty).toHaveTextContent('newest at the bottom')
    // The inspector's thread facts agree: no last message.
    expect(within(screen.getByTestId('wr-inspector')).getByText('None')).toBeInTheDocument()
  })

  it('names the instrument in an empty instrument thread, and shows the chart empty state', async () => {
    fakeBackend([[]])
    renderWarRoom('/warroom/threads/btc-1')
    const empty = await screen.findByTestId('wr-feed-empty')
    expect(within(empty).getByRole('heading')).toHaveTextContent('Nothing about BTC-USDT-SWAP yet')
    expect(screen.getByTestId('wr-chart-empty')).toHaveTextContent('The candle chart for BTC-USDT-SWAP appears here once Atlas records its market data.')
    expect(within(screen.getByTestId('wr-threads')).getByRole('link', { current: 'page' })).toHaveTextContent('BTC-USDT-SWAP')
  })

  it('shows a not-found state with a way back to Main for a thread id that is not in the list', async () => {
    const fetch = fakeBackend([[]])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const router = renderWarRoom('/warroom/threads/00000000-0000-0000-0000-000000000000')
    const notFound = await screen.findByTestId('wr-feed-not-found')
    expect(screen.queryByTestId('wr-feed-loading')).not.toBeInTheDocument()
    expect(within(notFound).getByRole('heading', { name: 'This thread isn’t here' })).toBeInTheDocument()
    expect(messageCalls(fetch)).toHaveLength(0)
    // The inspector agrees instead of showing loading placeholders.
    const inspector = screen.getByTestId('wr-inspector')
    expect(within(inspector).getByRole('heading', { name: 'This thread isn’t here' })).toBeInTheDocument()
    expect(inspector.querySelector('[aria-busy]')).toBeNull()

    await user.click(within(notFound).getByRole('link', { name: 'Go to Main' }))
    await screen.findByTestId('wr-feed-empty')
    expect(router.state.location.pathname).toBe('/warroom')
  })

  it('shows the error state with a retry that reloads the thread', async () => {
    const fetch = fakeBackend(['fail', []])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWarRoom()
    const error = await screen.findByTestId('wr-feed-error')
    expect(error).toHaveAttribute('role', 'alert')
    expect(within(error).getByRole('heading', { name: 'Couldn’t load messages' })).toBeInTheDocument()
    expect(error).toHaveTextContent('./atlas up')
    const before = messageCalls(fetch).length

    await user.click(within(error).getByRole('button', { name: 'Try again' }))
    await screen.findByTestId('wr-feed-empty')
    expect(messageCalls(fetch).length).toBe(before + 1)
  })

  it('collapses and reopens the inspector from the toolbar toggle', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width: 1280px'), addEventListener() {}, removeEventListener() {} }))
    fakeBackend([[]])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWarRoom()
    await screen.findByTestId('wr-inspector')
    const toggle = screen.getByRole('button', { name: 'Show or hide the inspector' })
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await user.click(toggle)
    expect(screen.queryByTestId('wr-inspector')).not.toBeInTheDocument()
    expect(localStorage.getItem('atlas.wr.inspector')).toBe('closed')
    await user.click(toggle)
    expect(screen.getByTestId('wr-inspector')).toBeInTheDocument()
  })
})

describe('Inspector positions (ATL-43)', () => {
  it('lists the seeded positions with the same side, size, entry, mark and unrealised PnL as Portfolio → Positions', async () => {
    fakeBackend([[]])
    renderWarRoom()
    const rows = await screen.findAllByTestId('wr-position-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
    expect(rows[0]).toHaveTextContent('BTC-USDT-SWAP')
    expect(rows[0]).toHaveTextContent('+$217.45')
    expect(rows[0]).toHaveTextContent('Long')
    expect(rows[0]).toHaveTextContent('5×')
    expect(rows[0]).toHaveTextContent('25')
    expect(rows[0]).toHaveTextContent('Entry')
    expect(rows[0]).toHaveTextContent('67,250.5')
    expect(rows[0]).toHaveTextContent('68,120.3')

    expect(rows[1]).toHaveAttribute('data-inst', 'XAU-USDT-SWAP')
    expect(rows[1]).toHaveTextContent('Short')
    expect(rows[1]).toHaveTextContent('−$53.60')
    expect(within(screen.getByTestId('wr-positions')).getByRole('link', { name: 'Positions' })).toHaveAttribute('href', '/portfolio/positions')
  })

  it('puts the selected instrument thread’s own position first, highlighted', async () => {
    fakeBackend([[]])
    renderWarRoom('/warroom/threads/btc-1')
    const rows = await screen.findAllByTestId('wr-position-row')
    expect(rows[0]).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
    expect(rows[0]).toHaveClass('is-here')
    expect(rows[1]).not.toHaveClass('is-here')
  })

  it('shows "No open positions" when the account has none', async () => {
    fakeBackend([[]], { taken_at: '2026-10-28T17:36:00.000000Z', items: [] })
    renderWarRoom()
    expect(await screen.findByTestId('wr-positions-empty')).toHaveTextContent('No open positions')
    expect(screen.queryByTestId('wr-position-row')).not.toBeInTheDocument()
  })

  it('shows an error with Retry that refetches on failure', async () => {
    const fetch = fakeBackend([[]], 'fail')
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWarRoom()
    const error = await screen.findByTestId('wr-positions-error')
    expect(error).toHaveAttribute('role', 'alert')
    expect(error).toHaveTextContent('Couldn’t load positions')
    const before = positionCalls(fetch).length

    await user.click(within(error).getByRole('button', { name: 'Try again' }))
    expect(positionCalls(fetch).length).toBeGreaterThan(before)
  })
})
