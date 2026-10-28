import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { PortfolioSummary, Position, PositionsPayload } from './api'
import portfolio from './routes'

/* The ATL-12 handoff's /api/portfolio/positions example: PortfolioDemoSeeder's two positions. */
const NOW = Date.parse('2026-10-28T17:40:00Z')
const BTC: Position = {
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
const XAU: Position = {
  ...BTC,
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
const SUMMARY = {
  env: 'demo',
  total_equity_usd: '5136121.59',
  unrealised_pnl_usd: '163.85',
  reconciled_at: '2026-10-28T17:36:00.000000Z',
  stale: false,
} as PortfolioSummary

function fakeBackend(positions: PositionsPayload | 'fail') {
  const reply = (status: number, body?: unknown) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/portfolio/summary') return reply(200, { data: SUMMARY })
    if (url.pathname === '/api/portfolio/positions') {
      return positions === 'fail' ? reply(500, { message: 'Server Error' }) : reply(200, { data: positions })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderPositions() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: ['/portfolio/positions'] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const cell = (row: HTMLElement, col: string) => row.querySelector<HTMLElement>(`[data-col="${col}"]`)!

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Portfolio Positions', () => {
  it('registers /portfolio/positions and a Positions nav entry after Overview', () => {
    const nav = Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]
    expect(nav.map((n) => n?.path).slice(0, 2)).toEqual(['/portfolio', '/portfolio/positions'])
    expect(nav[1]).toMatchObject({ labelKey: 'portfolio:nav.positions', section: 'portfolio', order: 20 })
  })

  it('shows every column for the seeded BTC long and XAU short, matching the API', async () => {
    fakeBackend({ taken_at: '2026-10-28T17:36:00.000000Z', items: [BTC, XAU] })
    renderPositions()
    await screen.findByTestId('positions-table')

    const headers = within(screen.getByTestId('positions-table'))
      .getAllByRole('columnheader')
      .map((h) => h.textContent)
    expect(headers).toEqual([
      'Instrument',
      'Side',
      'Size',
      'Leverage',
      'Margin mode',
      'Entry',
      'Mark',
      'Liq. price',
      'Unrealised PnL',
      'Time in trade',
    ])
    // No Phase 2 columns.
    expect(screen.queryByText(/stop|target|agent|close/i)).toBeNull()

    const [btc, xau] = screen.getAllByTestId('position-row')
    expect(btc).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
    expect(cell(btc!, 'instrument')).toHaveTextContent('BTC-USDT-SWAP')
    expect(cell(btc!, 'side')).toHaveTextContent('Long')
    expect(cell(btc!, 'side').querySelector('svg')).not.toBeNull()
    expect(cell(btc!, 'size')).toHaveTextContent('25contracts')
    expect(cell(btc!, 'leverage')).toHaveTextContent('5×')
    expect(cell(btc!, 'mgnMode')).toHaveTextContent('Isolated')
    expect(cell(btc!, 'entry')).toHaveTextContent('67,250.5')
    expect(cell(btc!, 'mark')).toHaveTextContent('68,120.3')
    expect(cell(btc!, 'liq')).toHaveTextContent('54,112.8')
    expect(cell(btc!, 'upl')).toHaveTextContent('+$217.45')
    expect(cell(btc!, 'upl')).toHaveTextContent('+6.47%')
    // 08:36 UTC → 17:40 UTC is 9 h 4 min; opened 11:36 Baghdad on Oct 28.
    expect(cell(btc!, 'time')).toHaveTextContent(/9h\s*4m/)
    expect(cell(btc!, 'time')).toHaveTextContent('Oct 28')

    expect(cell(xau!, 'side')).toHaveTextContent('Short')
    expect(cell(xau!, 'size')).toHaveTextContent('400')
    expect(cell(xau!, 'leverage')).toHaveTextContent('3×')
    expect(cell(xau!, 'mgnMode')).toHaveTextContent('Cross')
    expect(cell(xau!, 'entry')).toHaveTextContent('2,741.80')
    expect(cell(xau!, 'mark')).toHaveTextContent('2,728.40')
    expect(cell(xau!, 'liq')).toHaveTextContent('3,618.90')
    expect(cell(xau!, 'time')).toHaveTextContent(/1d\s*3h/)

    expect(screen.getByTestId('positions-summary')).toHaveTextContent('2 open positions')
    expect(screen.getByTestId('positions-upl-total')).toHaveTextContent('+$163.85')
    expect(await screen.findByTestId('reconciled')).toHaveTextContent('Reconciled with OKX at 20:36:00')
  })

  it('gives unrealised PnL a sign, a colour and an icon, and end-aligns every number column', async () => {
    fakeBackend({ taken_at: '2026-10-28T17:36:00.000000Z', items: [BTC, XAU] })
    renderPositions()
    const [btc, xau] = await screen.findAllByTestId('position-row')

    const gain = cell(btc!, 'upl').querySelector('[data-sign]')!
    expect(gain).toHaveAttribute('data-sign', 'profit')
    expect(gain).toHaveClass('text-profit-ink')
    expect(gain.querySelector('[data-sign-icon]')).not.toBeNull()
    const loss = cell(xau!, 'upl').querySelector('[data-sign]')!
    expect(loss).toHaveAttribute('data-sign', 'loss')
    expect(loss).toHaveTextContent('−$53.60')
    expect(loss).toHaveClass('text-loss-ink')
    expect(loss.querySelector('[data-sign-icon]')).not.toBeNull()

    for (const col of ['size', 'leverage', 'entry', 'mark', 'liq', 'upl', 'time']) {
      expect(cell(btc!, col)).toHaveClass('is-num')
      expect(document.querySelector(`th[data-col="${col}"]`)).toHaveClass('is-num')
    }
    expect(cell(btc!, 'instrument')).not.toHaveClass('is-num')
  })

  it('shows the empty state when OKX reports no open positions', async () => {
    fakeBackend({ taken_at: '2026-10-28T17:36:00.000000Z', items: [] })
    renderPositions()
    expect(await screen.findByText('No open positions')).toBeInTheDocument()
    expect(screen.getByText(/No open positions on the OKX demo account\./)).toBeInTheDocument()
    expect(screen.queryByTestId('positions-table')).toBeNull()
  })

  it('shows the syncing state before the first positions tick', async () => {
    fakeBackend({ taken_at: null, items: [] })
    renderPositions()
    expect(await screen.findByText('No data yet')).toBeInTheDocument()
  })

  it('shows an error with retry when the API fails', async () => {
    const fetch = fakeBackend('fail')
    renderPositions()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Couldn’t load your portfolio')
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const before = fetch.mock.calls.length
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(fetch.mock.calls.length).toBeGreaterThan(before)
  })

  it('polls the positions every 30 s', async () => {
    const fetch = fakeBackend({ taken_at: '2026-10-28T17:36:00.000000Z', items: [BTC] })
    renderPositions()
    await screen.findByTestId('positions-table')
    const calls = () => fetch.mock.calls.filter(([u]) => String(u).includes('/api/portfolio/positions')).length
    const first = calls()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(calls()).toBeGreaterThan(first)
  })

  it('mirrors in Arabic and keeps Western digits', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend({ taken_at: '2026-10-28T17:36:00.000000Z', items: [BTC, XAU] })
    renderPositions()
    const [btc] = await screen.findAllByTestId('position-row')
    expect(screen.getAllByRole('columnheader')[0]).toHaveTextContent('الأداة')
    expect(cell(btc!, 'side')).toHaveTextContent('شراء')
    expect(cell(btc!, 'entry')).toHaveTextContent('67,250.5')
    expect(cell(btc!, 'upl')).toHaveTextContent('+$217.45')
    expect(document.body.textContent).not.toMatch(/[٠-٩]/)
  })
})
