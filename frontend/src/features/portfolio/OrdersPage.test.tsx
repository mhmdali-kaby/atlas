import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { resetCsrfForTests } from '../../lib/api'
import type { AlgoOrder, OpenOrder, OrdersPayload, PortfolioSummary } from './api'
import portfolio from './routes'

/* The ATL-148 handoff's GET /api/portfolio/orders shapes. */
const NOW = Date.parse('2026-10-28T11:40:00Z')
const SOL: OpenOrder = {
  id: '9d6c1f2e-0000-4000-8000-000000000001',
  env: 'demo',
  inst_id: 'SOL-USDT-SWAP',
  side: 'buy',
  pos_side: 'net',
  ord_type: 'post_only',
  td_mode: 'isolated',
  px: '249.4',
  sz: '12.02',
  filled_sz: '3',
  avg_px: null,
  lever: 3,
  state: 'partially_filled',
  purpose: 'entry',
  origin: 'atlas',
  reduce_only: false,
  cl_ord_id: 'e3f0',
  ord_id: '123456',
  proposal_id: '7a1b2c3d-1111-4000-8000-000000000412',
  last_error_code: null,
  last_error_msg: null,
  can_cancel: true,
  created_at: '2026-10-28T09:40:00Z',
  updated_at: '2026-10-28T09:41:00Z',
}
const ETH_EXT: OpenOrder = {
  ...SOL,
  id: '9d6c1f2e-0000-4000-8000-000000000002',
  inst_id: 'ETH-USDT-SWAP',
  side: 'sell',
  ord_type: 'limit',
  px: '3980',
  sz: '0.5',
  filled_sz: '0',
  state: 'live',
  origin: 'external',
  proposal_id: null,
  cl_ord_id: null,
  created_at: '2026-10-28T11:22:00Z',
}
const BTC_OCO: AlgoOrder = {
  id: 'a1000000-0000-4000-8000-000000000001',
  env: 'demo',
  order_id: null,
  inst_id: 'BTC-USDT-SWAP',
  kind: 'oco',
  protects: true,
  side: 'sell',
  pos_side: 'net',
  trigger_px: null,
  sl_trigger_px: '113200',
  tp_trigger_px: '121300',
  ord_px: null,
  sz: '250',
  state: 'live',
  origin: 'atlas',
  reduce_only: true,
  algo_id: '7001',
  algo_cl_ord_id: 'o3f',
  last_error_code: null,
  last_error_msg: null,
  created_at: '2026-10-28T08:00:00Z',
  updated_at: '2026-10-28T08:00:00Z',
}
const XAU_TP: AlgoOrder = {
  ...BTC_OCO,
  id: 'a1000000-0000-4000-8000-000000000002',
  inst_id: 'XAU-USDT-SWAP',
  kind: 'tp',
  protects: false,
  trigger_px: '4075.8',
  sl_trigger_px: null,
  tp_trigger_px: null,
  sz: null,
  origin: 'external',
}
const SUMMARY = {
  env: 'demo',
  reconciled_at: '2026-10-28T11:38:00Z',
  stale: false,
} as PortfolioSummary

