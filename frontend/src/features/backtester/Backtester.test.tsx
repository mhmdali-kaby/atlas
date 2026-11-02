import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { resetCsrfForTests } from '../../lib/api'
import { POLL_MS, type RunDetail, type RunStatus } from './api'
import { initialForm, mapServerErrors, serverFieldKey, toRequest, validate, type BacktestRequest } from './model'
import backtester from './routes'
import { RunsSidebar } from './RunsSidebar'

/* ATL-209 against ATL-208's /api/backtests (docs/handoff/ATL-208-backtests-api.md). */
const NOW = Date.parse('2026-11-02T08:15:00Z') // 11:15 Baghdad, 2 Nov
const RUN_ID = '9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10'

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const DEFAULT_BODY: BacktestRequest = {
  rule_spec: {
    name: 'sma_10_30_cross',
    side: 'long',
    indicators: { sma10: { kind: 'sma', window: 10 }, sma30: { kind: 'sma', window: 30 } },
    entry: [{ left: 'sma10', op: 'crosses_above', right: 'sma30' }],
    exit: [{ left: 'sma10', op: 'crosses_below', right: 'sma30' }],
    stop_pct: 3,
    take_profit_pct: null,
  },
  inst_ids: ['BTC-USDT-SWAP'],
  bar: '1H',
  period: { start: '2026-08-04T00:00:00+03:00', end: '2026-11-02T00:00:00+03:00' },
  splits: 3,
  slippage_bps: 2,
}

function runOf(status: RunStatus, extra: Partial<RunDetail> = {}): RunDetail {
  return {
    id: RUN_ID,
    env: 'demo',
    status,
    rule_name: 'sma_10_30_cross',
    side: 'long',
    inst_ids: ['BTC-USDT-SWAP'],
    bar: '1H',
    period: { start: '2026-08-03T21:00:00Z', end: '2026-11-01T21:00:00Z' },
    n_trades: status === 'done' ? 18 : null,
    overfit_warning: status === 'done' ? false : null,
    data_gap: status === 'done' ? false : null,
    error: null,
    error_reason: null,
    created_at: '2026-11-02T08:15:00.000000Z',
    started_at: status === 'queued' ? null : '2026-11-02T08:15:01.000000Z',
    finished_at: status === 'done' || status === 'failed' ? '2026-11-02T08:15:07.000000Z' : null,
    request: { ...DEFAULT_BODY, period: { start: '2026-08-03T21:00:00Z', end: '2026-11-01T21:00:00Z' } },
    result: status === 'done' ? { ok: true } : null,
    ...extra,
  }
}

interface Backend {
  post?: (body: unknown) => Response
  /** Detail responses in order; the last one repeats. */
  detail?: RunDetail[]
  list?: RunDetail[]
  instruments?: string[]
}

function fakeBackend(opts: Backend = {}) {
  const details = opts.detail ?? [runOf('done')]
  let served = 0
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input))
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/backtests' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as unknown
      return opts.post ? opts.post(body) : json(202, { ok: true, id: RUN_ID, status: 'queued', created_at: '2026-11-02T08:15:00.000000Z' })
    }
    if (url.pathname === '/api/backtests') return json(200, { env: 'demo', data: (opts.list ?? []).map(({ request: _r, result: _x, ...row }) => row) })
    if (url.pathname === `/api/backtests/${RUN_ID}`) {
      const run = details[Math.min(served, details.length - 1)]!
      served += 1
      return json(200, run)
    }
    if (url.pathname.startsWith('/api/backtests/')) return json(404, { message: 'Not found' })
    if (url.pathname === '/api/instruments') {
      const q = (url.searchParams.get('q') ?? '').toUpperCase()
      const all = opts.instruments ?? ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP']
      return json(200, { data: all.filter((i) => i.startsWith(q)).map((inst_id) => ({ inst_id, asset_class: 'crypto', blocked: false })) })
    }
    return json(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const posts = (fetch: ReturnType<typeof fakeBackend>) =>
  fetch.mock.calls.filter(([input, init]) => new URL(String(input)).pathname === '/api/backtests' && (init?.method ?? 'GET').toUpperCase() === 'POST')
const detailCalls = (fetch: ReturnType<typeof fakeBackend>) => fetch.mock.calls.filter(([input]) => new URL(String(input)).pathname === `/api/backtests/${RUN_ID}`).length

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname + loc.search}</output>
}

