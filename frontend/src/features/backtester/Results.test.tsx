import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { resetCsrfForTests } from '../../lib/api'
import type { RunDetail } from './api'
import { doneResult, instrument, metrics, overfitResult, trades } from './fixtures'
import { percentAxis, type BacktestResult } from './result'
import { Results, VIRTUAL_AFTER } from './Results'
import backtester from './routes'

/* ATL-210: a done run's results, and a failed run's reason, on ATL-208's GET /api/backtests/{id}. */

const RUN_ID = '9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10'
const plain = (el: HTMLElement) => (el.textContent ?? '').replace(/[⁦-⁩]/g, '')

function renderResults(result: unknown) {
  return render(<Results raw={result} finishedAt="2026-11-02T08:15:07Z" top={(meta) => <header data-testid="top">{meta}</header>} />)
}

function run(extra: Partial<RunDetail>): RunDetail {
  return {
    id: RUN_ID,
    env: 'demo',
    status: 'done',
    rule_name: 'sma_10_30_cross',
    side: 'long',
    inst_ids: ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'],
    bar: '1H',
    period: { start: '2026-04-30T21:00:00Z', end: '2026-09-30T21:00:00Z' },
    n_trades: 36,
    overfit_warning: false,
    data_gap: false,
    error: null,
    error_reason: null,
    created_at: '2026-11-02T08:15:00Z',
    started_at: '2026-11-02T08:15:01Z',
    finished_at: '2026-11-02T08:15:07Z',
    request: {
      rule_spec: {
        name: 'sma_10_30_cross',
        side: 'long',
        indicators: { sma10: { kind: 'sma', window: 10 }, sma30: { kind: 'sma', window: 30 } },
        entry: [{ left: 'sma10', op: 'crosses_above', right: 'sma30' }],
        exit: [{ left: 'sma10', op: 'crosses_below', right: 'sma30' }],
        stop_pct: 3,
        take_profit_pct: null,
      },
      inst_ids: ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'],
      bar: '1H',
      period: { start: '2026-04-30T21:00:00Z', end: '2026-09-30T21:00:00Z' },
      splits: 3,
      slippage_bps: 2,
    },
    result: null,
    ...extra,
  }
}

