import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { keyProviders, keyStartedBy, withKey, type ApiSource } from './model'
import { SourcesPage } from './SourcesPage'
import { mixed } from './testFixtures'

/* ATL-69: the provider-wide key model, and the key sheet's no-cache rule. */

const NOW = Date.parse('2026-10-28T11:32:00Z')
const SECRET = 'unit-secret-key-9876'

describe('key model', () => {
  it('lists each keyed provider once with its last four', () => {
    const ps = keyProviders(mixed(NOW).data)
    expect(ps.map((p) => [p.provider, p.hasKey, p.last4])).toEqual([
      ['cryptopanic', false, null],
      ['finnhub', true, '7Q2F'],
    ])
  })

  it('a save keys every source of the provider and clears its failure columns', () => {
    const base = mixed(NOW)
    const failing = {
      ...base,
      data: base.data.map((s) => (s.key === 'cryptopanic' ? { ...s, consecutive_failures: 3, disabled_until: new Date(NOW + 60_000).toISOString(), last_error: 'HTTP 401' } : s)),
    }
    const next = withKey(failing, 'cryptopanic', 'AB12', NOW)
    const cp = next.data.find((s) => s.key === 'cryptopanic')!
    expect(cp).toMatchObject({ has_key: true, key_last4: 'AB12', consecutive_failures: 0, disabled_until: null, last_error: null })
    expect(cp.status).not.toBe('needs_key')
    expect(keyStartedBy(failing, 'cryptopanic', true)).toEqual(['cryptopanic'])
  })

  it('a removal returns the provider to needs_key and releases what it covered', () => {
    const base = mixed(NOW)
    expect(keyStartedBy(base, 'finnhub', false)).toEqual(['forexfactory.calendar'])
    const next = withKey(base, 'finnhub', null, NOW)
    expect(next.data.find((s) => s.key === 'finnhub.calendar')).toMatchObject({ has_key: false, key_last4: null, status: 'needs_key' })
    expect(next.data.find((s) => s.key === 'forexfactory.calendar')!.covered_by).toBeNull()
    expect(next.summary.calendar?.available).toBe(1)
  })
})

describe('key sheet', () => {
  let list: ApiSource[]
  const puts: unknown[] = []

  beforeEach(async () => {
    resetCsrfForTests()
    useToasts.setState({ toasts: [] })
    await i18n.changeLanguage('en')
    list = mixed(Date.now()).data
    puts.length = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
        const path = String(input).replace(API_URL, '')
        const reply = (status: number, json?: unknown) =>
          new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
        if (path === '/sanctum/csrf-cookie') return reply(204)
        if (path === '/api/data-sources') return reply(200, { data: list, summary: {} })
        if (path === '/api/data-sources/cryptopanic/key' && init.method === 'PUT') {
          puts.push(JSON.parse(String(init.body)))
          list = list.map((s) => (s.key === 'cryptopanic' ? { ...s, has_key: true, key_last4: '9876', status: 'never_run' } : s))
          return reply(200, { data: list.find((s) => s.key === 'cryptopanic') })
        }
        return reply(404)
      }),
    )
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('PUTs the typed key without it reaching the query or mutation caches, then shows the last four', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <SourcesPage />
      </QueryClientProvider>,
    )
    const row = await screen.findByText('CryptoPanic', { selector: '.src-row[data-key="cryptopanic"] bdi' })
    await user.click(within(row.closest<HTMLElement>('.src-row')!).getByTestId('src-add-key'))
    const dialog = await screen.findByRole('dialog')
    const save = within(dialog).getByTestId('src-ks-save')
    expect(save).toBeDisabled()
    await user.type(within(dialog).getByLabelText('API key'), SECRET)
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(puts).toEqual([{ api_key: SECRET }]))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await screen.findByLabelText('Key ending 9876')
    const caches = JSON.stringify([
      client.getQueryCache().getAll().map((q) => q.state.data),
      client.getMutationCache().getAll().map((m) => m.state.variables),
    ])
    expect(caches).not.toContain(SECRET)
    expect(document.body.innerHTML).not.toContain(SECRET)
    expect(JSON.stringify({ ...localStorage })).not.toContain(SECRET)
  })
})
