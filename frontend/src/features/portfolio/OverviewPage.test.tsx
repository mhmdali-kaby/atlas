import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL } from '../../lib/api'
import type { EquitySeries, PortfolioSummary } from './api'
import portfolio from './routes'

/* The ATL-12 handoff's example bodies: what PortfolioDemoSeeder data looks like. */
const NOW = Date.parse('2026-10-28T17:40:00Z')
const SUMMARY: PortfolioSummary = {
  env: 'demo',
  total_equity_usd: '5136121.59',
  net_deposits_usd: '1000',
  total_pnl_usd: '135121.59',
  total_pnl_pct: '2.7019',
  pnl_since: '2026-09-28T17:36:00.000000Z',
  today_pnl_usd: '7219.64',
  unrealised_pnl_usd: '271.05',
  fees_paid_usd: '13.88991',
  funding_net_usd: '-0.036183',
  equity_as_of: '2026-10-28T17:36:00.000000Z',
  reconciled_at: '2026-10-28T17:36:00.000000Z',
  stale: false,
}

function series(range: string, n: number): EquitySeries {
  const to = NOW
  const span = range === '1D' ? 86_400_000 : 30 * 86_400_000
  const points = Array.from({ length: n }, (_, i) => ({
    ts: new Date(to - span + (span * i) / Math.max(1, n - 1)).toISOString(),
    equity_usd: (5_000_000 + i * 100).toFixed(2),
  }))
  return {
    range: range as EquitySeries['range'],
    from: new Date(to - span).toISOString(),
    to: new Date(to).toISOString(),
    points,
    markers: [],
  }
}

interface Backend {
  summary?: PortfolioSummary
  points?: number
  failSummary?: boolean
  slow?: boolean
}

function fakeBackend(opts: Backend = {}) {
  const state = { ...opts }
  const reply = (status: number, body?: unknown) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    const path = String(input).replace(API_URL, '')
    if (state.slow) await new Promise(() => {})
    if (url.pathname === '/api/portfolio/summary') {
      return state.failSummary ? reply(500, { message: 'Server Error' }) : reply(200, { data: state.summary ?? SUMMARY })
    }
    if (url.pathname === '/api/portfolio/equity') {
      const range = url.searchParams.get('range') ?? '1M'
      return reply(200, { data: series(range, state.points ?? (range === '1D' ? 5 : 40)) })
    }
    if (url.pathname === '/api/portfolio/positions') {
      return reply(200, { data: { taken_at: SUMMARY.equity_as_of, items: [{ id: 'a', inst_id: 'BTC-USDT-SWAP' }, { id: 'b', inst_id: 'XAU-USDT-SWAP' }] } })
    }
    if (url.pathname === '/api/connections') {
      return reply(200, { data: [{ id: 'c1', exchange: 'okx', env: 'demo', uid_last4: '4821', last_verified_at: SUMMARY.reconciled_at }] })
    }
    return reply(404, { message: `Not found: ${path}` })
  })
  vi.stubGlobal('fetch', fetch)
  return { state, fetch }
}

const equityCalls = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/api/portfolio/equity'))

function renderOverview(path = '/portfolio') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: [path] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', RO)
})

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})