function renderRunPage(detail: RunDetail) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input))
      const body = url.pathname === `/api/backtests/${RUN_ID}` ? detail : url.pathname === '/api/backtests' ? { env: 'demo', data: [] } : { message: 'Not found' }
      return new Response(JSON.stringify(body), { status: body === detail || 'env' in body ? 200 : 404, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    backtester.routes.map((r) => ({ path: `/${r.path}`, element: r.element })),
    { initialEntries: [`/backtester/run?id=${RUN_ID}`] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  resetCsrfForTests()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('a done result', () => {
  it('renders every metric for each instrument', async () => {
    const user = userEvent.setup()
    renderResults(doneResult())
    const tile = (id: string) => plain(screen.getByTestId(`bt-m-${id}-v`))

    expect(screen.getByTestId('bt-tiles')).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
    expect(tile('total')).toContain('+8.32%')
    expect(tile('cagr')).toContain('+21.04%')
    expect(tile('sharpe')).toBe('1.21')
    expect(tile('sortino')).toBe('1.67')
    expect(tile('dd')).toContain('−6.14%')
    expect(tile('win')).toBe('50%')
    expect(plain(screen.getByTestId('bt-m-win'))).toContain('9 of 18 trades')
    expect(tile('pf')).toBe('1.42')
    expect(tile('trades')).toBe('18')
    expect(tile('fees')).toContain('−1.70%')
    expect(tile('funding')).toContain('−0.61%')
    // signed with an icon and words, not colour alone
    expect(screen.getByTestId('bt-m-total-v').querySelector('[data-sign="profit"] [data-sign-icon]')).not.toBeNull()
    expect(screen.getByTestId('bt-m-dd-v').querySelector('[data-sign="loss"] [data-sign-icon]')).not.toBeNull()

    await user.click(screen.getByRole('radio', { name: 'XAU' }))
    expect(screen.getByTestId('bt-tiles')).toHaveAttribute('data-inst', 'XAU-USDT-SWAP')
    expect(tile('total')).toContain('−2.15%')
    expect(tile('cagr')).toContain('−5.10%')
    expect(tile('sharpe')).toBe('−0.42')
    expect(tile('sortino')).toBe('−0.60')
    expect(tile('dd')).toContain('−8.91%')
    expect(tile('win')).toBe('25%')
    expect(tile('pf')).toBe('0.71')
    expect(tile('trades')).toBe('12')
    expect(tile('funding')).toContain('+0.12%')
    expect(screen.getAllByTestId('bt-trade')).toHaveLength(12)
    // fill prices at display precision, not the float's 12 places
    expect(plain(screen.getAllByTestId('bt-trade')[4]!)).toContain('2,440.00')
    expect(plain(screen.getAllByTestId('bt-trade')[4]!)).not.toMatch(/\d{5,}/)
  })

  it('has one split row per fold with fitted and tested figures', () => {
    renderResults(doneResult())
    const folds = screen.getByTestId('bt-folds')
    const rows = within(folds).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    const first = screen.getByTestId('bt-fold-1')
    expect(plain(first)).toContain('1.08 → 0.94')
    expect(within(first).getByRole('img')).toHaveAccessibleName('Sharpe fitted 1.08, tested 0.94')
    // fitted on May up to the block, tested on the next block
    expect(plain(first)).toMatch(/1 May–\d+ \w+/)
    expect(plain(first)).toContain('+1.22%')
    expect(plain(screen.getByTestId('bt-fold-2'))).toContain('1.17 → 1.32')
    expect(plain(screen.getByTestId('bt-fold-3'))).toContain('1.25 → 0.88')
  })

  it('shows the run line, the fee used, the left-out instrument and the equity curve', () => {
    renderResults(doneResult())
    const meta = plain(screen.getByTestId('bt-meta'))
    expect(meta).toContain('Ran 2 Nov, 11:15, on demo candles up to 1 Nov, 23:00.')
    expect(meta).toContain('Taker fee 0.05% from Atlas’s defaults')
    expect(plain(screen.getByTestId('bt-excluded'))).toBe('CL-USDT-SWAP left out: no 1H candles in demo for the period')
    const chart = screen.getByTestId('bt-equity-chart')
    expect(chart).toHaveAttribute('data-points', '241')
    expect(chart).toHaveAttribute('data-reversed', 'false')
    expect(chart.getAttribute('aria-label')).toMatch(/^Equity from 1 May 2026 to 1 Oct 2026: [+−]\d/)
  })

  it('says "Not enough to say" for null metrics and "No losing trades" for a null profit factor', () => {
    renderResults(doneResult({ instruments: [instrument('ETH-USDT-SWAP', { full: metrics({ sharpe: null, sortino: null, profit_factor: null }), funding_included: false })] }))
    expect(plain(screen.getByTestId('bt-m-sharpe-v'))).toBe('Not enough to say')
    expect(plain(screen.getByTestId('bt-m-pf-v'))).toBe('No losing trades')
    expect(plain(screen.getByTestId('bt-m-funding'))).toContain('No funding stored for this window')
    expect(screen.queryByRole('radiogroup')).toBeNull()
  })

  it('virtualises a long trade list and says when it was cut', () => {
    // jsdom has no layout: give the trade scroller its 560px so the virtualiser has a window.
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('is-virtual') ? 560 : 0
    })
    renderResults(doneResult({ instruments: [instrument('BTC-USDT-SWAP', { trades: trades(1000), trades_total: 1400, trades_truncated: true })] }))
    const section = screen.getByTestId('bt-trades')
    expect(section).toHaveAttribute('data-virtual', 'true')
    expect(plain(section)).toContain('1,400 closed trades over the whole period Showing the first 1,000 of 1,400.')
    const shown = within(section).getAllByTestId('bt-trade').length
    expect(shown).toBeGreaterThan(0)
    expect(shown).toBeLessThan(100)
    expect(VIRTUAL_AFTER).toBeLessThan(1000)
  })

  it('shows data-quality notes for a gap in the candles', () => {
    const r: BacktestResult = doneResult({
      data_gap: true,
      instruments: [instrument('XAU-USDT-SWAP', { data_gap: true, verdict_cap: 'does_not_support', gap_notes: ['15 1H bars missing from 2026-09-12T04:00Z to 2026-09-12T19:00Z.'] })],
    })
    renderResults(r)
    const gap = screen.getByTestId('bt-gap')
    expect(plain(gap)).toContain('There’s a gap in the candles')
    expect(plain(gap)).toContain('XAU-USDT-SWAP: 15 1H bars missing from 2026-09-12T04:00Z to 2026-09-12T19:00Z.')
    expect(plain(gap)).toContain('capped at “does not support”')
  })

  it('shows the overfit warning only when the result carries one', () => {
    const { unmount } = renderResults(overfitResult())
    const warning = screen.getByTestId('bt-overfit')
    expect(warning).toHaveAttribute('role', 'alert')
    expect(plain(warning)).toContain('Probably overfit')
    expect(plain(warning)).toContain('this rule’s Sharpe averaged 1.53. On data it hadn’t seen, 0.38: under half.')
    expect(screen.getByTestId('bt-fold-2').querySelector('.bt-pair')).toHaveAttribute('data-wide', 'true')
    unmount()

    renderResults(doneResult())
    expect(screen.queryByTestId('bt-overfit')).toBeNull()
    expect(screen.queryByText('Probably overfit')).toBeNull()
  })

  it('renders in Arabic with the time axis reversed', async () => {
    await i18n.changeLanguage('ar')
    renderResults(overfitResult())
    expect(screen.getByTestId('bt-equity-chart')).toHaveAttribute('data-reversed', 'true')
    expect(plain(screen.getByTestId('bt-overfit'))).toContain('على الأرجح مُفرط في المواءمة')
    expect(plain(screen.getByTestId('bt-m-sharpe-v'))).toBe('1.21')
  })

  it('says so when the stored result is not one it can read', () => {
    renderResults({ ok: true })
    expect(screen.getByTestId('bt-unreadable')).toHaveTextContent('This result can’t be shown')
  })
})

describe('the run page', () => {
  it('puts the results under the header for a done run', async () => {
    renderRunPage(run({ result: doneResult() as unknown as Record<string, unknown> }))
    expect(await screen.findByTestId('bt-results')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('sma_10_30_cross')
    expect(screen.getByTestId('bt-meta').closest('header')).not.toBeNull()
  })

  it("shows trading_core's reason for a failed run", async () => {
    renderRunPage(run({ status: 'failed', error: 'insufficient_data', error_reason: 'BTC-USDT-SWAP: 4 bars are too few for 3 walk-forward splits', n_trades: null }))
    const failed = await screen.findByTestId('bt-failed')
    expect(failed).toHaveTextContent('Too few bars for the splits')
    expect(failed).toHaveTextContent('BTC-USDT-SWAP: 4 bars are too few for 3 walk-forward splits')
    expect(screen.queryByTestId('bt-results')).toBeNull()
  })
})

describe('the value axis', () => {
  it('always includes 0% and uses round steps', () => {
    const a = percentAxis([-1.2, 4.8, 8.3])
    expect(a.ticks).toContain(0)
    expect(a.lo).toBeLessThan(-1.2)
    expect(a.hi).toBeGreaterThan(8.3)
    const steps = a.ticks.slice(1).map((v, i) => +(v - a.ticks[i]!).toFixed(6))
    expect(new Set(steps).size).toBe(1)
  })
})
