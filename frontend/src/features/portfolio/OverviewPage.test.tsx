import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL } from '../../lib/api'
import type { BenchmarkSeries, BenchmarkSymbol, EquitySeries, PortfolioSummary } from './api'
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

/** ATL-176's benchmark body: rebased to the equity's first point, then the close's ratio. */
function benchmark(symbol: BenchmarkSymbol, range: string, available: boolean): BenchmarkSeries {
  const eq = series(range, range === '1D' ? 5 : 40)
  const base = { ts: eq.points[0]!.ts, equity_usd: eq.points[0]!.equity_usd, close: '60000', candle_ts: eq.points[0]!.ts }
  const closes = ['60000', '61200', '62400', '63000']
  return {
    symbol,
    env: 'demo',
    range: range as BenchmarkSeries['range'],
    from: eq.from,
    to: eq.to,
    available,
    reason: available ? null : 'not_in_universe',
    inst_id: available ? `${symbol}-USDT-SWAP` : null,
    bar: available ? '4H' : null,
    base: available ? base : null,
    points: available
      ? closes.map((close, i) => ({
          ts: eq.points[Math.round((i * (eq.points.length - 1)) / (closes.length - 1))]!.ts,
          close,
          value_usd: ((5_000_000 * Number(close)) / 60000).toFixed(2),
        }))
      : [],
  }
}

interface Backend {
  summary?: PortfolioSummary
  points?: number
  failSummary?: boolean
  slow?: boolean
  /** Symbols the env has no history for; the rest are available. Unset: benchmark 404s. */
  unavailable?: BenchmarkSymbol[]
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
    if (url.pathname === '/api/portfolio/benchmark' && state.unavailable) {
      const symbol = url.searchParams.get('symbol') as BenchmarkSymbol
      const range = url.searchParams.get('range') ?? '1M'
      return reply(200, { data: benchmark(symbol, range, !state.unavailable.includes(symbol)) })
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

const benchmarkCalls = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/api/portfolio/benchmark'))

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

  it('choosing BTC fetches its benchmark for the active range and draws a second series; None removes it', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { fetch } = fakeBackend({ unavailable: [] })
    renderOverview()
    const chart = await screen.findByTestId('equity-chart')
    expect(chart).not.toHaveAttribute('data-benchmark')
    expect(screen.getByRole('radiogroup', { name: 'Compare with' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'None' })).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('radio', { name: 'BTC' }))
    await waitFor(() => expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-benchmark', 'BTC'))
    expect(benchmarkCalls(fetch).some((u) => u.includes('symbol=BTC') && u.includes('range=1M'))).toBe(true)
    // The second series: its legend with the change since the base, a column in the screen-reader
    // table, and the chart's label.
    expect(screen.getByTestId('legend-bench')).toHaveTextContent('BTC')
    expect(screen.getByTestId('legend-bench')).toHaveTextContent('+5.00%')
    expect(screen.getByTestId('legend-bench')).toHaveAttribute('title', expect.stringMatching(/^Rebased to your equity/))
    expect(screen.getByTestId('legend-equity')).toHaveTextContent('Your equity')
    expect(within(screen.getByTestId('equity-table')).getByRole('columnheader', { name: 'BTC' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Compared with BTC/ })).toBeInTheDocument()

    // A new range fetches that range's benchmark.
    await user.click(screen.getByRole('radio', { name: '1D' }))
    await waitFor(() => expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-range', '1D'))
    await waitFor(() => expect(screen.getByTestId('equity-chart')).toHaveAttribute('data-benchmark', 'BTC'))
    expect(benchmarkCalls(fetch).some((u) => u.includes('symbol=BTC') && u.includes('range=1D'))).toBe(true)

    await user.click(screen.getByRole('radio', { name: 'None' }))
    await waitFor(() => expect(screen.getByTestId('equity-chart')).not.toHaveAttribute('data-benchmark'))
    expect(screen.queryByTestId('legend-bench')).toBeNull()
    expect(screen.queryByTestId('legend-equity')).toBeNull()
    expect(within(screen.getByTestId('equity-table')).queryByRole('columnheader', { name: 'BTC' })).toBeNull()
  })

  it('renders an unavailable SPY disabled, with the locale file’s tooltip', async () => {
    fakeBackend({ unavailable: ['SPY'] })
    renderOverview()
    await screen.findByTestId('equity-chart')
    const spy = screen.getByRole('radio', { name: 'SPY' })
    await waitFor(() => expect(spy).toBeDisabled())
    const tip = 'No SPY history for this range on the demo account yet'
    expect(spy.closest('[data-segment-tip]')).toHaveAttribute('title', tip)
    expect(spy).toHaveAccessibleDescription(tip)
    expect(screen.getByRole('radio', { name: 'BTC' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: 'Gold' })).toBeEnabled()

    await i18n.changeLanguage('ar')
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'SPY' })).toHaveAccessibleDescription(
        'لا يتوفر سجل لـ SPY في هذا المدى على الحساب التجريبي بعد',
      ),
    )
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

  it('leaves "/" to the shell, which opens the War Room (design v4 War Room §3)', () => {
    expect(portfolio.routes.some((r) => r.index)).toBe(false)
  })
})
