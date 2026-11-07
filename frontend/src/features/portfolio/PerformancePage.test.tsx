import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import en from '../../i18n/locales/en/portfolio-performance.json'
import ar from '../../i18n/locales/ar/portfolio-performance.json'
import { resetCsrfForTests } from '../../lib/api'
import type { PortfolioSummary } from './api'
import { heatStep } from './performanceFormat'
import type { Performance } from './performanceApi'
import portfolio from './routes'

/* ATL-174's GET /api/portfolio/performance shape (docs/handoff/ATL-174-portfolio-performance-api.md). */
const PERF: Performance = {
  env: 'demo',
  range: 'ALL',
  from: null,
  to: '2026-10-28T11:40:00.000000Z',
  timezone: 'Asia/Baghdad',
  trades: {
    total: 37,
    wins: 20,
    losses: 17,
    flats: 0,
    excluded_non_usd: 0,
    gross_profit_usd: '12248',
    gross_loss_usd: '-6767.7',
    net_pnl_usd: '5480.3',
  },
  win_rate_pct: { value: '54.05', sample_size: 37, reason: null },
  avg_win_usd: { value: '612.4', sample_size: 20, reason: null },
  avg_loss_usd: { value: '-398.1', sample_size: 17, reason: null },
  profit_factor: { value: '1.8099', sample_size: 37, reason: null },
  expectancy_r: { value: '0.3100', sample_size: 33, reason: null },
  max_drawdown: {
    usd: '2410',
    pct: '2.40',
    peak_usd: '100410',
    trough_usd: '98000',
    peak_at: '2026-10-09T09:00:00.000000Z',
    trough_at: '2026-10-14T09:00:00.000000Z',
    sample_size: 89280,
    reason: null,
  },
  sharpe: { value: null, sample_size: 7, reason: 'insufficient_data' },
  sortino: { value: '2.0500', sample_size: 62, reason: null },
  breakdowns: {
    by_instrument: [
      { inst_id: 'SOL-USDT-SWAP', pnl_usd: '3120.5', trades: 9, wins: 6, losses: 3 },
      { inst_id: 'XAU-USDT-SWAP', pnl_usd: '-840.25', trades: 6, wins: 2, losses: 4 },
    ],
    by_asset_class: [
      { asset_class: 'crypto', pnl_usd: '4200', trades: 25, wins: 15, losses: 10 },
      { asset_class: 'commodity', pnl_usd: '-840.25', trades: 6, wins: 2, losses: 4 },
    ],
    by_agent: [
      { agent: 'strategist', pnl_usd: '5100', trades: 33, wins: 18, losses: 15 },
      { agent: null, pnl_usd: '380.3', trades: 4, wins: 2, losses: 2 },
    ],
    by_weekday: [],
    by_hour: [],
    heatmap: [
      { weekday: 3, hour: 15, pnl_usd: '412.3', trades: 6, wins: 4, losses: 2 },
      { weekday: 5, hour: 9, pnl_usd: '-120', trades: 1, wins: 0, losses: 1 },
    ],
  },
}