describe('Portfolio Overview', () => {
  it('shows the hero equity and every tile from /api/portfolio/summary with sign, currency, colour and icon', async () => {
    fakeBackend()
    renderOverview()
    const hero = await screen.findByTestId('hero-equity')
    expect(hero).toHaveTextContent('$5,136,121.59')

    expect(screen.getByTestId('tile-net-deposits')).toHaveTextContent('$1,000.00')
    expect(screen.getByTestId('tile-fees')).toHaveTextContent('$13.89')

    const signed = (key: string) => screen.getByTestId(`tile-${key}`).querySelector('[data-sign]')!
    expect(signed('total-pnl')).toHaveAttribute('data-sign', 'profit')
    expect(signed('total-pnl')).toHaveTextContent('+$135,121.59')
    expect(signed('total-pnl')).toHaveClass('text-profit-ink')
    expect(signed('total-pnl').querySelector('[data-sign-icon]')).not.toBeNull()
    expect(screen.getByTestId('tile-total-pnl-pct')).toHaveTextContent('+2.70%')
    expect(signed('today-pnl')).toHaveTextContent('+$7,219.64')
    expect(signed('unrealised')).toHaveTextContent('+$271.05')
    expect(signed('funding')).toHaveAttribute('data-sign', 'loss')
    expect(signed('funding')).toHaveTextContent('−$0.04')
    expect(signed('funding')).toHaveClass('text-loss-ink')
    expect(signed('funding').querySelector('[data-sign-icon]')).not.toBeNull()

    expect(await screen.findByText('2 open positions')).toBeInTheDocument()
    expect(screen.getAllByText('since Sep 28').length).toBeGreaterThan(0)
    expect(screen.getByTestId('reconciled')).toHaveAttribute('data-state', 'ok')
    // 17:36:00 UTC is 20:36:00 in Baghdad.
    expect(screen.getByTestId('reconciled')).toHaveTextContent('Reconciled with OKX at 20:36:00')
    expect(screen.getByText('•••• 4821')).toBeInTheDocument()
  })

  it('shows "—" with a reason when total_pnl_pct is null (decision 238)', async () => {
    fakeBackend({ summary: { ...SUMMARY, total_pnl_pct: null } })
    renderOverview()
    const pct = await screen.findByTestId('tile-total-pnl-pct')
    expect(pct).toHaveTextContent('—')
    expect(within(pct).getByRole('note')).toHaveAccessibleName(/starting equity above zero/)
  })

  it('switching the range from 1M to 1D refetches ?range=1D, redraws, and the active segment is announced', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { fetch } = fakeBackend()
    renderOverview()
    await waitFor(() => expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-range', '1M'))
    expect(equityCalls(fetch).some((u) => u.includes('range=1M'))).toBe(true)
    expect(screen.getByRole('radio', { name: '1M' })).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('radio', { name: '1D' }))
    await waitFor(() => expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-range', '1D'))
    expect(equityCalls(fetch).some((u) => u.includes('range=1D'))).toBe(true)
    expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-points', '5')
    expect(screen.getByRole('radiogroup', { name: 'Chart range' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '1D' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('range-announcement')).toHaveTextContent('Chart range: 1D')
    expect(screen.getByRole('img', { name: /Equity over 1D/ })).toBeInTheDocument()
    // The screen-reader table carries the redrawn values.
    expect(within(screen.getByTestId('equity-table')).getAllByRole('row').length).toBe(6)
  })

  it('shows the warning state with the minutes since the last sync when summary.stale is true', async () => {
    fakeBackend({ summary: { ...SUMMARY, stale: true, reconciled_at: new Date(NOW - 12 * 60_000).toISOString() } })
    renderOverview()
    const indicator = await screen.findByTestId('reconciled')
    expect(indicator).toHaveAttribute('data-state', 'stale')
    expect(indicator).toHaveTextContent('Last synced 12 minutes ago')
    expect(indicator).toHaveClass('text-warning-ink')
  })

  it('shows the empty state before the first sync', async () => {
    fakeBackend({
      summary: { ...SUMMARY, total_equity_usd: null, total_pnl_usd: null, total_pnl_pct: null, reconciled_at: null, stale: true },
      points: 0,
    })
    renderOverview()
    expect(await screen.findByTestId('state-empty')).toHaveTextContent('No data yet')
    expect(screen.getByTestId('state-empty')).toHaveTextContent('Atlas is syncing your OKX demo account')
  })

  it('shows the chart empty state when the equity API returns no points', async () => {
    fakeBackend({ points: 0 })
    renderOverview()
    await screen.findByTestId('hero-equity')
    expect(await screen.findByTestId('state-empty')).toHaveTextContent('No data yet')
    expect(screen.queryByTestId('equity-chart')).toBeNull()
  })

  it('shows skeletons while the API is slow', async () => {
    fakeBackend({ slow: true })
    renderOverview()
    expect(await screen.findByRole('status')).toHaveTextContent('Loading your portfolio')
    expect(screen.getByTestId('chart-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('hero-equity')).toBeNull()
  })

  it('shows the error state when the API fails, and Retry refetches', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { state, fetch } = fakeBackend({ failSummary: true })
    renderOverview()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Couldn’t load your portfolio')
    const before = fetch.mock.calls.filter(([u]) => String(u).includes('/summary')).length

    state.failSummary = false
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('hero-equity')).toHaveTextContent('$5,136,121.59')
    expect(fetch.mock.calls.filter(([u]) => String(u).includes('/summary')).length).toBeGreaterThan(before)
  })

  it('polls the summary every 30 s', async () => {
    const { fetch } = fakeBackend()
    renderOverview()
    await screen.findByTestId('hero-equity')
    const count = () => fetch.mock.calls.filter(([u]) => String(u).includes('/summary')).length
    const before = count()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000)
    })
    expect(count()).toBe(before + 1)
  })

  it('keeps Western digits and mirrors the time axis in Arabic', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderOverview()
    expect(await screen.findByTestId('hero-equity')).toHaveTextContent('$5,136,121.59')
    expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-reversed', 'true')
    expect(screen.getByTestId('reconciled')).toHaveTextContent('20:36:00')
    expect(document.body.textContent).not.toMatch(/[٠-٩۰-۹]/)
  })

  it('sends a signed-in owner at "/" to /portfolio', async () => {
    fakeBackend()
    const router = renderOverview('/')
    await waitFor(() => expect(router.state.location.pathname).toBe('/portfolio'))
  })
})
