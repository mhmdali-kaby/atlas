import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { Holding, HoldingsPayload, PortfolioSummary } from './api'
import { byValueDesc } from './ledger'
import portfolio from './routes'

/* The ATL-12 handoff's /api/portfolio/holdings example: PortfolioDemoSeeder's three balances. */
const row = (h: Partial<Holding> & Pick<Holding, 'ccy' | 'account' | 'quantity'>): Holding => ({
  available: h.quantity,
  frozen: '0',
  value_usd: h.quantity,
  pct_of_portfolio: null,
  avg_cost_usd: null,
  change_24h_pct: null,
  change_24h_reason: h.change_24h_pct ? null : 'no_instrument',
  change_24h_inst_id: null,
  ...h,
})
const USDT_T = row({ ccy: 'USDT', account: 'trading', quantity: '5099121.59', value_usd: '5099121.59', pct_of_portfolio: '99.2796' })
const USDC_T = row({ ccy: 'USDC', account: 'trading', quantity: '25000', value_usd: '25000', pct_of_portfolio: '0.4867' })
const USDT_F = row({ ccy: 'USDT', account: 'funding', quantity: '12000', value_usd: '12000', pct_of_portfolio: '0.2336' })
const SEEDED: HoldingsPayload = { taken_at: '2026-10-28T17:36:00.000000Z', total_value_usd: '5136121.59', items: [USDT_T, USDC_T, USDT_F] }
const SUMMARY = { env: 'demo', reconciled_at: '2026-10-28T17:36:00.000000Z', stale: false } as PortfolioSummary

