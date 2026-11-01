import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import type { PortfolioSummary } from './api'
import { BLANK_FILTERS, csvRowCount, exportUrl, filterParams, rangeDays, type ClosedRow, type FillRow } from './historyApi'
import portfolio from './routes'

/* ATL-172's GET /api/portfolio/history and /api/exports/{type}.csv shapes (docs/handoff/ATL-172-…). */
const NOW = Date.parse('2026-10-28T11:40:00Z') // 14:40 Baghdad

const LOSS: ClosedRow = {
  id: '9f3c0000-0000-4000-8000-000000000001',
  pos_id: '1777777777777777777',
  inst_id: 'ETH-USDT-SWAP',
  asset_class: 'crypto',
  side: 'short',
  pos_side: 'net',
  margin_mode: 'isolated',
  close_type: '2',
  leverage: '5',
  ccy: 'USDT',
  size: '2',
  entry: '3301.5',
  exit: '3350.12',
  realised_pnl: '-4.5',
  pnl: '-4.5',
  fees: '-0.67',
  funding: '-0.012',
  liq_penalty: '0',
  r: '-1.50',
  outcome: 'loss',
  opened_at: '2026-10-21T18:30:00.000000Z',
  closed_at: '2026-10-21T20:30:00.000000Z',
  proposal_id: '0b1e0000-0000-4000-8000-000000000001',
  agent: 'strategist',
  setup_tag: null,
  origin: 'atlas',
}
const EXT_WIN: ClosedRow = {
  ...LOSS,
  id: '9f3c0000-0000-4000-8000-000000000002',
  inst_id: 'BTC-USDT-SWAP',
  side: 'long',
  realised_pnl: '120.25',
  pnl: '120.25',
  r: null,
  outcome: 'win',
  proposal_id: null,
  agent: null,
  origin: 'external',
}
const FILL: FillRow = {
  id: 'f1000000-0000-4000-8000-000000000001',
  ts: '2026-10-20T08:00:00.000000Z',
  inst_id: 'ETH-USDT-SWAP',
  asset_class: 'crypto',
  side: 'sell',
  pos_side: 'net',
  price: '3350.12',
  size: '2',
  fee: '-0.67',
  fee_ccy: 'USDT',
  fill_pnl: '-3.2',
  exec_type: 'T',
  trade_id: '1900000002',
  ord_id: '2900000000000000002',
  order_id: null,
  proposal_id: null,
  agent: null,
  origin: 'external',
}
const SUMMARY = { env: 'demo', reconciled_at: '2026-10-28T11:38:00Z', stale: false } as PortfolioSummary

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(opts: { fills?: FillRow[]; closed?: ClosedRow[]; fail?: boolean } = {}) {
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/portfolio/summary') return json(200, { data: SUMMARY })
    if (url.pathname === '/api/portfolio/history') {
      if (opts.fail) return json(500, { message: 'Server Error' })
      const type = url.searchParams.get('type')
      const data = type === 'closed' ? (opts.closed ?? [LOSS, EXT_WIN]) : (opts.fills ?? [FILL])
      return json(200, { data, meta: { type, per_page: 200, next_cursor: null, prev_cursor: null } })
    }
    if (url.pathname.startsWith('/api/exports/')) {
      return new Response('id,inst_id\n1,ETH-USDT-SWAP\n2,"BTC\nUSDT"\n', {
        status: 200,
        headers: { 'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="atlas-demo-closed-20261028.csv"' },
      })
    }
    return json(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const historyCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.map(([input]) => new URL(String(input))).filter((u) => u.pathname === '/api/portfolio/history')

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: ['/portfolio/history'] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const cell = (row: HTMLElement, col: string) => row.querySelector<HTMLElement>(`[data-col="${col}"]`)!

beforeEach(async () => {
  // jsdom lays nothing out: give the table's scroll box a viewport so the virtualiser renders a window.
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.dataset.testid === 'history-scroll' ? 600 : 0
  })
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.dataset.testid === 'history-scroll' ? 1200 : 0
  })
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Portfolio History', () => {
  it('registers /portfolio/history with a History nav entry after Cash flows', () => {
    const nav = (Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]).filter(Boolean)
    const sorted = [...nav].sort((a, b) => (a!.order ?? 0) - (b!.order ?? 0)).map((n) => n!.path)
    expect(sorted.at(-1)).toBe('/portfolio/history')
    expect(portfolio.routes.map((r) => r.path)).toContain('portfolio/history')
  })

  it('opens on fills, switching to Closed positions requests type=closed, and side Sell adds side=sell', async () => {
    const fetch = fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderHistory()
    await screen.findByTestId('history-table')
    expect(historyCalls(fetch)[0]!.searchParams.get('type')).toBe('fills')

    await user.click(screen.getByRole('radio', { name: 'Closed positions' }))
    await waitFor(() => expect(historyCalls(fetch).at(-1)!.searchParams.get('type')).toBe('closed'))
    await waitFor(() => expect(screen.getAllByTestId('history-row')).toHaveLength(2))

    await user.click(within(screen.getByRole('radiogroup', { name: 'Side' })).getByRole('radio', { name: 'Sell' }))
    await waitFor(() => {
      const last = historyCalls(fetch).at(-1)!
      expect(last.searchParams.get('type')).toBe('closed')
      expect(last.searchParams.get('side')).toBe('sell')
    })
    expect(screen.getByTestId('history-chips')).toHaveTextContent('Sell')
  })

  it('a losing row shows "−", the loss icon and red; R has 2 decimals or "—" when null', async () => {
    fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderHistory()
    await screen.findByTestId('history-table')
    await user.click(screen.getByRole('radio', { name: 'Closed positions' }))
    await waitFor(() => expect(screen.getAllByTestId('history-row')).toHaveLength(2))
    const [loss, win] = screen.getAllByTestId('history-row')

    const pnl = cell(loss!, 'pnl').querySelector<HTMLElement>('[data-sign]')!
    expect(pnl).toHaveAttribute('data-sign', 'loss')
    expect(pnl).toHaveClass('text-loss-ink')
    expect(pnl.textContent).toContain('−$4.50')
    expect(pnl.querySelector('[data-sign-icon]')).not.toBeNull()
    expect(pnl).toHaveTextContent(/loss/)
    expect(cell(loss!, 'pnl')).toHaveClass('is-num')
    expect(cell(loss!, 'r')).toHaveTextContent('−1.50R')

    expect(cell(win!, 'pnl').querySelector('[data-sign]')).toHaveAttribute('data-sign', 'profit')
    expect(cell(win!, 'pnl')).toHaveTextContent('+$120.25')
    expect(cell(win!, 'r')).toHaveTextContent('—')
    expect(within(cell(win!, 'r')).getByRole('note')).toHaveAccessibleName('No R: Atlas set no stop on this position.')
    expect(cell(win!, 'agent')).toHaveTextContent('—')
    expect(cell(loss!, 'agent')).toHaveTextContent('Strategist')
    expect(cell(loss!, 'close')).toHaveTextContent('Closed')
    // 21:30 → 23:30 Baghdad: held 2 h.
    expect(cell(loss!, 'held')).toHaveTextContent(/2h/)
  })

  it('Export CSV builds the URL with the active filters', async () => {
    const fetch = fakeBackend()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderHistory()
    await screen.findByTestId('history-table')
    await user.click(screen.getByRole('radio', { name: 'Closed positions' }))
    await user.click(within(screen.getByRole('radiogroup', { name: 'Result' })).getByRole('radio', { name: 'Losses' }))
    await user.click(within(screen.getByRole('radiogroup', { name: 'Side' })).getByRole('radio', { name: 'Buy' }))
    await waitFor(() => expect(historyCalls(fetch).at(-1)!.searchParams.get('side')).toBe('buy'))

    await user.click(screen.getByTestId('export-csv'))
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => new URL(String(u)).pathname === '/api/exports/closed.csv')).toBe(true))
    const [input, init] = fetch.mock.calls.find(([u]) => new URL(String(u)).pathname === '/api/exports/closed.csv')!
    const url = new URL(String(input))
    expect(url.origin).toBe(new URL(API_URL).origin)
    expect(Object.fromEntries(url.searchParams)).toEqual({ env: 'demo', side: 'buy', win_loss: 'loss' })
    expect(init?.credentials).toBe('include')
  })

  it('exportUrl carries every filter; strategy_tag only for closed positions; ranges are Baghdad days', () => {
    const f = { ...BLANK_FILTERS, inst: 'xau-usdt-swap', assetClass: 'commodity', agent: 'strategist', tag: 'breakout', range: '7d' as const, side: 'sell' as const, result: 'win' as const }
    const closed = new URL(exportUrl('closed', f, 'demo', NOW))
    expect(closed.pathname).toBe('/api/exports/closed.csv')
    expect(Object.fromEntries(closed.searchParams)).toEqual({
      env: 'demo',
      inst_id: 'XAU-USDT-SWAP',
      asset_class: 'commodity',
      agent: 'strategist',
      strategy_tag: 'breakout',
      from: '2026-10-22',
      to: '2026-10-28',
      side: 'sell',
      win_loss: 'win',
    })
    expect(new URL(exportUrl('fills', f, 'live', NOW)).searchParams.has('strategy_tag')).toBe(false)
    expect(filterParams('fills', BLANK_FILTERS, 'demo', NOW).toString()).toBe('env=demo')
    // 22:30 UTC is already the next day in Baghdad.
    expect(rangeDays({ range: 'today', from: null, to: null }, Date.parse('2026-10-28T22:30:00Z'))).toEqual({ from: '2026-10-29', to: '2026-10-29' })
    expect(csvRowCount('a,b\n1,"x\ny"\n2,z\n')).toBe(2)
  })

  it('shows the no-matches state with Clear filters, and the error state with Try again', async () => {
    const fetch = fakeBackend({ fills: [] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderHistory()
    expect(await screen.findByText('No trades yet')).toBeInTheDocument()
    await user.click(within(screen.getByRole('radiogroup', { name: 'Side' })).getByRole('radio', { name: 'Sell' }))
    expect(await screen.findByText('No trades match these filters')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Clear filters' }).at(-1)!)
    await waitFor(() => expect(historyCalls(fetch).at(-1)!.searchParams.has('side')).toBe(false))
    expect(await screen.findByText('No trades yet')).toBeInTheDocument()
  })

  it('shows the error state when the API fails', async () => {
    fakeBackend({ fail: true })
    renderHistory()
    expect(await screen.findByText('History didn’t load')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Try again')
  })
})