function renderAt(url = '/backtester') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    backtester.routes.map((r) => ({
      path: `/${r.path}`,
      element: (
        <>
          {r.element}
          <RunsSidebar onNavigate={() => {}} />
          <Where />
        </>
      ),
    })),
    { initialEntries: [url] },
  )
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const where = () => screen.getByTestId('where').textContent ?? ''

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  resetCsrfForTests()
  // the segmented controls (Radix) measure themselves
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  await i18n.changeLanguage('en')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the form model', () => {
  it('builds the ATL-208 body from the default form, with Baghdad-midnight period bounds', () => {
    expect(toRequest(initialForm('2026-11-02'))).toEqual(DEFAULT_BODY)
  })

  it('mirrors the API checks', () => {
    const f = initialForm('2026-11-02')
    expect(validate(f)).toEqual({})
    expect(Object.keys(validate({ ...f, insts: [] }))).toEqual(['insts'])
    expect(Object.keys(validate({ ...f, from: '2026-11-03' }))).toEqual(['period'])
    expect(validate({ ...f, indicators: [{ name: 'close', kind: 'sma', window: '10' }, f.indicators[1]!] })['ind.0']?.key).toBe('indClose')
    expect(validate({ ...f, indicators: [{ ...f.indicators[0]!, window: '5001' }, f.indicators[1]!] })['ind.0']?.key).toBe('window')
    expect(validate({ ...f, entry: [{ left: 'sma99', op: 'above', right: 'sma30', num: false }] })['entry.0']?.key).toBe('unknown')
    expect(validate({ ...f, exit: [{ left: 'close', op: 'above', right: 'abc', num: true }] })['exit.0']?.key).toBe('number')
    expect(validate({ ...f, entry: [] }).entry?.key).toBe('entryNone')
    expect(validate({ ...f, stop: '100' }).stop?.key).toBe('stop')
    expect(validate({ ...f, tp: '0' }).tp?.key).toBe('tp')
    expect(validate({ ...f, name: ' ' }).name?.key).toBe('nameRequired')
  })

  it('maps every 422 path onto its field, and keeps the rest for the banner', () => {
    const f = initialForm('2026-11-02')
    expect(serverFieldKey('rule_spec.entry.0.right', f)).toBe('entry.0')
    expect(serverFieldKey('rule_spec.exit.0.op', f)).toBe('exit.0')
    expect(serverFieldKey('rule_spec.indicators.sma30.window', f)).toBe('ind.1')
    expect(serverFieldKey('rule_spec.indicators', f)).toBe('indicators')
    expect(serverFieldKey('rule_spec.stop_pct', f)).toBe('stop')
    expect(serverFieldKey('inst_ids.0', f)).toBe('insts')
    expect(serverFieldKey('period.end', f)).toBe('period')
    expect(serverFieldKey('slippage_bps', f)).toBe('slip')
    expect(serverFieldKey('surprise', f)).toBeNull()
    expect(mapServerErrors({ 'period.start': ['a'], 'period.end': ['b'], surprise: ['c'] }, f)).toEqual({ fields: { period: 'a' }, other: ['c'] })
  })
})

