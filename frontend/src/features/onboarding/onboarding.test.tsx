import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import type { Connection, ConnectionTestResponse } from './connections'
import { useToasts } from '../../ui'
import type { RiskLimits, SettingsDoc } from './settings'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const ID = 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30'
const KEY = 'f3b1c2d4-1111-2222-3333-44445555ab12'
const SECRET = 'SECRET-9F8E7D6C5B4A'
const PASS = 'Pass-phrase-77'

type TestMode = 'ok' | 'wrong' | 'withdraw-live' | 'withdraw-demo' | 'down'

function connectionRow(over: Partial<Connection> = {}): Connection {
  return {
    id: ID,
    exchange: 'okx',
    env: 'demo',
    label: 'OKX demo',
    api_key_last4: 'ab12',
    has_secret: true,
    has_passphrase: true,
    permissions: null,
    account_mode: null,
    position_mode: null,
    uid_last4: null,
    last_verified_at: null,
    last_test_error: null,
    created_at: '2026-10-28T08:00:00.000000Z',
    updated_at: '2026-10-28T08:00:00.000000Z',
    ...over,
  }
}

const PRESETS: Record<'conservative' | 'balanced' | 'aggressive', RiskLimits> = {
  conservative: { max_loss_per_trade_pct: '0.5', max_position_pct: '2', max_daily_loss_pct: '2', max_weekly_loss_pct: '5', max_leverage: 2, max_open_positions: 3, max_share_per_instrument_pct: '15', max_correlated_exposure_pct: '20', min_expected_r: '2.0', max_consecutive_losses: 2 },
  balanced: { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 },
  aggressive: { max_loss_per_trade_pct: '2.0', max_position_pct: '10', max_daily_loss_pct: '8', max_weekly_loss_pct: '20', max_leverage: 10, max_open_positions: 10, max_share_per_instrument_pct: '40', max_correlated_exposure_pct: '50', min_expected_r: '1.5', max_consecutive_losses: 4 },
}

function settingsDoc(version = 1, over: Partial<SettingsDoc['settings']> = {}): SettingsDoc {
  return {
    id: '9f6c0a7e-3a1b-4d0e-9a51-2b7f1c2d3e4f',
    env: 'demo',
    version,
    settings: { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, max_instruments_per_day: 5, onboarding_completed: 0, ...over },
    presets: PRESETS,
    updated_at: '2026-10-28T08:00:00.000000Z',
  }
}

