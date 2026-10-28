import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import type { CashflowMarker, PortfolioSummary } from './api'
import { flowState, shortTx } from './ledger'
import portfolio from './routes'

const SUMMARY = { env: 'demo', reconciled_at: '2026-10-28T17:36:00.000000Z', stale: false } as PortfolioSummary

/** 23 flows, newest first: every 7th a withdrawal; states cycle success / pending / failed. */
const ALL: CashflowMarker[] = Array.from({ length: 23 }, (_, i) => ({
  id: `flow-${i + 1}`,
  kind: i % 7 === 0 ? 'withdrawal' : 'deposit',
  ccy: i % 2 ? 'USDC' : 'USDT',
  amount: String(1000 + i),
  amount_usd: String(1000 + i),
  state: ['2', '0', '-1'][i % 3]!,
  tx_id: i === 1 ? null : `0x9f3a${'0'.repeat(50)}c41e${i}`,
  ts: new Date(Date.parse('2026-10-25T17:36:25Z') - i * 86_400_000).toISOString(),
}))

function fakeBackend(flows: CashflowMarker[] | 'fail') {
  const reply = (status: number, body?: unknown) =>
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input))
    if (url.pathname === '/api/portfolio/summary') return reply(200, { data: SUMMARY })
    if (url.pathname === '/api/portfolio/cashflows') {
      if (flows === 'fail') return reply(500, { message: 'Server Error' })
      const per = Number(url.searchParams.get('per_page') ?? 20)
      const page = Number(url.searchParams.get('page') ?? 1)
      const data = flows.slice((page - 1) * per, page * per)
      const last = Math.max(1, Math.ceil(flows.length / per))
      return reply(200, {
        data,
        links: {},
        meta: {
          current_page: page,
          last_page: last,
          per_page: per,
          from: data.length ? (page - 1) * per + 1 : null,
          to: data.length ? (page - 1) * per + data.length : null,
          total: flows.length,
        },
      })
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderCashflows(entry = '/portfolio/cashflows') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', children: portfolio.routes }], { initialEntries: [entry] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const cell = (r: HTMLElement, col: string) => r.querySelector<HTMLElement>(`[data-col="${col}"]`)!

beforeEach(async () => {
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Portfolio Cash flows', () => {
  it('registers /portfolio/cashflows with a Cash flows nav entry at order 40', () => {
    const nav = Array.isArray(portfolio.nav) ? portfolio.nav : [portfolio.nav]
    expect(nav.find((n) => n?.path === '/portfolio/cashflows')).toMatchObject({
      labelKey: 'portfolio:nav.cashflows',
      section: 'portfolio',
      order: 40,
    })
  })

  it('lists flows with Baghdad dates, signed amounts, states and short tx ids', async () => {
    const fetch = fakeBackend(ALL)
    renderCashflows()
    const table = await screen.findByTestId('cashflows-table')
    const url = String(fetch.mock.calls.find((c) => String(c[0]).includes('cashflows'))![0])
    expect(url).toContain('env=demo')
    expect(url).toContain('per_page=20')
    expect(url).toContain('page=1')

    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Date',
      'Type',
      'Currency',
      'Amount',
      'State',
      'Transaction',
    ])
    const rows = screen.getAllByTestId('cashflow-row')
    expect(rows).toHaveLength(20)
    const [first, second, third] = rows
    // 17:36 UTC is 20:36 in Baghdad.
    expect(cell(first!, 'date')).toHaveTextContent('Oct 25, 2026, 20:36')
    expect(cell(first!, 'kind')).toHaveTextContent('Withdrawal')
    expect(cell(first!, 'currency')).toHaveTextContent('USDT')
    expect(cell(first!, 'amount')).toHaveTextContent('−1,000.00')
    expect(cell(first!, 'state')).toHaveTextContent('Completed')
    expect(cell(first!, 'tx')).toHaveTextContent('0x9f3a…41e0')
    expect(cell(first!, 'tx').querySelector('[title]')).toHaveAttribute('title', ALL[0]!.tx_id)
    expect(cell(second!, 'amount')).toHaveTextContent('+1,001.00')
    expect(cell(second!, 'state')).toHaveTextContent('Pending')
    expect(cell(second!, 'tx')).toHaveTextContent('No transaction')
    expect(cell(third!, 'state')).toHaveTextContent('Failed')
    expect(screen.getByTestId('cashflows-summary')).toHaveTextContent('23 deposits and withdrawals')
  })

  it('has no buttons other than pagination, and pages through the history', async () => {
    const fetch = fakeBackend(ALL)
    const router = renderCashflows()
    await screen.findByTestId('cashflows-table')
    const buttons = screen.getAllByRole('button')
    expect(buttons.map((b) => b.textContent)).toEqual(['Previous', 'Next'])
    expect(buttons.every((b) => b.closest('[data-testid="cashflows-pager"]'))).toBe(true)
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByTestId('cashflows-range')).toHaveTextContent('1–20 of 23')

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await vi.waitFor(() => expect(screen.getByTestId('cashflows-range')).toHaveTextContent('21–23 of 23'))
    expect(screen.getAllByTestId('cashflow-row')).toHaveLength(3)
    expect(router.state.location.search).toBe('?page=2')
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('page=2'))).toBe(true)
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    await vi.waitFor(() => expect(screen.getByTestId('cashflows-range')).toHaveTextContent('1–20 of 23'))
    expect(router.state.location.search).toBe('')
  })

  it('lands on the last page when ?page= is past the end', async () => {
    fakeBackend(ALL)
    const router = renderCashflows('/portfolio/cashflows?page=9')
    await vi.waitFor(() => expect(router.state.location.search).toBe('?page=2'))
    await vi.waitFor(() => expect(screen.getByTestId('cashflows-range')).toHaveTextContent('21–23 of 23'))
  })

  it('shows the empty state when there are no deposits or withdrawals', async () => {
    fakeBackend([])
    renderCashflows()
    expect(await screen.findByTestId('state-none')).toHaveTextContent('No deposits or withdrawals')
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('shows the error state with a retry when the API fails', async () => {
    fakeBackend('fail')
    renderCashflows()
    const alert = await screen.findByTestId('state-error')
    fakeBackend(ALL)
    await userEvent.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByTestId('cashflows-table')).toBeInTheDocument()
  })

  it('renders in Arabic with Western digits', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend(ALL)
    renderCashflows()
    const rows = await screen.findAllByTestId('cashflow-row')
    expect(cell(rows[0]!, 'kind')).toHaveTextContent('سحب')
    expect(cell(rows[0]!, 'amount')).toHaveTextContent('−1,000.00')
    expect(cell(rows[0]!, 'date')).toHaveTextContent('20:36')
    expect(screen.getByRole('button', { name: 'التالي' })).toBeEnabled()
  })
})

describe('ledger helpers', () => {
  it('maps OKX state codes', () => {
    expect(flowState('2')).toBe('completed')
    expect(flowState('-1')).toBe('failed')
    expect(flowState('-2')).toBe('failed')
    for (const code of ['0', '1', '-3', '7', '10', '12']) expect(flowState(code)).toBe('pending')
  })
  it('shortens long tx ids only', () => {
    expect(shortTx('abc')).toBe('abc')
    expect(shortTx('0123456789abcdef')).toBe('012345…cdef')
  })
})