type Reply = { status: number; body?: unknown }
const json = (r: Reply) =>
  new Response(r.body === undefined ? null : JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(orders: OrdersPayload | 'fail' | 'hang', cancel: Reply = { status: 200, body: { ok: true, status: 'cancelled' } }) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    if (url.pathname === '/sanctum/csrf-cookie') return json({ status: 204 })
    if (url.pathname === '/api/portfolio/summary') return json({ status: 200, body: { data: SUMMARY } })
    if (url.pathname === '/api/portfolio/orders') {
      if (orders === 'hang') return new Promise<Response>(() => {})
      return orders === 'fail' ? json({ status: 500, body: { message: 'Server Error' } }) : json({ status: 200, body: { data: orders } })
    }
    if (/^\/api\/orders\/[^/]+\/cancel$/.test(url.pathname) && init?.method === 'POST') return json(cancel)
    return json({ status: 404, body: { message: 'Not found' } })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const callsTo = (fetch: ReturnType<typeof fakeBackend>, path: string | RegExp, method = 'GET') =>
  fetch.mock.calls.filter(([input, init]) => {
    const p = new URL(String(input)).pathname
    return (typeof path === 'string' ? p === path : path.test(p)) && (init?.method ?? 'GET') === method
  })

function renderOrders() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: ['/portfolio/orders'] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const cell = (row: HTMLElement, col: string) => row.querySelector<HTMLElement>(`[data-col="${col}"]`)!
const FULL: OrdersPayload = { env: 'demo', orders: [SOL, ETH_EXT], algo_orders: [BTC_OCO, XAU_TP] }

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Portfolio Orders', () => {
  it('registers /portfolio/orders with an Orders nav entry between Positions and Holdings', () => {
    const nav = (Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]).filter(Boolean)
    const sorted = [...nav].sort((a, b) => (a!.order ?? 0) - (b!.order ?? 0)).map((n) => n!.path)
    expect(sorted).toEqual(['/portfolio', '/portfolio/positions', '/portfolio/orders', '/portfolio/holdings', '/portfolio/cashflows', '/portfolio/history', '/portfolio/performance'])
    expect(portfolio.routes.map((r) => r.path)).toContain('portfolio/orders')
  })

  it('renders every column of both sections, and external orders carry an External badge', async () => {
    fakeBackend(FULL)
    renderOrders()
    const openTable = await screen.findByTestId('open-orders-table')
    expect(screen.getByTestId('orders-summary')).toHaveTextContent('2 open orders and 2 algo orders')

    expect(within(openTable).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Instrument',
      'Side',
      'Type',
      'Price',
      'Size',
      'Filled',
      'State',
      'Origin',
      'Age',
      'Actions',
    ])
    const [sol, eth] = screen.getAllByTestId('open-order-row')
    expect(cell(sol!, 'instrument')).toHaveTextContent('SOL-USDT-SWAP')
    expect(cell(sol!, 'side')).toHaveTextContent('Buy')
    expect(cell(sol!, 'type')).toHaveTextContent('Post only')
    expect(cell(sol!, 'price')).toHaveTextContent('249.40')
    expect(cell(sol!, 'price')).toHaveClass('is-num')
    expect(cell(sol!, 'size')).toHaveTextContent('12.02contracts')
    expect(cell(sol!, 'filled')).toHaveTextContent('325%')
    expect(cell(sol!, 'state')).toHaveTextContent('Partly filled')
    expect(cell(sol!, 'origin')).toHaveTextContent('Atlas')
    expect(cell(sol!, 'origin')).toHaveTextContent('Proposal 7a1b2c3d')
    expect(within(sol!).queryByTestId('external-badge')).toBeNull()
    // 09:40 UTC → 11:40 UTC: 2 h; placed 12:40 Baghdad.
    expect(cell(sol!, 'age')).toHaveTextContent(/2h/)
    expect(cell(sol!, 'age')).toHaveTextContent('12:40')

    expect(cell(eth!, 'side')).toHaveTextContent('Sell')
    expect(cell(eth!, 'type')).toHaveTextContent('Limit')
    expect(cell(eth!, 'price')).toHaveTextContent('3,980.00')
    expect(cell(eth!, 'state')).toHaveTextContent('On the book')
    expect(within(cell(eth!, 'origin')).getByTestId('external-badge')).toHaveTextContent('External')

    const algoTable = screen.getByTestId('algo-orders-table')
    expect(within(algoTable).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Instrument',
      'Kind',
      'Trigger price',
      'Size',
      'State',
    ])
    const [oco, tp] = screen.getAllByTestId('algo-order-row')
    expect(cell(oco!, 'instrument')).toHaveTextContent('BTC-USDT-SWAP')
    expect(cell(oco!, 'kind')).toHaveTextContent('Stop and take profit')
    expect(cell(oco!, 'kind')).toHaveTextContent('Protects the position')
    expect(cell(oco!, 'trigger')).toHaveTextContent('Stop113,200.0')
    expect(cell(oco!, 'trigger')).toHaveTextContent('Target121,300.0')
    expect(cell(oco!, 'size')).toHaveTextContent('250contracts')
    expect(cell(oco!, 'state')).toHaveTextContent('Waiting for its price')
    expect(within(oco!).queryByTestId('external-badge')).toBeNull()
    expect(cell(tp!, 'kind')).toHaveTextContent('Take profit')
    expect(cell(tp!, 'kind')).not.toHaveTextContent('Protects')
    expect(cell(tp!, 'trigger')).toHaveTextContent('4,075.80')
    expect(cell(tp!, 'size')).toHaveTextContent('Whole position')
    expect(within(tp!).getByTestId('external-badge')).toHaveTextContent('External')
    // Algo orders are read-only: no cancel route exists for them.
    expect(within(algoTable).queryByRole('button')).toBeNull()
  })

  it('hides Cancel on an order the API says cannot be cancelled', async () => {
    fakeBackend({ ...FULL, orders: [{ ...SOL, can_cancel: false }] })
    renderOrders()
    await screen.findByTestId('open-orders-table')
    expect(screen.queryByTestId('cancel-order')).toBeNull()
  })

  it('Cancel → confirm posts the cancel once and refetches the list', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const fetch = fakeBackend(FULL)
    renderOrders()
    await screen.findByTestId('open-orders-table')
    const before = callsTo(fetch, '/api/portfolio/orders').length

    await user.click(within(screen.getAllByTestId('open-order-row')[0]!).getByTestId('cancel-order'))
    const sheet = await screen.findByRole('dialog')
    expect(within(sheet).getByTestId('cancel-sheet-title')).toHaveTextContent('Cancel the \u2066SOL-USDT-SWAP\u2069 buy order?')
    expect(sheet).toHaveTextContent('Proposal 7a1b2c3d')

    await user.click(within(sheet).getByTestId('cancel-confirm'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    const posts = callsTo(fetch, /^\/api\/orders\/.+\/cancel$/, 'POST')
    expect(posts).toHaveLength(1)
    expect(new URL(String(posts[0]![0])).pathname).toBe(`/api/orders/${SOL.id}/cancel`)
    await waitFor(() => expect(callsTo(fetch, '/api/portfolio/orders').length).toBe(before + 1))
  })

  it('dismissing the sheet sends nothing', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const fetch = fakeBackend(FULL)
    renderOrders()
    await screen.findByTestId('open-orders-table')

    await user.click(within(screen.getAllByTestId('open-order-row')[1]!).getByTestId('cancel-order'))
    let sheet = await screen.findByRole('dialog')
    expect(sheet).toHaveTextContent('This order was placed outside Atlas.')
    await user.click(within(sheet).getByTestId('cancel-keep'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    await user.click(within(screen.getAllByTestId('open-order-row')[1]!).getByTestId('cancel-order'))
    sheet = await screen.findByRole('dialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    expect(callsTo(fetch, /\/cancel$/, 'POST')).toHaveLength(0)
  })

  it.each([
    ['409 detail', { status: 409, body: { ok: false, error: 'order_not_open', detail: 'The order is already filled.' } }, 'The order is already filled.'],
    [
      '502 failed item',
      { status: 502, body: { ok: false, status: 'failed', failed: [{ code: '51400', message: 'Order cancellation failed as the order has been filled, canceled or does not exist.' }] } },
      'Order cancellation failed as the order has been filled, canceled or does not exist.',
    ],
    ['503 message', { status: 503, body: { ok: false, error: 'trading_core_unavailable', message: 'Atlas couldn’t reach trading_core. Nothing was sent to OKX.' } }, 'Atlas couldn’t reach trading_core. Nothing was sent to OKX.'],
  ])('an API error on cancel (%s) shows its message in the sheet', async (_name, reply, message) => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    fakeBackend(FULL, reply)
    renderOrders()
    await screen.findByTestId('open-orders-table')
    await user.click(within(screen.getAllByTestId('open-order-row')[0]!).getByTestId('cancel-order'))
    const sheet = await screen.findByRole('dialog')
    await user.click(within(sheet).getByTestId('cancel-confirm'))

    const err = await within(sheet).findByTestId('cancel-error')
    expect(err).toHaveTextContent('OKX didn’t cancel it')
    expect(err).toHaveTextContent(message)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('empty lists show their own text in each section', async () => {
    fakeBackend({ env: 'demo', orders: [], algo_orders: [] })
    renderOrders()
    expect(await screen.findByText('Atlas places orders only after you approve a trade.')).toBeInTheDocument()
    expect(screen.getByText('No open orders')).toBeInTheDocument()
    expect(screen.getByText('No algo orders')).toBeInTheDocument()
    expect(screen.getByText('Stops and take-profits appear here once a position is open.')).toBeInTheDocument()
    expect(screen.queryByTestId('orders-summary')).toBeNull()
  })

  it('loading shows skeletons and its own text', async () => {
    fakeBackend('hang')
    renderOrders()
    expect(await screen.findByTestId('orders-loading')).toHaveTextContent('Loading your orders')
    expect(screen.getByTestId('open-orders-loading')).toBeInTheDocument()
    expect(screen.getByTestId('algo-orders-loading')).toBeInTheDocument()
  })

  it('an error loading the list shows its own text and retries', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const fetch = fakeBackend('fail')
    renderOrders()
    const block = await screen.findByTestId('state-error')
    expect(block).toHaveTextContent('Couldn’t load your orders')
    const before = callsTo(fetch, '/api/portfolio/orders').length
    await user.click(within(block).getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(callsTo(fetch, '/api/portfolio/orders').length).toBe(before + 1))
  })

  it('renders in Arabic with Western digits', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend(FULL)
    renderOrders()
    await screen.findByTestId('open-orders-table')
    expect(screen.getByTestId('orders-summary')).toHaveTextContent('أمران مفتوحان وأمران مشروطان')
    expect(screen.getAllByTestId('external-badge')[0]).toHaveTextContent('خارجي')
    expect(document.querySelector('main, body')!.textContent).not.toMatch(/[٠-٩]/)

    // The age is words and digits: in Arabic it keeps the RTL order, so it must not be
    // the LTR-isolated .fig used for figures, or "1 س و9 د" renders as "1 د 9و س".
    const [first] = screen.getAllByTestId('open-order-row')
    const age = within(first!).getByTestId('duration')
    expect(age).not.toHaveClass('fig')
    expect(age).toHaveAttribute('dir', 'rtl')
  })
})
