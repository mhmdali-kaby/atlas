import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import type { SourcesReport } from './model'
import { SourcesPage } from './SourcesPage'
import { mixed } from './testFixtures'

function fakeBackend(opts: { list: SourcesReport | 500; patch?: number }) {
  const patches: { path: string; body: unknown }[] = []
  let release: () => void = () => {}
  const held = new Promise<void>((r) => (release = r))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/data-sources' && (init.method ?? 'GET') === 'GET')
        return opts.list === 500 ? reply(500, { message: 'Server Error' }) : reply(200, opts.list)
      if (path.startsWith('/api/data-sources/') && init.method === 'PATCH') {
        patches.push({ path, body: typeof init.body === 'string' ? JSON.parse(init.body) : null })
        await held
        const status = opts.patch ?? 200
        return reply(status, status === 200 ? { data: {} } : { message: 'Server Error' })
      }
      return reply(404)
    }),
  )
  return { patches, release: () => release() }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SourcesPage />
    </QueryClientProvider>,
  )
}

const row = (key: string) => screen.getAllByTestId('src-row').find((r) => r.dataset.key === key)!

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  await i18n.changeLanguage('en')
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Settings → Data sources', () => {
  it('groups the sources by kind and words each status', async () => {
    fakeBackend({ list: mixed(Date.now()) })
    renderPage()
    await screen.findAllByTestId('src-row')
    expect(screen.getAllByTestId('src-group').map((g) => g.dataset.cat)).toEqual([
      'exchange',
      'crypto_news',
      'markets_news',
      'calendar',
      'sentiment',
    ])
    expect(row('rss.coindesk').dataset.status).toBe('working')
    expect(within(row('rss.coindesk')).getByTestId('src-status')).toHaveTextContent('Working')
    expect(within(row('rss.theblock')).getByTestId('src-status')).toHaveTextContent(/^Retrying at \d\d:\d\d$/)
    expect(within(row('cryptopanic')).getByTestId('src-status')).toHaveTextContent('Needs a key')
    expect(within(row('rss.cnbc')).getByTestId('src-status')).toHaveTextContent('Off')
    expect(within(row('forexfactory.calendar')).getByTestId('src-status')).toHaveTextContent('Standing by')
    expect(within(row('rss.theblock')).getByTestId('src-error')).toHaveTextContent('HTTP 503 Service Unavailable from www.theblock.co')
    // A missing key is a visible notice, and the switch is off and disabled (brief §7.2).
    expect(screen.getByTestId('src-sum-keys')).toHaveTextContent('CryptoPanic needs a free key.')
    const locked = within(row('cryptopanic')).getByRole('switch')
    expect(locked).toBeDisabled()
    expect(locked).toHaveAccessibleName('Add a key to turn on CryptoPanic')
    expect(screen.getByTestId('src-summary').dataset.state).toBe('trouble')
  })

  it('flips the switch at once, sends PATCH {enabled:false}, and rolls back with a toast on a 500', async () => {
    const be = fakeBackend({ list: mixed(Date.now()), patch: 500 })
    renderPage()
    await screen.findAllByTestId('src-row')
    const sw = within(row('rss.coindesk')).getByRole('switch', { name: 'Use CoinDesk' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    await userEvent.click(sw)
    await waitFor(() => expect(row('rss.coindesk').dataset.status).toBe('off'))
    expect(within(row('rss.coindesk')).getByRole('switch')).toHaveAttribute('aria-checked', 'false')
    await waitFor(() => expect(be.patches).toEqual([{ path: '/api/data-sources/rss.coindesk', body: { enabled: false } }]))
    be.release()
    await waitFor(() => expect(row('rss.coindesk').dataset.status).toBe('working'))
    expect(within(row('rss.coindesk')).getByRole('switch')).toHaveAttribute('aria-checked', 'true')
    expect(useToasts.getState().toasts).toMatchObject([{ tone: 'error', title: 'Couldn’t turn off CoinDesk' }])
  })

  it('a source turned on shows Starting until its next check', async () => {
    const be = fakeBackend({ list: mixed(Date.now()) })
    renderPage()
    await screen.findAllByTestId('src-row')
    await userEvent.click(within(row('rss.cnbc')).getByRole('switch'))
    await waitFor(() => expect(row('rss.cnbc').dataset.status).toBe('starting'))
    expect(be.patches[0]!.body).toEqual({ enabled: true })
    be.release()
  })

  it('names a kind that has no working source in an alert', async () => {
    const now = Date.now()
    const list = mixed(now)
    list.summary.crypto_news = { total: 3, available: 0, all_down: true }
    fakeBackend({ list })
    renderPage()
    const alert = await screen.findByTestId('src-down')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(alert).toHaveTextContent('No crypto news is coming in')
  })

  it('shows the error and empty states', async () => {
    fakeBackend({ list: 500 })
    const { unmount } = renderPage()
    expect(await screen.findByRole('heading', { name: 'Couldn’t load the data sources' })).toBeInTheDocument()
    unmount()
    fakeBackend({ list: { data: [], summary: {} } })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'No data sources yet' })).toBeInTheDocument()
  })
})