const SUMMARY = { env: 'demo', reconciled_at: '2026-10-28T11:38:00Z', stale: false } as PortfolioSummary
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function fakeBackend(perf: Performance = PERF) {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/portfolio/summary') return json(200, { data: SUMMARY })
    if (url.pathname === '/api/portfolio/performance') {
      return json(200, { data: { ...perf, range: url.searchParams.get('range') ?? 'ALL' } })
    }
    return json(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const perfCalls = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.map(([input]) => new URL(String(input))).filter((u) => u.pathname === '/api/portfolio/performance')

/** Text with the bidi isolates removed. */
const plain = (el: Element) => (el.textContent ?? '').replace(/[⁦-⁩]/g, '')

function renderPerformance() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: ['/portfolio/performance'] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Portfolio Performance', () => {
  it('registers /portfolio/performance with a Performance nav entry after History', () => {
    const nav = (Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]).filter(Boolean)
    const paths = [...nav].sort((a, b) => a!.order - b!.order).map((n) => n!.path)
    expect(paths.indexOf('/portfolio/performance')).toBe(paths.indexOf('/portfolio/history') + 1)
    expect(portfolio.routes.map((r) => r.path)).toContain('portfolio/performance')
  })

  it('renders all 7 tiles with values and sample sizes, and a null Sharpe says "Not enough data"', async () => {
    fakeBackend()
    renderPerformance()
    const tiles = await screen.findByTestId('pp-tiles')
    const ids = ['win', 'avg', 'pf', 'exp', 'dd', 'sharpe', 'sortino']
    expect(within(tiles).getAllByTestId(/^pp-tile-[a-z]+$/).map((el) => el.dataset.testid)).toEqual(ids.map((i) => `pp-tile-${i}`))

    const tile = (id: string) => plain(screen.getByTestId(`pp-tile-${id}`))
    const sub = (id: string) => plain(screen.getByTestId(`pp-tile-${id}-sub`))
    expect(tile('win')).toContain('54%')
    expect(sub('win')).toBe('20 of 37 trades won')
    expect(tile('avg')).toContain('+$612.40')
    expect(tile('avg')).toContain('−$398.10')
    expect(sub('avg')).toBe('Wins are 1.54× the size of losses, over 20 wins and 17 losses')
    expect(tile('pf')).toContain('1.81')
    expect(sub('pf')).toBe('$12,248.00 won for $6,767.70 lost, 37 trades')
    expect(tile('exp')).toContain('+0.31R')
    expect(sub('exp')).toBe('Average per trade, 33 trades with a stop')
    expect(tile('dd')).toContain('−2.40%')
    expect(sub('dd')).toBe('−$2,410.00, Oct 9 to Oct 14, over 89280 equity readings')
    expect(tile('sortino')).toContain('2.05')
    expect(sub('sortino')).toBe('Downside only, 62 days')

    // Null Sharpe: the locale's words, and the sample it had.
    const sharpe = screen.getByTestId('pp-tile-sharpe')
    expect(sharpe).toHaveAttribute('data-null', 'true')
    expect(within(sharpe).getByText(en.nodata)).toBeInTheDocument()
    expect(sub('sharpe')).toBe('Only 7 days of equity so far')
    expect(plain(sharpe)).not.toMatch(/\d\.\d/)
  })

  it('shows "Not enough data" in Arabic from the Arabic locale file', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend()
    renderPerformance()
    const sharpe = await screen.findByTestId('pp-tile-sharpe')
    expect(within(sharpe).getByText(ar.nodata)).toBeInTheDocument()
  })

  it('refetches with range=3M when the range switches to 3M', async () => {
    const fetch = fakeBackend()
    renderPerformance()
    await screen.findByTestId('pp-tiles')
    expect(perfCalls(fetch).map((u) => u.searchParams.get('range'))).toEqual(['ALL'])
    expect(perfCalls(fetch)[0]!.searchParams.get('env')).toBe('demo')

    await userEvent.click(screen.getByRole('radio', { name: '3M' }))
    await waitFor(() => expect(perfCalls(fetch).map((u) => u.searchParams.get('range'))).toEqual(['ALL', '3M']))
    await waitFor(() => expect(screen.getByTestId('performance-page')).toHaveAttribute('data-range', '3M'))
    expect(screen.getByRole('radio', { name: '3M' })).toBeChecked()
  })

  it('draws a 7 × 24 heatmap whose cells name the weekday, hour and P&L', async () => {
    fakeBackend()
    renderPerformance()
    const grid = await screen.findByRole('grid', { name: en.heatLabel })
    const rows = within(grid).getAllByRole('row')
    expect(rows).toHaveLength(7)
    for (const row of rows) expect(within(row).getAllByRole('gridcell')).toHaveLength(24)
    const cells = within(grid).getAllByRole('gridcell')
    expect(cells).toHaveLength(7 * 24)

    const label = (el: Element) => el.getAttribute('aria-label')!.replace(/[⁦-⁩]/g, '')
    // Rows run Monday first; Wednesday is row 3, 15:00 is column 16.
    expect(label(within(rows[2]!).getAllByRole('gridcell')[15]!)).toBe('Wednesday 15:00 Baghdad: +$412.30 over 6 trades')
    expect(label(within(rows[4]!).getAllByRole('gridcell')[9]!)).toBe('Friday 09:00 Baghdad: −$120.00 over 1 trade')
    expect(label(within(rows[0]!).getAllByRole('gridcell')[0]!)).toBe('Monday 00:00 Baghdad: no trades')
    expect(label(within(rows[6]!).getAllByRole('gridcell')[23]!)).toBe('Sunday 23:00 Baghdad: no trades')
    for (const c of cells) expect(label(c)).toMatch(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) \d\d:00 Baghdad: /)

    // One tab stop, on the first cell with trades; arrows rove.
    expect(cells.filter((c) => c.tabIndex === 0)).toHaveLength(1)
    expect(within(rows[2]!).getAllByRole('gridcell')[15]).toHaveAttribute('tabindex', '0')
  })

  it('moves heatmap focus with the arrow keys', async () => {
    fakeBackend()
    renderPerformance()
    const grid = await screen.findByRole('grid')
    const rows = within(grid).getAllByRole('row')
    within(rows[2]!).getAllByRole('gridcell')[15]!.focus()
    await userEvent.keyboard('{ArrowRight}{ArrowDown}')
    expect(document.activeElement).toBe(within(rows[3]!).getAllByRole('gridcell')[16])
    expect(screen.getByTestId('pp-heat-tip')).toBeInTheDocument()
  })

  it('scales heat steps to the largest cell', () => {
    expect(heatStep(0, 800)).toBe(0)
    expect(heatStep(100, 800)).toBe(1)
    expect(heatStep(-400, 800)).toBe(2)
    expect(heatStep(800, 800)).toBe(4)
  })

  it('shows the error state with retry when the API fails', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input))
      if (url.pathname === '/api/portfolio/summary') return json(200, { data: SUMMARY })
      return json(500, { message: 'Server Error' })
    })
    vi.stubGlobal('fetch', fetch)
    renderPerformance()
    expect(await screen.findByRole('alert')).toHaveTextContent(en.error.title)
  })
})