const VERIFIED = { last_verified_at: '2026-10-28T08:00:00Z', permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123' }

/**
 * A fake Laravel API: auth, /api/connections CRUD, the test endpoint and GET/PATCH /api/settings
 * (version check, preset rules for risk_profile). Records every exchange.
 */
function fakeBackend(opts: { mode?: TestMode; stored?: Connection | null; settings?: SettingsDoc; conflicts?: number; settingsDown?: boolean } = {}) {
  const state = {
    mode: opts.mode ?? 'ok',
    stored: opts.stored ?? null,
    settings: opts.settings ?? settingsDoc(),
    conflicts: opts.conflicts ?? 0,
    settingsDown: opts.settingsDown ?? false,
    responses: [] as string[],
    requests: [] as { method: string; path: string; body: unknown }[],
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
      if (path === '/api/settings?env=demo' && method === 'GET') {
        if (state.settingsDown) return reply(503, { message: 'Service unavailable' })
        return reply(200, { data: state.settings })
      }
      if (path === '/api/settings' && method === 'PATCH') {
        const req = JSON.parse(String(init.body)) as { env: string; version: number; changes: Record<string, string | number> }
        if (state.conflicts > 0) {
          // Someone else saved in between: the row moves on and this request is stale.
          state.conflicts -= 1
          state.settings = { ...state.settings, version: state.settings.version + 1 }
        }
        if (req.version !== state.settings.version)
          return reply(409, { message: 'Settings were changed elsewhere. Reload them and apply your change again.', current_version: state.settings.version })
        const profile = req.changes.risk_profile
        const preset = typeof profile === 'string' && profile in PRESETS ? PRESETS[profile as keyof typeof PRESETS] : {}
        const next = { ...state.settings.settings, ...preset, ...req.changes } as SettingsDoc['settings']
        const changed = JSON.stringify(next) !== JSON.stringify(state.settings.settings)
        state.settings = { ...state.settings, version: state.settings.version + (changed ? 1 : 0), settings: next }
        return reply(200, { data: state.settings })
      }
      if (path === '/api/connections' && method === 'GET') return reply(200, { data: state.stored ? [state.stored] : [] })
      if (path === '/api/connections' && method === 'POST') {
        state.stored = connectionRow({ label: body?.label ?? 'OKX demo', api_key_last4: (body?.api_key ?? '').slice(-4) })
        return reply(201, { data: state.stored })
      }
      if (path === `/api/connections/${ID}` && method === 'PATCH') {
        state.stored = connectionRow({ ...state.stored, label: body?.label ?? 'OKX demo', api_key_last4: (body?.api_key ?? '').slice(-4), last_verified_at: null })
        return reply(200, { data: state.stored })
      }
      if (path === `/api/connections/${ID}/test` && method === 'POST') {
        if (state.mode === 'down')
          return reply(503, { ok: false, error: 'trading_core_unavailable', message: 'The trading service is not running. Start Atlas and try again.' })
        const verifiedFields = { account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123' }
        const base = { acct_lv: '2', okx_code: null, message: null, ...verifiedFields }
        let res: ConnectionTestResponse
        if (state.mode === 'wrong') {
          state.stored = connectionRow({ ...state.stored, last_test_error: 'invalid_credentials', last_verified_at: null })
          res = { ...base, ok: false, acct_lv: null, account_mode: null, position_mode: null, uid_last4: null, permissions: [], warnings: [], error: 'invalid_credentials', okx_code: '50113', verified_at: null, message: 'Key or passphrase is wrong', connection: state.stored }
        } else if (state.mode === 'withdraw-live') {
          state.stored = connectionRow({ ...state.stored, ...verifiedFields, permissions: ['read_only', 'trade', 'withdraw'], last_test_error: 'withdraw_permission_enabled' })
          res = { ...base, ok: false, permissions: ['read_only', 'trade', 'withdraw'], warnings: [], error: 'withdraw_permission_enabled', verified_at: null, connection: state.stored }
        } else {
          const permissions = state.mode === 'withdraw-demo' ? ['read_only', 'trade', 'withdraw'] : ['read_only', 'trade']
          state.stored = connectionRow({ ...state.stored, ...verifiedFields, permissions, last_verified_at: '2026-10-28T08:00:00.123456Z' })
          res = { ...base, ok: true, permissions, warnings: state.mode === 'withdraw-demo' ? ['withdraw_permission_enabled'] : [], error: null, verified_at: state.stored.last_verified_at, connection: state.stored }
        }
        return reply(200, res)
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderApp(initial: string, { defaultRetries = false } = {}) {
  const router = createMemoryRouter(routes, { initialEntries: [initial] })
  const client = new QueryClient(defaultRetries ? {} : { defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router, view }
}

async function fillAndTest(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('API key'), KEY)
  await user.type(screen.getByLabelText('Secret key'), SECRET)
  await user.type(screen.getByLabelText('Passphrase'), PASS)
  await user.click(screen.getByRole('button', { name: 'Test connection' }))
}

const continueButton = () => screen.getByTestId('ob-next')

beforeEach(async () => {
  resetCsrfForTests()
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('onboarding', () => {
  it('sends a signed-in owner with no verified demo connection from / to Welcome, with a 4-step stepper', async () => {
    fakeBackend()
    const user = userEvent.setup()
    const { router } = renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome to Atlas' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/onboarding')

    const stepper = screen.getByRole('navigation', { name: 'Setup steps' })
    expect(within(stepper).getAllByRole('listitem')).toHaveLength(4)
    expect(within(stepper).getByText('Welcome').closest('[aria-current="step"]')).not.toBeNull()
    expect(screen.getByText(/Automated trading can lose money\. Start on demo/)).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Demo/Live: Demo')
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()

    await user.click(continueButton())
    const title = await screen.findByRole('heading', { level: 1, name: 'Connect your OKX demo account' })
    expect(router.state.location.search).toBe('?step=connect')
    await waitFor(() => expect(title).toHaveFocus())
    expect(continueButton()).toBeDisabled()
  })

  it('lands a verified, onboarded owner on the first sidebar page, not on onboarding', async () => {
    fakeBackend({ stored: connectionRow(VERIFIED), settings: settingsDoc(3, { onboarding_completed: 1 }) })
    const { router } = renderApp('/')
    // Phase 0 has no sidebar page in this checkout yet, so "/" shows the shell's fallback.
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Checking your OKX connection…' })).toBeNull())
    await act(() => new Promise((r) => setTimeout(r, 50)))
    expect(router.state.location.pathname).not.toBe('/onboarding')
    expect(screen.queryByRole('heading', { name: 'Welcome to Atlas' })).toBeNull()
  })

  it('saves and tests the key, shows the account details, unlocks Continue, and never echoes a secret', async () => {
    const backend = fakeBackend()
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    expect(await screen.findByLabelText('Secret key')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Passphrase')).toHaveAttribute('type', 'password')

    await fillAndTest(user)
    const success = await screen.findByTestId('test-success')
    expect(within(success).getByText('Connected to OKX demo')).toBeInTheDocument()
    expect(within(success).getByText('Spot and futures mode')).toBeInTheDocument()
    expect(within(success).getByText('One-way mode')).toBeInTheDocument()
    expect(within(success).getByText('Read')).toBeInTheDocument()
    expect(within(success).getByText('Trade')).toBeInTheDocument()
    expect(within(success).getByText('Withdraw off')).toBeInTheDocument()
    expect(within(success).getByTestId('uid-tail')).toHaveTextContent('•••• 6123')
    expect(screen.queryByTestId('withdraw-warning')).toBeNull()
    expect(continueButton()).toBeEnabled()

    const post = backend.requests.find((r) => r.method === 'POST' && r.path === '/api/connections')
    expect(post?.body).toMatchObject({ env: 'demo', api_key: KEY, api_secret: SECRET, passphrase: PASS })
    for (const text of backend.responses) {
      expect(text).not.toContain(SECRET)
      expect(text).not.toContain(PASS)
      expect(text).not.toContain(KEY)
    }
    // the typed values are gone from the page once the test passed
    expect(screen.queryByDisplayValue(SECRET)).toBeNull()
    expect(screen.queryByDisplayValue(PASS)).toBeNull()
  })

  it('shows "Key or passphrase is wrong" on a bad passphrase and keeps Continue disabled', async () => {
    fakeBackend({ mode: 'wrong' })
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await fillAndTest(user)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Key or passphrase is wrong')
    await waitFor(() => expect(alert).toHaveFocus())
    expect(continueButton()).toBeDisabled()
    // what was typed stays, so the owner fixes one field
    expect(screen.getByLabelText('API key')).toHaveValue(KEY)
  })

  it('shows the Arabic message for a wrong passphrase', async () => {
    fakeBackend({ mode: 'wrong' })
    await i18n.changeLanguage('ar')
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await user.type(await screen.findByLabelText('مفتاح API'), KEY)
    await user.type(screen.getByLabelText('المفتاح السري'), SECRET)
    await user.type(screen.getByLabelText('عبارة المرور'), PASS)
    await user.click(screen.getByRole('button', { name: 'اختبار الاتصال' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('المفتاح أو عبارة المرور غير صحيحة')
    expect(continueButton()).toBeDisabled()
  })

  it('blocks a key whose test fails with withdraw_permission_enabled', async () => {
    fakeBackend({ mode: 'withdraw-live' })
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await fillAndTest(user)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This key can withdraw funds')
    expect(alert).toHaveTextContent('create a new key with Read and Trade only')
    expect(continueButton()).toBeDisabled()
  })

  it('accepts a demo key with Withdraw, with a persistent orange notice that survives a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const backend = fakeBackend({ mode: 'withdraw-demo' })
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { view } = renderApp('/onboarding?step=connect')
      await fillAndTest(user)
      const notice = await screen.findByTestId('withdraw-warning')
      expect(notice).toHaveTextContent('This key can withdraw funds')
      expect(notice).toHaveTextContent('Read and Trade only')
      expect(notice).toHaveAttribute('data-tone', 'warning')
      expect(screen.getByText('Connected to OKX demo')).toBeInTheDocument()
      expect(continueButton()).toBeEnabled()

      await act(() => vi.advanceTimersByTimeAsync(10_000))
      expect(screen.getByTestId('withdraw-warning')).toBeInTheDocument()

      // "reload": a fresh app and cache, reading only what GET /api/connections stores
      view.unmount()
      expect(backend.stored?.permissions).toContain('withdraw')
      renderApp('/onboarding?step=connect')
      expect(await screen.findByTestId('withdraw-warning')).toBeInTheDocument()
      expect(screen.getByTestId('saved-key')).toHaveTextContent('•••• ab12')
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows the saved key only as •••• last4 after Back and Continue', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await fillAndTest(user)
    await screen.findByTestId('test-success')

    await user.click(screen.getByRole('button', { name: 'Back' }))
    await screen.findByRole('heading', { level: 1, name: 'Welcome to Atlas' })
    await user.click(continueButton())
    await screen.findByRole('heading', { level: 1, name: 'Connect your OKX demo account' })

    expect(screen.getByTestId('saved-key')).toHaveTextContent('API key •••• ab12')
    expect(screen.queryByLabelText('API key')).toBeNull()
    expect(screen.queryByLabelText('Secret key')).toBeNull()
    expect(document.body.textContent).not.toContain(KEY)
    expect(continueButton()).toBeEnabled()

    // Replace keys reopens an empty form and locks Continue until the new keys pass
    await user.click(screen.getByRole('button', { name: 'Replace keys' }))
    expect(screen.getByLabelText('API key')).toHaveValue('')
    expect(continueButton()).toBeDisabled()
  })

  it('asks for every missing field before calling the API', async () => {
    const backend = fakeBackend()
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await user.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(screen.getByText('Paste the API key from OKX.')).toBeInTheDocument()
    expect(screen.getByText('Paste the secret key from OKX.')).toBeInTheDocument()
    expect(screen.getByText('Paste the passphrase from OKX.')).toBeInTheDocument()
    expect(screen.getByLabelText('API key')).toHaveFocus()
    expect(backend.requests.some((r) => r.path.startsWith('/api/connections') && r.method !== 'GET')).toBe(false)
  })

  it('reports a stopped trading service', async () => {
    fakeBackend({ mode: 'down' })
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await fillAndTest(user)
    expect(await screen.findByRole('alert')).toHaveTextContent('Atlas’s trading service isn’t running')
    expect(continueButton()).toBeDisabled()
  })

  it('sends a URL that skips ahead back to Connect while the key is unverified', async () => {
    fakeBackend()
    const { router } = renderApp('/onboarding?step=risk')
    await screen.findByRole('heading', { level: 1, name: 'Connect your OKX demo account' })
    expect(router.state.location.search).toBe('?step=connect')
  })

  it('goes back to a finished step from the stepper', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderApp('/onboarding?step=connect')
    await screen.findByRole('heading', { level: 1, name: 'Connect your OKX demo account' })
    const done = within(screen.getByRole('navigation', { name: 'Setup steps' })).getByText('Welcome').closest('button')
    expect(done).toHaveTextContent('Done: Welcome')
    await user.click(done as HTMLButtonElement)
    expect(await screen.findByRole('heading', { level: 1, name: 'Welcome to Atlas' })).toBeInTheDocument()
  })
})

describe('onboarding steps 3–4 (ATL-19)', () => {
  const radio = (group: string, name: RegExp | string) => within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name })
  const patches = (backend: ReturnType<typeof fakeBackend>) => backend.requests.filter((r) => r.method === 'PATCH' && r.path === '/api/settings')

  it('shows the three profiles with their limits from the API, Balanced preselected', async () => {
    fakeBackend({ stored: connectionRow(VERIFIED) })
    renderApp('/onboarding?step=risk')
    expect(await screen.findByRole('heading', { level: 1, name: 'Choose a risk profile' })).toBeInTheDocument()
    const group = await screen.findByRole('radiogroup', { name: 'Risk profile' })
    const cards = within(group).getAllByRole('radio')
    expect(cards).toHaveLength(3)

    const conservative = radio('Risk profile', 'Conservative')
    expect(conservative).toHaveAttribute('aria-checked', 'false')
    expect(radio('Risk profile', 'Balanced')).toHaveAttribute('aria-checked', 'true')
    expect(radio('Risk profile', 'Balanced')).toHaveAttribute('tabindex', '0')
    expect(conservative).toHaveAttribute('tabindex', '-1')

    const fig = (card: HTMLElement, field: string) => card.querySelector(`[data-field="${field}"]`)?.textContent
    expect(fig(conservative, 'max_loss_per_trade_pct')).toBe('Max loss per trade0.5%')
    expect(fig(conservative, 'max_position_pct')).toBe('Max position size2%')
    expect(fig(conservative, 'max_daily_loss_pct')).toBe('Max daily loss2.0%')
    expect(fig(conservative, 'max_leverage')).toBe('Max leverage2×')
    expect(fig(radio('Risk profile', 'Aggressive'), 'max_leverage')).toBe('Max leverage10×')
    expect(fig(radio('Risk profile', 'Balanced'), 'max_loss_per_trade_pct')).toBe('Max loss per trade1.0%')
  })

  it('opens the ten-limit comparison', async () => {
    fakeBackend({ stored: connectionRow(VERIFIED) })
    const user = userEvent.setup()
    renderApp('/onboarding?step=risk')
    await user.click(await screen.findByRole('button', { name: 'Compare all ten limits' }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(11)
    expect(within(table).getByRole('rowheader', { name: 'Min expected R' }).parentElement).toHaveTextContent('2.0 R1.8 R1.5 R')
    expect(screen.getByRole('button', { name: 'Hide the comparison' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('saves Conservative chosen by keyboard, then offers levels 1 and 2 only, level 2 by default', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED) })
    const user = userEvent.setup()
    const { router } = renderApp('/onboarding?step=risk')
    await screen.findByRole('radiogroup', { name: 'Risk profile' })

    radio('Risk profile', 'Balanced').focus()
    await user.keyboard('{ArrowLeft}')
    expect(radio('Risk profile', 'Conservative')).toHaveAttribute('aria-checked', 'true')
    expect(radio('Risk profile', 'Conservative')).toHaveFocus()
    await user.tab()
    // Tab leaves the group (one tab stop): next is the comparison disclosure.
    expect(screen.getByRole('button', { name: 'Compare all ten limits' })).toHaveFocus()
    continueButton().focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { level: 1, name: 'Choose how much Atlas does on its own' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('?step=autonomy')
    expect(patches(backend)[0]?.body).toEqual({ env: 'demo', version: 1, changes: { risk_profile: 'conservative' } })
    expect(backend.settings.version).toBe(2)
    expect(backend.settings.settings.risk_profile).toBe('conservative')
    expect(backend.settings.settings.max_leverage).toBe(2)

    const levels = within(screen.getByRole('radiogroup', { name: 'Autonomy' })).getAllByRole('radio')
    expect(levels).toHaveLength(4)
    expect(radio('Autonomy', 'Approve each trade')).toHaveAttribute('aria-checked', 'true')
    for (const name of ['Auto within limits', 'Full auto']) {
      const r = radio('Autonomy', name)
      expect(r).toHaveAttribute('aria-disabled', 'true')
      expect(r).toHaveAccessibleDescription(/Available in a later phase/)
      await user.click(r)
      expect(r).toHaveAttribute('aria-checked', 'false')
    }
    // Arrows skip the disabled levels: from 2, Down wraps to 1, and Down again comes back to 2.
    radio('Autonomy', 'Approve each trade').focus()
    await user.keyboard('{ArrowDown}')
    expect(radio('Autonomy', 'Advise only')).toHaveAttribute('aria-checked', 'true')
    await user.keyboard('{ArrowDown}')
    expect(radio('Autonomy', 'Approve each trade')).toHaveAttribute('aria-checked', 'true')
  })

  it('finishes on /portfolio and never reopens onboarding, even when every default is kept', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED) })
    const user = userEvent.setup()
    const first = renderApp('/onboarding?step=risk')
    await screen.findByRole('radiogroup', { name: 'Risk profile' })
    await user.click(continueButton())
    await screen.findByRole('radiogroup', { name: 'Autonomy' })
    expect(continueButton()).toHaveTextContent('Finish setup')
    await user.click(continueButton())

    await waitFor(() => expect(first.router.state.location.pathname).toBe('/portfolio'))
    expect(patches(backend).at(-1)?.body).toEqual({ env: 'demo', version: 1, changes: { autonomy_level: 2, onboarding_completed: 1 } })
    expect(backend.settings.version).toBe(2)
    first.view.unmount()

    const again = renderApp('/onboarding')
    await waitFor(() => expect(again.router.state.location.pathname).toBe('/portfolio'))
    again.view.unmount()
    const home = renderApp('/onboarding?step=autonomy')
    await waitFor(() => expect(home.router.state.location.pathname).toBe('/portfolio'))
  })

  it('saves autonomy level 1 when chosen', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED), settings: settingsDoc(2, { risk_profile: 'aggressive', ...PRESETS.aggressive }) })
    const user = userEvent.setup()
    const { router } = renderApp('/onboarding?step=autonomy')
    await user.click(await screen.findByRole('radio', { name: 'Advise only' }))
    await user.click(continueButton())
    await waitFor(() => expect(router.state.location.pathname).toBe('/portfolio'))
    expect(backend.settings.settings.autonomy_level).toBe(1)
    expect(backend.settings.settings.onboarding_completed).toBe(1)
  })

  it('refetches and retries once when the settings changed elsewhere (409)', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED), conflicts: 1 })
    const user = userEvent.setup()
    renderApp('/onboarding?step=risk')
    await user.click(await screen.findByRole('radio', { name: 'Aggressive' }))
    await user.click(continueButton())
    await screen.findByRole('radiogroup', { name: 'Autonomy' })
    expect(patches(backend).map((r) => (r.body as { version: number }).version)).toEqual([1, 2])
    expect(backend.settings.settings.risk_profile).toBe('aggressive')
    expect(backend.settings.version).toBe(3)
  })

  it('stays on the step with an error when the retry also conflicts', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED), conflicts: 2 })
    const user = userEvent.setup()
    const { router } = renderApp('/onboarding?step=risk')
    await screen.findByRole('radiogroup', { name: 'Risk profile' })
    await user.click(continueButton())
    await waitFor(() => expect(useToasts.getState().toasts.map((x) => x.title)).toContain('Couldn’t save your choice'))
    expect(useToasts.getState().toasts.at(-1)?.tone).toBe('error')
    expect(patches(backend)).toHaveLength(2)
    expect(router.state.location.search).toBe('?step=risk')
  })

  it('shows an error with Try again when the profiles cannot load, and keeps Continue disabled', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED), settingsDown: true })
    const user = userEvent.setup()
    renderApp('/onboarding?step=risk')
    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t load the risk profiles')
    expect(continueButton()).toBeDisabled()
    backend.settingsDown = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('radiogroup', { name: 'Risk profile' })).toBeInTheDocument()
    expect(continueButton()).toBeEnabled()
  })

  it('sends a verified owner who has not finished setup from / to the risk step', async () => {
    fakeBackend({ stored: connectionRow(VERIFIED) })
    const { router } = renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Choose a risk profile' })).toBeInTheDocument()
    expect(router.state.location.search).toBe('?step=risk')
  })

  it('lets a verified owner through at once when the settings lookup fails, without retrying it', async () => {
    const backend = fakeBackend({ stored: connectionRow(VERIFIED), settingsDown: true })
    // TanStack's default 3 retries (~7 s of backoff) must not apply to the gate's lookup.
    const { router } = renderApp('/', { defaultRetries: true })
    const settingsCalls = () => backend.requests.filter((r) => r.path === '/api/settings?env=demo')
    await waitFor(() => expect(settingsCalls()).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Checking your OKX connection…' })).toBeNull())
    expect(router.state.location.pathname).not.toBe('/onboarding')
    await act(() => new Promise((r) => setTimeout(r, 1200)))
    expect(backend.requests.filter((r) => r.path === '/api/settings?env=demo')).toHaveLength(1)
  })

  it('renders both steps in Arabic, with arrows following the reading direction', async () => {
    fakeBackend({ stored: connectionRow(VERIFIED) })
    await i18n.changeLanguage('ar')
    const user = userEvent.setup()
    renderApp('/onboarding?step=risk')
    expect(await screen.findByRole('heading', { level: 1, name: 'اختر مستوى المخاطرة' })).toBeInTheDocument()
    await screen.findByRole('radiogroup', { name: 'مستوى المخاطرة' })
    expect(document.documentElement.dir).toBe('rtl')
    const balanced = radio('مستوى المخاطرة', 'متوازن')
    expect(balanced).toHaveAttribute('aria-checked', 'true')
    expect(balanced.querySelector('[data-field="max_leverage"]')).toHaveTextContent('أقصى رافعة5×')
    balanced.focus()
    // In RTL the previous card sits to the right.
    await user.keyboard('{ArrowRight}')
    expect(radio('مستوى المخاطرة', 'متحفّظ')).toHaveAttribute('aria-checked', 'true')
    await user.click(continueButton())
    expect(await screen.findByRole('heading', { level: 1, name: 'اختر مقدار ما يفعله أطلس بنفسه' })).toBeInTheDocument()
    expect(radio('الاستقلالية', 'تلقائي بالكامل')).toHaveAccessibleDescription(/متاح في مرحلة لاحقة/)
    expect(continueButton()).toHaveTextContent('إنهاء الإعداد')
  })
})
