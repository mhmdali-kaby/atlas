import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import type { Connection, ConnectionTestResponse } from '../onboarding'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const ID = 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30'
const NEW_KEY = 'aaaa1111-2222-3333-4444-55556666zz99'

function verified(over: Partial<Connection> = {}): Connection {
  return {
    id: ID,
    exchange: 'okx',
    env: 'demo',
    label: 'OKX demo',
    api_key_last4: 'ab12',
    has_secret: true,
    has_passphrase: true,
    permissions: ['read_only', 'trade'],
    account_mode: 'futures',
    position_mode: 'net_mode',
    uid_last4: '6123',
    // 08:00 UTC = 11:00 in Baghdad (UTC+3)
    last_verified_at: '2026-10-28T08:00:00.000000Z',
    last_test_error: null,
    created_at: '2026-10-28T07:00:00.000000Z',
    updated_at: '2026-10-28T08:00:00.000000Z',
    ...over,
  }
}

type Mode = 'ok' | 'wrong' | 'down'

function fakeBackend(opts: { stored?: Connection | null; mode?: Mode } = {}) {
  const state = {
    stored: opts.stored === undefined ? verified() : opts.stored,
    mode: opts.mode ?? ('ok' as Mode),
    requests: [] as { method: string; path: string }[],
    responses: [] as string[],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const body = typeof init.body === 'string' ? (JSON.parse(init.body) as Record<string, string>) : null
      state.requests.push({ method, path })
      const reply = (status: number, json?: unknown) => {
        const text = json === undefined ? '' : JSON.stringify(json)
        state.responses.push(text)
        return new Response(json === undefined ? null : text, { status, headers: { 'Content-Type': 'application/json' } })
      }
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections' && method === 'GET') return reply(200, { data: state.stored ? [state.stored] : [] })
      if (path === '/api/connections' && method === 'POST') {
        state.stored = verified({ label: body?.label, api_key_last4: (body?.api_key ?? '').slice(-4), permissions: null, last_verified_at: null, uid_last4: null })
        return reply(201, { data: state.stored })
      }
      if (path === `/api/connections/${ID}` && method === 'PATCH') {
        state.stored = verified({ ...state.stored, label: body?.label, api_key_last4: (body?.api_key ?? '').slice(-4), last_verified_at: null, permissions: null })
        return reply(200, { data: state.stored })
      }
      if (path === `/api/connections/${ID}` && method === 'DELETE') {
        state.stored = null
        return reply(204)
      }
      if (path === `/api/connections/${ID}/test` && method === 'POST') {
        if (state.mode === 'down') return reply(503, { ok: false, error: 'trading_core_unavailable', message: 'down' })
        const base = { acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123', message: null }
        let res: ConnectionTestResponse
        if (state.mode === 'wrong') {
          state.stored = verified({ ...state.stored, last_verified_at: null, last_test_error: 'invalid_credentials' })
          res = { ...base, ok: false, permissions: [], warnings: [], error: 'invalid_credentials', okx_code: '50113', verified_at: null, connection: state.stored }
        } else {
          state.stored = verified({ ...state.stored, permissions: ['read_only', 'trade'], last_verified_at: '2026-10-28T14:30:00.000000Z', last_test_error: null })
          res = { ...base, ok: true, permissions: ['read_only', 'trade'], warnings: [], error: null, okx_code: null, verified_at: state.stored.last_verified_at, connection: state.stored }
        }
        return reply(200, res)
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/connections'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const accountRows = async () => within(await screen.findByRole('region', { name: 'Account' }))
const toastTitles = () => useToasts.getState().toasts.map((t) => t.title)

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('Settings → Connections', () => {
  it('shows the verified demo connection with only the key tail, modes, permissions, UID tail and Baghdad time', async () => {
    const state = fakeBackend()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Connections' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Connections' })).toHaveAttribute('aria-current', 'page')
    const rows = await accountRows()
    expect(await rows.findByTestId('key-tail')).toHaveTextContent('•••• ab12')
    expect(rows.getByText('Account mode')).toBeInTheDocument()
    expect(rows.getByText('Position mode')).toBeInTheDocument()
    expect(rows.getByText('Read')).toBeInTheDocument()
    expect(rows.getByText('Trade')).toBeInTheDocument()
    expect(rows.getByText('Withdraw off')).toBeInTheDocument()
    expect(rows.getByTestId('uid-tail')).toHaveTextContent('•••• 6123')
    // 08:00 UTC is 11:00 in Asia/Baghdad
    expect(rows.getByTestId('last-verified')).toHaveTextContent('Oct 28, 2026, 11:00')
    expect(screen.getByTestId('conn-status')).toHaveAttribute('data-state', 'ok')
    expect(screen.queryByTestId('withdraw-warning')).toBeNull()
    expect(state.responses.join('')).not.toMatch(/"(api_secret|passphrase|api_key)"\s*:/)
  })

  it('Test again updates Last verified on success', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByTestId('last-verified')).toHaveTextContent('11:00')
    await user.click(screen.getByRole('button', { name: 'Test again' }))
    // 14:30 UTC → 17:30 Baghdad
    await waitFor(() => expect(screen.getByTestId('last-verified')).toHaveTextContent('Oct 28, 2026, 17:30'))
    expect(screen.getByTestId('conn-status')).toHaveAttribute('data-state', 'ok')
    expect(toastTitles()).toContain('Connection works')
  })

  it('Test again shows the mapped error when OKX refuses the key', async () => {
    fakeBackend({ mode: 'wrong' })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Test again' }))
    const alert = await screen.findByTestId('test-error')
    expect(alert).toHaveTextContent('Key or passphrase is wrong')
    expect(screen.getByTestId('conn-status')).toHaveAttribute('data-state', 'failed')
    expect(screen.getByTestId('last-verified')).toHaveTextContent('Never')
  })

  it('Test again says Atlas could not run the test when trading_core is down, keeping Last verified', async () => {
    fakeBackend({ mode: 'down' })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Test again' }))
    expect(await screen.findByTestId('test-error')).toHaveTextContent('Atlas’s trading service isn’t running')
    expect(screen.getByTestId('conn-status')).toHaveTextContent(/Atlas couldn’t run the test\. Last verified/)
    expect(screen.getByTestId('last-verified')).toHaveTextContent('11:00')
  })

  it('Replace keys saves new values in a sheet and the masked tail changes', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Replace keys' }))
    const sheet = await screen.findByRole('dialog', { name: 'Replace the OKX demo keys' })
    await user.type(within(sheet).getByLabelText('API key'), NEW_KEY)
    await user.type(within(sheet).getByLabelText('Secret key'), 'NEW-SECRET-1')
    await user.type(within(sheet).getByLabelText('Passphrase'), 'New-pass-1')
    await user.click(within(sheet).getByRole('button', { name: 'Save and test' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByTestId('key-tail')).toHaveTextContent('•••• zz99')
    expect(screen.getByTestId('last-verified')).toHaveTextContent('17:30')
    expect(state.requests.some((r) => r.method === 'PATCH')).toBe(true)
    expect(toastTitles()).toContain('Keys replaced')
  })

  it('Replace keys keeps the sheet open with the mapped error when the new key fails', async () => {
    fakeBackend({ mode: 'wrong' })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Replace keys' }))
    const sheet = await screen.findByRole('dialog', { name: 'Replace the OKX demo keys' })
    await user.type(within(sheet).getByLabelText('API key'), NEW_KEY)
    await user.type(within(sheet).getByLabelText('Secret key'), 'NEW-SECRET-1')
    await user.type(within(sheet).getByLabelText('Passphrase'), 'Wrong-pass')
    await user.click(within(sheet).getByRole('button', { name: 'Save and test' }))
    expect(await within(sheet).findByTestId('test-error')).toHaveTextContent('Key or passphrase is wrong')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('Remove asks for the typed label, deletes the connection and shows Not connected', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Remove…' }))
    const sheet = await screen.findByRole('dialog', { name: 'Remove the OKX demo connection?' })
    const confirm = within(sheet).getByRole('button', { name: 'Remove connection' })
    expect(confirm).toBeDisabled()
    const input = within(sheet).getByLabelText('Type “OKX demo” to confirm')
    await user.type(input, 'OKX dem')
    expect(confirm).toBeDisabled()
    await user.type(input, 'o{Enter}')
    expect(await screen.findByTestId('not-connected')).toHaveTextContent('Not connected')
    expect(state.requests.some((r) => r.method === 'DELETE' && r.path === `/api/connections/${ID}`)).toBe(true)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect OKX demo' })).toHaveFocus())
  })

  it('shows the Live row disabled with the Phase 4 note', async () => {
    fakeBackend()
    renderPage()
    const live = await screen.findByTestId('live-row')
    expect(live).toHaveAttribute('aria-disabled', 'true')
    expect(live).toHaveTextContent('Live trading is enabled in Phase 4.')
    expect(within(live).getByRole('switch', { name: 'OKX live' })).toBeDisabled()
  })

  it('keeps the orange withdraw notice for a demo key with Withdraw', async () => {
    fakeBackend({ stored: verified({ permissions: ['read_only', 'trade', 'withdraw'] }) })
    renderPage()
    expect(await screen.findByTestId('withdraw-warning')).toHaveTextContent('This key can withdraw funds')
  })

  it('renders in Arabic with the RTL strings', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'الاتصالات' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'إعادة الاختبار' })).toBeInTheDocument()
    expect(screen.getByTestId('key-tail')).toHaveTextContent('•••• ab12')
    const verifiedAt = screen.getByTestId('last-verified')
    expect(verifiedAt.textContent).toMatch(/^28 .*2026، 11:00$/)
    // A date is read in the Arabic order, so it must not be the LTR-isolated .fig used for figures.
    expect(verifiedAt).not.toHaveClass('fig')
    expect(document.documentElement.dir).toBe('rtl')
  })
})
