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
const LIVE_ID = 'b1d8d4b3-7a5f-4a1c-8b80-1e7c9d2f3a41'
const LIVE_KEY = 'bbbb1111-2222-3333-4444-55556666lv42'

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
    withdraw_off_verified_at: '2026-10-28T08:00:00.000000Z',
    last_test_error: null,
    created_at: '2026-10-28T07:00:00.000000Z',
    updated_at: '2026-10-28T08:00:00.000000Z',
    ...over,
  }
}

/** A live row as ATL-235 returns it: verified means last_verified_at and withdraw_off_verified_at. */
function liveConn(over: Partial<Connection> = {}): Connection {
  return verified({
    id: LIVE_ID,
    env: 'live',
    label: 'OKX live',
    api_key_last4: 'lv42',
    uid_last4: '9876',
    last_verified_at: '2026-11-02T07:10:00.000000Z',
    withdraw_off_verified_at: '2026-11-02T07:10:00.000000Z',
    ...over,
  })
}

type Mode = 'ok' | 'wrong' | 'down'
type LiveMode = 'ok' | 'withdraw' | 'wrong'

function fakeBackend(
  opts: { stored?: Connection | null; mode?: Mode; live?: Connection | null; liveMode?: LiveMode; activeEnv?: 'demo' | 'live' } = {},
) {
  const state = {
    stored: opts.stored === undefined ? verified() : opts.stored,
    live: opts.live ?? null,
    mode: opts.mode ?? ('ok' as Mode),
    liveMode: opts.liveMode ?? ('ok' as LiveMode),
    requests: [] as { method: string; path: string; body: Record<string, string> | null }[],
    responses: [] as string[],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const body = typeof init.body === 'string' ? (JSON.parse(init.body) as Record<string, string>) : null
      state.requests.push({ method, path, body })
      const reply = (status: number, json?: unknown) => {
        const text = json === undefined ? '' : JSON.stringify(json)
        state.responses.push(text)
        return new Response(json === undefined ? null : text, { status, headers: { 'Content-Type': 'application/json' } })
      }
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/environment' && opts.activeEnv) {
        return reply(200, {
          data: {
            active_env: opts.activeEnv,
            live_started_at: null,
            first_month_ends_at: null,
            in_first_live_month: false,
            switched_at: null,
            confirmation_text: 'TRADE LIVE',
            preflight: { ok: false, items: [] },
          },
        })
      }
      if (path === '/api/connections' && method === 'GET') {
        return reply(200, { data: [state.stored, state.live].filter((c): c is Connection => c !== null) })
      }
      if (path === '/api/connections' && method === 'POST' && body?.env === 'live') {
        state.live = liveConn({
          label: body.label,
          api_key_last4: (body.api_key ?? '').slice(-4),
          permissions: null,
          account_mode: null,
          position_mode: null,
          uid_last4: null,
          last_verified_at: null,
          withdraw_off_verified_at: null,
        })
        return reply(201, { data: state.live })
      }
      if (path === `/api/connections/${LIVE_ID}` && method === 'PATCH') {
        state.live = liveConn({ ...state.live, label: body?.label, api_key_last4: (body?.api_key ?? '').slice(-4), last_verified_at: null, withdraw_off_verified_at: null })
        return reply(200, { data: state.live })
      }
      if (path === `/api/connections/${LIVE_ID}` && method === 'DELETE') {
        state.live = null
        return reply(204)
      }
      if (path === `/api/connections/${LIVE_ID}/test` && method === 'POST') {
        const base = { acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', uid_last4: '9876', okx_code: null, message: null, warnings: [] }
        let res: ConnectionTestResponse
        if (state.liveMode === 'withdraw') {
          // ATL-235: a live key with withdraw is refused, OKX's account/config is still recorded.
          state.live = liveConn({ ...state.live, permissions: ['read_only', 'trade', 'withdraw'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '9876', last_verified_at: null, withdraw_off_verified_at: null, last_test_error: 'withdraw_permission_enabled' })
          res = { ...base, ok: false, permissions: ['read_only', 'trade', 'withdraw'], error: 'withdraw_permission_enabled', verified_at: null, message: 'This key can withdraw funds. Create a key with Read + Trade only.', connection: state.live }
        } else if (state.liveMode === 'wrong') {
          state.live = liveConn({ ...state.live, last_verified_at: null, withdraw_off_verified_at: null, last_test_error: 'invalid_credentials' })
          res = { ...base, ok: false, permissions: [], error: 'invalid_credentials', okx_code: '50113', verified_at: null, connection: state.live }
        } else {
          const at = '2026-11-02T09:30:00.000000Z'
          state.live = liveConn({ ...state.live, permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '9876', last_verified_at: at, withdraw_off_verified_at: at, last_test_error: null })
          res = { ...base, ok: true, permissions: ['read_only', 'trade'], error: null, verified_at: at, connection: state.live }
        }
        return reply(200, res)
      }
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

function renderPageWithUnmount() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/connections'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
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

  it('shows the Live account empty state under Demo, and no Trading now badge with only one account', async () => {
    fakeBackend()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    expect(within(live).getByTestId('live-empty')).toHaveTextContent('No live account connected')
    expect(within(live).getByTestId('live-empty')).toHaveTextContent('Adding it doesn’t switch Atlas to live')
    expect(within(live).getByRole('button', { name: 'Connect OKX live' })).toBeEnabled()
    expect(screen.getByRole('region', { name: 'Demo account' })).toContainElement(screen.getByTestId('account-card'))
    expect(screen.queryByTestId('trading-demo')).toBeNull()
    expect(screen.queryByText('Live trading is enabled in Phase 4.')).toBeNull()
  })

  it('connects a live key: POSTs env live, then shows only the last 4 of the key and UID', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Connect OKX live' }))
    const sheet = await screen.findByRole('dialog', { name: 'Connect the OKX live account' })
    expect(within(sheet).getByText('Create a live key with Read and Trade only')).toBeInTheDocument()
    expect(within(sheet).getByLabelText('Label')).toHaveValue('OKX live')
    await user.type(within(sheet).getByLabelText('API key'), LIVE_KEY)
    await user.type(within(sheet).getByLabelText('Secret key'), 'LIVE-SECRET-1')
    await user.type(within(sheet).getByLabelText('Passphrase'), 'Live-pass-1')
    await user.click(within(sheet).getByRole('button', { name: 'Save and test' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const post = state.requests.find((r) => r.method === 'POST' && r.path === '/api/connections')
    expect(post?.body?.env).toBe('live')
    const live = screen.getByRole('region', { name: 'Live account' })
    expect(within(live).getByTestId('live-key-tail')).toHaveTextContent('•••• lv42')
    expect(within(live).getByTestId('uid-tail')).toHaveTextContent('•••• 9876')
    expect(live.textContent).not.toContain(LIVE_KEY)
    expect(live.textContent).not.toContain('LIVE-SECRET-1')
    expect(within(live).getByTestId('live-status')).toHaveAttribute('data-state', 'verified')
    expect(toastTitles()).toContain('Live key saved and verified')
    // Two accounts now: the demo one is marked as the one Atlas trades on.
    expect(screen.getByTestId('trading-demo')).toHaveTextContent('Trading now')
    expect(state.responses.join('')).not.toMatch(/"(api_secret|passphrase|api_key)"\s*:/)
  })

  it('a verified live key shows account mode, position mode, permissions, UID last 4 and the Environment link', async () => {
    fakeBackend({ live: liveConn() })
    const router = renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    const card = await within(live).findByTestId('live-card')
    expect(card).toHaveAttribute('data-state', 'verified')
    expect(within(live).getByTestId('live-status')).toHaveTextContent(/^Verified .*\. Withdrawals are off\.$/)
    const rows = within(within(live).getByTestId('live-rows'))
    expect(rows.getByText('Account mode')).toBeInTheDocument()
    expect(rows.getByText('Spot and futures mode')).toBeInTheDocument()
    expect(rows.getByText('One-way mode')).toBeInTheDocument()
    expect(rows.getByText('Position mode')).toBeInTheDocument()
    expect(rows.getByText('Read')).toBeInTheDocument()
    expect(rows.getByText('Trade')).toBeInTheDocument()
    expect(rows.getByText('Withdraw off')).toBeInTheDocument()
    expect(rows.getByTestId('uid-tail')).toHaveTextContent('•••• 9876')
    expect(rows.getByTestId('live-key-tail')).toHaveTextContent('•••• lv42')
    const link = within(live).getByRole('link', { name: /Switch Atlas to live in Settings → Environment/ })
    expect(link).toHaveAttribute('href', '/settings/environment')
    expect(link).toHaveTextContent('Adding this key didn’t switch anything.')
    await userEvent.setup().click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/environment'))
  })

  it('Test again on a passing live key keeps it verified with a live toast', async () => {
    fakeBackend({ live: liveConn() })
    const user = userEvent.setup()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    await user.click(await within(live).findByRole('button', { name: 'Test again' }))
    // 09:30 UTC → 12:30 Baghdad
    await waitFor(() => expect(within(live).getByTestId('live-last-verified')).toHaveTextContent('Nov 2, 2026, 12:30'))
    expect(toastTitles()).toContain('Live connection works')
    expect(within(live).getByRole('link', { name: /Environment/ })).toBeInTheDocument()
  })

  it('a live test refused for withdraw permission shows the red refusal and not verified', async () => {
    fakeBackend({ live: liveConn(), liveMode: 'withdraw' })
    const user = userEvent.setup()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    await user.click(await within(live).findByRole('button', { name: 'Test again' }))
    const refused = await within(live).findByTestId('live-refused')
    expect(refused).toHaveAttribute('role', 'alert')
    expect(refused).toHaveTextContent('This key can withdraw funds')
    expect(refused).toHaveTextContent('Create a key with Read + Trade only')
    // Red (loss), not the demo's orange warning.
    expect(refused.className).toContain('bg-loss-wash')
    expect(screen.queryByTestId('withdraw-warning')).toBeNull()
    expect(within(live).getByTestId('live-status')).toHaveAttribute('data-state', 'refused')
    expect(within(live).getByTestId('live-status')).toHaveTextContent('Not verified. This key can withdraw funds.')
    expect(within(live).getByTestId('live-status').className).toContain('text-loss-ink')
    // Only label, key and permissions: Withdraw is a red chip, no account mode, no Environment link.
    const rows = within(within(live).getByTestId('live-rows'))
    expect(rows.getByText('Withdraw').closest('span[class*="bg-loss-wash"]')).not.toBeNull()
    expect(rows.queryByText('Account mode')).toBeNull()
    expect(rows.queryByText('Withdraw off')).toBeNull()
    expect(within(live).queryByRole('link', { name: /Environment/ })).toBeNull()
    expect(toastTitles()).toContain('Live key refused')
  })

  it('a stored refused live key still reads not verified after a reload', async () => {
    fakeBackend({
      live: liveConn({ permissions: ['read_only', 'trade', 'withdraw'], last_verified_at: null, withdraw_off_verified_at: null, last_test_error: 'withdraw_permission_enabled' }),
    })
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    expect(await within(live).findByTestId('live-refused')).toHaveTextContent('This key can withdraw funds')
    expect(within(live).getByTestId('live-card')).toHaveAttribute('data-state', 'refused')
  })

  it('a live key refused in the connect sheet keeps the sheet open with the red live text', async () => {
    fakeBackend({ liveMode: 'withdraw' })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Connect OKX live' }))
    const sheet = await screen.findByRole('dialog', { name: 'Connect the OKX live account' })
    await user.type(within(sheet).getByLabelText('API key'), LIVE_KEY)
    await user.type(within(sheet).getByLabelText('Secret key'), 'LIVE-SECRET-1')
    await user.type(within(sheet).getByLabelText('Passphrase'), 'Live-pass-1')
    await user.click(within(sheet).getByRole('button', { name: 'Save and test' }))
    const err = await within(sheet).findByTestId('test-error')
    expect(err).toHaveTextContent('This key can withdraw funds')
    expect(err).toHaveTextContent('Create a key with Read + Trade only')
    expect(err.className).toContain('bg-loss-wash')
    // The sheet survives the empty state turning into the card behind it.
    await waitFor(() => expect(screen.getByTestId('live-card')).toHaveAttribute('data-state', 'refused'))
    expect(screen.getByRole('dialog', { name: 'Connect the OKX live account' })).toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getByTestId('test-error')).toBeInTheDocument()
  })

  it('a live test with a wrong key says not verified with the mapped error, keeping only label and key', async () => {
    fakeBackend({ live: liveConn(), liveMode: 'wrong' })
    const user = userEvent.setup()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    await user.click(await within(live).findByRole('button', { name: 'Test again' }))
    expect(await within(live).findByTestId('test-error')).toHaveTextContent('Key or passphrase is wrong')
    expect(within(live).getByTestId('live-status')).toHaveTextContent('Not verified. The last test failed.')
    const rows = within(within(live).getByTestId('live-rows'))
    expect(rows.getByTestId('live-key-tail')).toBeInTheDocument()
    expect(rows.queryByText('Permissions')).toBeNull()
  })

  it('removes the live connection after the typed label, leaving demo untouched', async () => {
    const state = fakeBackend({ live: liveConn() })
    const user = userEvent.setup()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    await user.click(await within(live).findByRole('button', { name: 'Remove…' }))
    const sheet = await screen.findByRole('dialog', { name: 'Remove the OKX live connection?' })
    await user.type(within(sheet).getByLabelText('Type “OKX live” to confirm'), 'OKX live{Enter}')
    expect(await within(live).findByTestId('live-empty')).toBeInTheDocument()
    expect(state.requests.some((r) => r.method === 'DELETE' && r.path === `/api/connections/${LIVE_ID}`)).toBe(true)
    expect(state.requests.some((r) => r.method === 'DELETE' && r.path === `/api/connections/${ID}`)).toBe(false)
    expect(screen.getByTestId('account-card')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Connect OKX live' })).toHaveFocus())
  })

  it('while trading live, the live card says Trading now and Remove sends the owner to Environment', async () => {
    fakeBackend({ live: liveConn(), activeEnv: 'live' })
    const user = userEvent.setup()
    renderPage()
    const live = await screen.findByRole('region', { name: 'Live account' })
    expect(await within(live).findByTestId('trading-live')).toHaveTextContent('Trading now')
    expect(screen.queryByTestId('trading-demo')).toBeNull()
    expect(within(live).getByRole('link', { name: /Atlas is trading on this account/ })).toBeInTheDocument()
    await user.click(within(live).getByRole('button', { name: 'Remove…' }))
    const sheet = await screen.findByRole('dialog', { name: 'Remove the OKX live connection?' })
    expect(sheet).toHaveTextContent('Switch to demo in Settings → Environment before you remove it.')
    expect(within(sheet).queryByRole('textbox')).toBeNull()
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

  it('renders the live section in Arabic: empty state, then the refused key in red', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    const { unmount } = renderPageWithUnmount()
    const empty = await screen.findByTestId('live-empty')
    expect(empty).toHaveTextContent('لا يوجد حساب حقيقي متصل')
    expect(screen.getByRole('button', { name: 'ربط OKX الحقيقي' })).toBeInTheDocument()
    unmount()
    vi.unstubAllGlobals()
    fakeBackend({
      live: liveConn({ permissions: ['read_only', 'trade', 'withdraw'], last_verified_at: null, withdraw_off_verified_at: null, last_test_error: 'withdraw_permission_enabled' }),
    })
    renderPage()
    expect(await screen.findByTestId('live-refused')).toHaveTextContent('هذا المفتاح يستطيع سحب الأموال')
    expect(screen.getByTestId('live-status')).toHaveTextContent('لم يُتحقق منه. هذا المفتاح يستطيع سحب الأموال.')
    expect(screen.getByTestId('live-key-tail')).toHaveTextContent('•••• lv42')
  })

  it('renders a verified live key in Arabic with the Environment link', async () => {
    fakeBackend({ live: liveConn() })
    await i18n.changeLanguage('ar')
    renderPage()
    const live = await screen.findByRole('region', { name: 'الحساب الحقيقي' })
    expect(await within(live).findByRole('link', { name: /انقل أطلس إلى الحقيقي/ })).toHaveAttribute('href', '/settings/environment')
    expect(within(live).getByTestId('uid-tail')).toHaveTextContent('•••• 9876')
  })
})
