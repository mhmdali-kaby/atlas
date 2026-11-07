import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import type { ApiJob, BackfillReport } from './api'
import { DataPage } from './DataPage'

const NOW = new Date().toISOString()

function job(dataset: string, over: Partial<ApiJob> = {}): ApiJob {
  return {
    dataset,
    status: 'done',
    depth_days: 730,
    target_from: '2024-10-29T12:00:00Z',
    cursor_ts: '2024-10-29T12:00:00Z',
    rows_written: 100,
    expected: 100,
    percent: '100.0',
    last_error: null,
    updated_at: NOW,
    ...over,
  }
}

function report(instruments: BackfillReport['instruments']): BackfillReport {
  return {
    env: 'demo',
    as_of: NOW,
    instruments,
    totals: { instruments: instruments.length, jobs: 0, by_status: { pending: 0, running: 0, done: 0, failed: 0, paused: 0 }, rows_written: 0, percent: null },
    quality: { open: 0, open_by_kind: { gap: 0, duplicate: 0, zero_volume: 0, outlier: 0 } },
    quality_issues: [],
  }
}

const RUNNING = report([
  { inst_id: 'BTC-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H', { status: 'running', rows_written: 62, expected: 100 })] },
  { inst_id: 'XAU-USDT-SWAP', asset_class: 'commodity', open_issues: 0, datasets: [job('candles:1H')] },
])
const DONE = report([{ inst_id: 'BTC-USDT-SWAP', asset_class: 'crypto', open_issues: 0, datasets: [job('candles:1H')] }])

function fakeBackend(opts: { backfill: BackfillReport | 500; resync?: number }) {
  const posts: { path: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path.startsWith('/api/data/backfill')) return opts.backfill === 500 ? reply(500, { message: 'Server Error' }) : reply(200, { data: opts.backfill })
      if (path === '/api/data/storage') return reply(200, { data: { hypertables: [{ name: 'candles', bytes: 3_270_000 }], total_bytes: 3_270_000 } })
      if (path === '/api/data/resync' || path === '/api/data/pause') {
        posts.push({ path, body: typeof init.body === 'string' ? JSON.parse(init.body) : null })
        const status = opts.resync ?? 202
        return reply(status, status === 202 ? { ok: true } : { message: 'Too Many Attempts.' })
      }
      return reply(404)
    }),
  )
  return posts
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <div id="content">
        <DataPage />
      </div>
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  // jsdom lays nothing out; give the page scroller a window-sized box so the virtual table renders rows.
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(900)
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(1200)
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Settings → Data', () => {
  it('shows the running summary, every instrument row with its percent, and the storage total', async () => {
    fakeBackend({ backfill: RUNNING })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Backfilling market history' })).toBeInTheDocument()
    const rows = await screen.findAllByTestId('dt-row')
    expect(rows.map((r) => r.dataset.inst)).toEqual(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])
    const cell = within(rows[0]!).getByText('62%, Fetching')
    expect(cell).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('dt-storage-total')).toHaveTextContent('3.27 MB on this Mac'))
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })

  it('Re-sync now posts once and a 429 shows the try-again notice', async () => {
    const posts = fakeBackend({ backfill: DONE, resync: 429 })
    renderPage()
    const button = await screen.findByRole('button', { name: 'Re-sync now' })
    await userEvent.click(button)
    expect(await screen.findByText('Re-sync already requested')).toBeInTheDocument()
    expect(screen.getByText('Atlas takes one re-sync a minute. Try again shortly.')).toBeInTheDocument()
    expect(posts).toEqual([{ path: '/api/data/resync', body: { env: 'demo' } }])
  })

  it('shows the not-started state when there are no jobs', async () => {
    fakeBackend({ backfill: report([]) })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'No market history yet' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start backfill' })).toBeInTheDocument()
  })

  it('shows the error state when the API fails', async () => {
    fakeBackend({ backfill: 500 })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Couldn’t load the data status' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