describe('Backtester form', () => {
  it('posts the RuleSpec body ATL-208 expects for what the owner set', async () => {
    const fetch = fakeBackend({ detail: [runOf('queued')] })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt()
    await screen.findByTestId('bt-form')

    // add XAU from the search, go short on 4H bars, make the exit a number, set a take-profit
    await user.type(screen.getByRole('combobox', { name: 'Add instrument' }), 'XAU')
    await user.click(await screen.findByRole('option', { name: 'XAU-USDT-SWAP' }))
    await user.click(screen.getByRole('radio', { name: 'Short' }))
    await user.click(screen.getByRole('radio', { name: '4H' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Right side, exit condition 1' }), '__num')
    const level = screen.getByRole('textbox', { name: 'Right side, exit condition 1' })
    await user.clear(level)
    await user.type(level, '70')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Left side, exit condition 1' }), 'close')
    await user.type(screen.getByRole('textbox', { name: 'Take-profit' }), '6')
    await user.click(screen.getByRole('button', { name: 'Increase Walk-forward splits' }))
    await user.click(screen.getByRole('button', { name: 'Add indicator' }))

    expect(screen.getByTestId('bt-sentence')).toHaveTextContent(
      'Go short on BTC and XAU, 4H bars, 4 Aug to 2 Nov 2026. Enter when SMA 10 crosses above SMA 30. Exit when Close price crosses below 70, or at a 3% stop, or at a 6% take-profit. Test it in 4 walk-forward splits with 2 bps slippage.',
    )
    await user.click(screen.getByTestId('bt-run'))

    await waitFor(() => expect(posts(fetch)).toHaveLength(1))
    const body = JSON.parse(String(posts(fetch)[0]![1]!.body)) as unknown
    expect(body).toEqual({
      ...DEFAULT_BODY,
      rule_spec: {
        ...DEFAULT_BODY.rule_spec,
        side: 'short',
        indicators: { ...DEFAULT_BODY.rule_spec.indicators, ema40: { kind: 'ema', window: 40 } },
        exit: [{ left: 'close', op: 'crosses_below', right: 70 }],
        take_profit_pct: 6,
      },
      inst_ids: ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'],
      bar: '4H',
      splits: 4,
    })
    await waitFor(() => expect(where()).toBe(`/backtester/run?id=${RUN_ID}`))
  })

  it('does not post while the form is invalid, and says why beside the field', async () => {
    const fetch = fakeBackend()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt()
    await screen.findByTestId('bt-form')
    await user.click(screen.getByRole('button', { name: 'Remove BTC-USDT-SWAP' }))
    expect(screen.getByTestId('bt-err-insts')).toHaveTextContent('Choose 1 to 5 instruments.')
    expect(screen.getByTestId('bt-run')).toBeDisabled()
    expect(posts(fetch)).toHaveLength(0)
  })

  it('shows a 422 reason beside the field its path names, and focuses it', async () => {
    fakeBackend({
      post: () =>
        json(422, {
          message: 'The rule has a problem.',
          errors: {
            'rule_spec.entry.0.right': ['rule_spec: condition uses unknown indicator ‘sma99’'],
            'period.end': ['The period must end after it starts.'],
            'rule_spec.indicators.sma30.window': ['The window may not be greater than 5000.'],
          },
        }),
    })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt()
    await screen.findByTestId('bt-form')
    await user.click(screen.getByTestId('bt-run'))

    const banner = await screen.findByTestId('bt-banner')
    expect(banner).toHaveTextContent('The rule has a problem')
    expect(banner).toHaveTextContent('Fix the field marked below, then run it again.')

    const row = screen.getByTestId('bt-entry-0')
    expect(within(row).getByTestId('bt-err-entry-0')).toHaveTextContent('rule_spec: condition uses unknown indicator ‘sma99’')
    const right = within(row).getByRole('combobox', { name: 'Right side, entry condition 1' })
    expect(right).toHaveAttribute('aria-invalid', 'true')
    expect(right).toHaveAttribute('aria-describedby', 'bt-err-entry-0')
    expect(screen.getByTestId('bt-period-note')).toHaveTextContent('The period must end after it starts.')
    expect(screen.getByLabelText('From')).toHaveAttribute('aria-invalid', 'true')
    expect(within(screen.getByTestId('bt-ind-1')).getByTestId('bt-err-ind-1')).toHaveTextContent('The window may not be greater than 5000.')
    // focus lands on the first marked control
    await waitFor(() => expect(document.activeElement).toHaveAttribute('aria-invalid', 'true'))
    expect(where()).toBe('/backtester')

    // editing the row clears its server reason, and only its
    await user.selectOptions(right, 'close')
    expect(screen.queryByTestId('bt-err-entry-0')).toBeNull()
    expect(screen.getByTestId('bt-period-note')).toHaveTextContent('The period must end after it starts.')
  })

  it('says so when the API throttles a 7th run in a minute', async () => {
    fakeBackend({ post: () => json(429, { message: 'Too Many Attempts.' }) })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderAt()
    await screen.findByTestId('bt-form')
    await user.click(screen.getByTestId('bt-run'))
    expect(await screen.findByTestId('bt-banner')).toHaveTextContent('Too many runs started in a minute')
  })
})

describe('a run', () => {
  it('polls until done, showing the running state meanwhile, then stops polling', async () => {
    const fetch = fakeBackend({ detail: [runOf('queued'), runOf('running'), runOf('running'), runOf('done')] })
    renderAt(`/backtester/run?id=${RUN_ID}`)

    const running = await screen.findByTestId('bt-running')
    expect(running).toHaveTextContent('Running on 1 instrument with 1H bars')
    expect(screen.getByTestId('bt-status')).toHaveAttribute('data-status', 'queued')
    expect(detailCalls(fetch)).toBe(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS)
    })
    await waitFor(() => expect(screen.getByTestId('bt-status')).toHaveAttribute('data-status', 'running'))
    expect(screen.getByTestId('bt-running')).toBeInTheDocument()
    expect(detailCalls(fetch)).toBe(2)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2)
    })
    await waitFor(() => expect(screen.getByTestId('bt-status')).toHaveAttribute('data-status', 'done'))
    expect(screen.queryByTestId('bt-running')).toBeNull()
    expect(screen.getByRole('link', { name: 'Edit and run again' })).toHaveAttribute('href', `/backtester?from=${RUN_ID}`)
    const calls = detailCalls(fetch)
    expect(calls).toBe(4)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 5)
    })
    expect(detailCalls(fetch)).toBe(calls)
  })

  it('stops polling at failed and shows the error with its reason', async () => {
    const fetch = fakeBackend({
      detail: [
        runOf('running'),
        runOf('failed', { error: 'too_many_bars', error_reason: 'the period is 73440 1m bars per instrument; backtest.max_bars is 50000' }),
      ],
    })
    renderAt(`/backtester/run?id=${RUN_ID}`)
    await screen.findByTestId('bt-running')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS)
    })
    const failed = await screen.findByTestId('bt-failed')
    expect(failed).toHaveTextContent('The period is too long for this bar size')
    expect(failed).toHaveTextContent('the period is 73440 1m bars per instrument; backtest.max_bars is 50000')
    expect(failed).toHaveTextContent('Use a larger bar or a shorter period.')
    const calls = detailCalls(fetch)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    })
    expect(detailCalls(fetch)).toBe(calls)
  })

  it('"Edit and run again" fills the form with that run’s rule', async () => {
    fakeBackend({ detail: [runOf('failed', { error: 'no_data', error_reason: 'no candles' })] })
    renderAt(`/backtester?from=${RUN_ID}`)
    await screen.findByTestId('bt-form')
    expect(screen.getByLabelText('From')).toHaveValue('2026-08-04')
    expect(screen.getByLabelText('To')).toHaveValue('2026-11-02')
    expect(screen.getAllByTestId('bt-inst-chip').map((c) => c.textContent)).toEqual(['BTC-USDT-SWAP'])
  })

  it('lists recent runs in the sidebar with their status', async () => {
    fakeBackend({ list: [runOf('running'), { ...runOf('failed'), id: 'b2', rule_name: 'rsi_14_reversal', inst_ids: ['ETH-USDT-SWAP'] }] })
    renderAt()
    const rows = await screen.findAllByTestId('bt-run-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('sma_10_30_cross')
    expect(rows[0]).toHaveTextContent('BTC Running')
    expect(rows[1]).toHaveTextContent('ETH Failed')
    expect(rows[1]).toHaveAttribute('href', '/backtester/run?id=b2')
  })
})