function fakeBackend(holdings: HoldingsPayload | 'fail', opts: { env?: 'demo' | 'live' } = {}) {
  const env = opts.env ?? 'demo'
  const reply = (status: number, body?: unknown) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/environment') {
      return reply(200, {
        data: { active_env: env, live_started_at: null, first_month_ends_at: null, in_first_live_month: false, switched_at: null, confirmation_text: 'TRADE LIVE', preflight: { ok: true, items: [] } },
      })
    }
    if (url.pathname === '/api/portfolio/summary') return reply(200, { data: SUMMARY })
    if (url.pathname === '/api/portfolio/holdings') {
      return holdings === 'fail' ? reply(500, { message: 'Server Error' }) : reply(200, { data: holdings })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderHoldings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: ['/portfolio/holdings'] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const cell = (r: HTMLElement, col: string) => r.querySelector<HTMLElement>(`[data-col="${col}"]`)!

beforeEach(async () => {
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Portfolio Holdings', () => {
  it('registers /portfolio/holdings with a Holdings nav entry at order 30', () => {
    expect(portfolio.area).toBe('portfolio')
    const nav = Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]
    expect(nav.find((n) => n?.path === '/portfolio/holdings')).toMatchObject({
      labelKey: 'portfolio:nav.holdings',
      order: 30,
    })
  })

  it('lists each currency per account with the API value and share, largest first', async () => {
    const fetch = fakeBackend(SEEDED)
    renderHoldings()
    const table = await screen.findByTestId('holdings-table')
    expect(String(fetch.mock.calls.find((c) => String(c[0]).includes('holdings'))![0])).toContain('env=demo')

    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Currency',
      'Account',
      'Quantity',
      'Value',
      'Share of portfolio',
      'Average cost',
      '24h change',
    ])
    const rows = screen.getAllByTestId('holding-row')
    expect(rows.map((r) => `${r.dataset.ccy}/${r.dataset.account}`)).toEqual(['USDT/trading', 'USDC/trading', 'USDT/funding'])
    expect(cell(rows[0]!, 'account')).toHaveTextContent('Trading')
    expect(cell(rows[2]!, 'account')).toHaveTextContent('Funding')
    expect(cell(rows[0]!, 'quantity')).toHaveTextContent('5,099,121.59')
    expect(cell(rows[0]!, 'value')).toHaveTextContent('$5,099,121.59')
    expect(cell(rows[0]!, 'share')).toHaveTextContent('99.28%')
    expect(cell(rows[1]!, 'share')).toHaveTextContent('0.49%')
    expect(cell(rows[2]!, 'share')).toHaveTextContent('0.23%')
    expect(screen.getByTestId('holdings-summary')).toHaveTextContent('3 balances across trading and funding')
    expect(screen.getByTestId('holdings-total')).toHaveTextContent('$5,136,121.59')
  })

  it('renders average cost and a signed 24h change, and a dash with its reason for nulls', async () => {
    const btc = row({
      ccy: 'BTC',
      account: 'trading',
      quantity: '1.5',
      value_usd: '99000',
      pct_of_portfolio: '60',
      avg_cost_usd: '60000.50',
      change_24h_pct: '10',
      change_24h_inst_id: 'BTC-USDT-SWAP',
    })
    const pepe = row({
      ccy: 'PEPE',
      account: 'trading',
      quantity: '1000000',
      value_usd: '50000',
      pct_of_portfolio: '30',
      avg_cost_usd: '0.00001234',
      change_24h_pct: '-2.5',
      change_24h_inst_id: 'PEPE-USDT',
    })
    const sol = row({ ccy: 'SOL', account: 'funding', quantity: '2', value_usd: '400', pct_of_portfolio: '5', change_24h_reason: 'no_candles' })
    const xyz = row({ ccy: 'XYZ', account: 'funding', quantity: '3', value_usd: '100', pct_of_portfolio: '1' })
    const usdt = row({ ccy: 'USDT', account: 'trading', quantity: '7000', value_usd: '7000', pct_of_portfolio: '4' })
    fakeBackend({ taken_at: SEEDED.taken_at, total_value_usd: '156500', items: [btc, pepe, sol, xyz, usdt] })
    renderHoldings()
    await screen.findByTestId('holdings-table')
    const rows = screen.getAllByTestId('holding-row')
    const byCcy = (ccy: string) => rows.find((r) => r.dataset.ccy === ccy)!

    // Average cost: every decimal the API fixed, in dollars.
    expect(cell(byCcy('BTC'), 'avgCost')).toHaveTextContent('$60,000.50')
    expect(cell(byCcy('PEPE'), 'avgCost')).toHaveTextContent('$0.00001234')

    // 24h change: signed, coloured, with an icon and a spoken direction.
    const up = within(cell(byCcy('BTC'), 'change')).getByTestId('change-24h')
    expect(up).toHaveTextContent('+10.00%')
    expect(up.querySelector('[data-sign="profit"]')).toHaveClass('text-profit-ink')
    expect(up.querySelector('[data-sign-icon]')).not.toBeNull()
    const down = within(cell(byCcy('PEPE'), 'change')).getByTestId('change-24h')
    expect(down).toHaveTextContent('−2.50%')
    expect(down.querySelector('[data-sign="loss"]')).toHaveClass('text-loss-ink')

    // Nulls: a dash whose tooltip and screen-reader text say why.
    const why = (ccy: string, col: string) => within(cell(byCcy(ccy), col)).getByTestId(col === 'avgCost' ? 'no-avg-cost' : 'no-change')
    expect(why('SOL', 'avgCost')).toHaveTextContent('—')
    expect(why('SOL', 'avgCost')).toHaveAttribute('title', 'No spot buys of SOL are recorded, so it has no average cost')
    expect(why('SOL', 'change')).toHaveAttribute('title', 'No SOL price history for the last 24 hours yet')
    expect(why('XYZ', 'change')).toHaveAttribute(
      'title',
      'OKX has no XYZ market against USDT or USDC on this account, so it has no 24h change',
    )
    expect(why('USDT', 'avgCost')).toHaveAttribute('title', 'Cash is counted at $1.00, so it has no average cost')
    expect(why('USDT', 'change')).toHaveTextContent('—')
    expect(why('USDT', 'change')).toHaveAttribute('title', 'Cash is counted at $1.00, so it has no 24h change')
  })

  it('renders the cost columns in Arabic', async () => {
    await i18n.changeLanguage('ar')
    const btc = row({ ccy: 'BTC', account: 'trading', quantity: '1', value_usd: '66000', pct_of_portfolio: '100', avg_cost_usd: '60000.50', change_24h_pct: '-1.2345' })
    fakeBackend({ taken_at: SEEDED.taken_at, total_value_usd: '66000', items: [btc, USDT_F] })
    renderHoldings()
    await screen.findByTestId('holdings-table')
    expect(screen.getByRole('columnheader', { name: 'متوسط التكلفة' })).toBeInTheDocument()
    const [first, second] = screen.getAllByTestId('holding-row')
    expect(cell(first!, 'avgCost')).toHaveTextContent('$60,000.50')
    expect(cell(first!, 'change')).toHaveTextContent('−1.23%')
    expect(within(cell(second!, 'change')).getByTestId('no-change')).toHaveAttribute(
      'title',
      'يُحتسب النقد بسعر ⁦$1.00⁩، فلا تغيّر له خلال 24 ساعة',
    )
  })

  it('keeps every digit of a quantity and sorts by value even if the API order differs', async () => {
    const btc = row({ ccy: 'BTC', account: 'trading', quantity: '0.123456789', value_usd: '8400.12', pct_of_portfolio: '50' })
    const unknown = row({ ccy: 'XYZ', account: 'funding', quantity: '3', value_usd: null, pct_of_portfolio: null })
    const eth = row({ ccy: 'ETH', account: 'trading', quantity: '3.5', value_usd: '10000', pct_of_portfolio: '50' })
    fakeBackend({ taken_at: SEEDED.taken_at, total_value_usd: '18400.12', items: [unknown, btc, eth] })
    renderHoldings()
    await screen.findByTestId('holdings-table')
    const rows = screen.getAllByTestId('holding-row')
    expect(rows.map((r) => r.dataset.ccy)).toEqual(['ETH', 'BTC', 'XYZ'])
    expect(cell(rows[1]!, 'quantity')).toHaveTextContent('0.123456789')
    expect(cell(rows[2]!, 'value')).toHaveTextContent('—')
    expect(cell(rows[2]!, 'share')).toHaveTextContent('—')
  })

  it('shows the syncing state before the first balances tick', async () => {
    fakeBackend({ taken_at: null, total_value_usd: '0', items: [] })
    renderHoldings()
    expect(await screen.findByTestId('state-empty')).toHaveTextContent('No data yet')
  })

  it('shows "No balances" when the synced tick is empty', async () => {
    fakeBackend({ taken_at: SEEDED.taken_at, total_value_usd: '0', items: [] })
    renderHoldings()
    expect(await screen.findByTestId('state-none')).toHaveTextContent('No balances')
    expect(screen.queryByTestId('holdings-table')).toBeNull()
  })

  it('in live, the empty state names the OKX live account, in English and Arabic (ATL-244)', async () => {
    fakeBackend({ taken_at: SEEDED.taken_at, total_value_usd: '0', items: [] }, { env: 'live' })
    renderHoldings()
    expect(await screen.findByText(/OKX reports no balances on the live account yet\./)).toBeInTheDocument()
    expect(screen.queryByText(/demo account/)).toBeNull()

    await i18n.changeLanguage('ar')
    expect(await screen.findByText(/في الحساب الحقيقي بعد/)).toBeInTheDocument()
    expect(screen.queryByText(/التجريبي/)).toBeNull()
  })

  it('shows the error state with a retry when the API fails', async () => {
    const fetch = fakeBackend('fail')
    renderHoldings()
    const alert = await screen.findByTestId('state-error')
    expect(alert).toHaveTextContent('Couldn’t load your portfolio')
    fakeBackend(SEEDED)
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('holdings-table')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalled()
  })

  it('renders in Arabic with Western digits', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend(SEEDED)
    renderHoldings()
    const rows = await screen.findAllByTestId('holding-row')
    expect(cell(rows[0]!, 'account')).toHaveTextContent('التداول')
    expect(cell(rows[0]!, 'value')).toHaveTextContent('$5,099,121.59')
    expect(screen.getByTestId('holdings-summary')).toHaveTextContent('3 أرصدة')
  })
})

describe('byValueDesc', () => {
  it('compares decimal strings without floats', () => {
    const v = (value_usd: string | null) => row({ ccy: 'X', account: 'trading', quantity: '1', value_usd })
    const sorted = [v('9.99'), v(null), v('10'), v('100.5'), v('100.25'), v('-5'), v('0')].sort(byValueDesc)
    expect(sorted.map((h) => h.value_usd)).toEqual(['100.5', '100.25', '10', '9.99', '0', '-5', null])
  })
})
