import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'

/* ATL-239: Settings → Environment against a faked ATL-236 API. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const PASSWORD = 'correct horse'
const CONN = (env: 'demo' | 'live', uid: string) => ({
  id: `c-${env}`, exchange: 'okx', env, label: `OKX ${env}`, api_key_last4: 'ab12', has_secret: true, has_passphrase: true,
  permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: uid,
  last_verified_at: '2026-11-05T08:00:00Z', withdraw_off_verified_at: env === 'live' ? '2026-11-05T08:00:00Z' : null,
  last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
})

type Key = 'live_connection' | 'demo_history' | 'live_risk_profile' | 'live_autonomy' | 'live_capital_cap' | 'ip_allowlist' | 'sub_account' | 'demo_reviewed'
const KEYS: Key[] = ['live_connection', 'demo_history', 'live_risk_profile', 'live_autonomy', 'live_capital_cap', 'ip_allowlist', 'sub_account', 'demo_reviewed']
const ATTESTED = new Set<Key>(['ip_allowlist', 'sub_account', 'demo_reviewed'])
const DETAIL: Record<Key, [string, string]> = {
  live_connection: ['Verified by OKX, with no withdraw permission.', 'No live OKX connection. Add one in Settings → Connections.'],
  demo_history: ['30 days of demo history.', '12 of 28 days of demo history.'],
  live_risk_profile: ['Live risk profile: Conservative.', 'No live risk profile yet. Choose one in Settings → Risk with Live selected.'],
  live_autonomy: ['Live autonomy is level 2.', 'Live autonomy is not set yet. Choose level 2 or lower.'],
  live_capital_cap: ['Live bot capital cap: 10000 USDT.', 'No live bot capital cap. Set one in Settings → Money limits with Live selected.'],
  ip_allowlist: ['Confirmed by the owner at 2026-11-05T18:40:00.000000Z.', 'Confirm the IP allow-list.'],
  sub_account: ['Confirmed by the owner at 2026-11-05T18:40:00.000000Z.', 'Confirm the sub-account.'],
  demo_reviewed: ['Confirmed by the owner at 2026-11-05T18:40:00.000000Z.', 'Confirm you reviewed demo.'],
}

interface Call { method: string; path: string; body: unknown }

function fakeBackend(opts: { failing?: Key[]; env?: 'demo' | 'live'; attestFails?: boolean } = {}) {
  const state = {
    env: opts.env ?? 'demo',
    ok: Object.fromEntries(KEYS.map((k) => [k, !(opts.failing ?? []).includes(k)])) as Record<Key, boolean>,
    liveStarted: opts.env === 'live' ? '2026-11-01T07:14:00Z' : (null as string | null),
    calls: [] as Call[],
    attestFails: opts.attestFails ?? false,
  }
  const doc = () => {
    const items = KEYS.map((key) => ({
      key,
      kind: ATTESTED.has(key) ? 'attested' : 'computed',
      ok: state.ok[key],
      detail: DETAIL[key][state.ok[key] ? 0 : 1],
      ...(ATTESTED.has(key) ? { confirmed_at: state.ok[key] ? '2026-11-05T18:40:00.000000Z' : null } : {}),
    }))
    const ends = state.liveStarted ? new Date(Date.parse(state.liveStarted) + 30 * 86_400_000).toISOString() : null
    return {
      data: {
        active_env: state.env, live_started_at: state.liveStarted, first_month_ends_at: ends, in_first_live_month: ends !== null,
        switched_at: state.liveStarted, confirmation_text: 'TRADE LIVE', preflight: { ok: items.every((i) => i.ok), items },
      },
    }
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const body = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
      state.calls.push({ method, path, body })
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [CONN('demo', '4821'), ...(state.ok.live_connection ? [CONN('live', '9876')] : [])] })
      if (path === '/api/environment' && method === 'GET') return reply(200, doc())
      if (path === '/api/environment/attestations' && method === 'PUT') {
        if (state.attestFails) return reply(500, { message: 'down' })
        for (const [k, v] of Object.entries(body ?? {})) state.ok[k as Key] = v === true
        return reply(200, doc())
      }
      if (path === '/api/environment/switch' && method === 'POST') {
        if (body?.target === 'demo') {
          state.env = 'demo'
          return reply(200, doc())
        }
        if (body?.password !== PASSWORD)
          return reply(422, { message: 'The password is incorrect.', errors: { password: ['The password is incorrect.'] }, failing_items: [] })
        if (body?.confirmation !== 'TRADE LIVE')
          return reply(422, { message: 'x', errors: { confirmation: ['Type TRADE LIVE exactly, in capital letters.'] }, failing_items: [] })
        state.env = 'live'
        state.liveStarted = '2026-11-06T07:14:00Z'
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/environment'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const toLive = () => screen.getByRole('button', { name: 'Switch to live…' })
const toolbarBadge = () => document.querySelector('.shell-env-badge') as HTMLElement

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(() => vi.unstubAllGlobals())

describe('Settings → Environment (ATL-239)', () => {
  it('lists all eight checks; the two failing show their detail and a fix link, and Switch to live is disabled', async () => {
    fakeBackend({ failing: ['demo_history', 'live_capital_cap'] })
    const router = renderPage()
    await screen.findByTestId('env-preflight')
    for (const k of KEYS) expect(screen.getByTestId(`env-item-${k}`)).toBeInTheDocument()
    expect(screen.getByTestId('env-preflight').querySelectorAll('.env-row')).toHaveLength(8)

    const hist = screen.getByTestId('env-item-demo_history')
    expect(hist).toHaveTextContent('12 of 28 days of demo history.')
    expect(hist).toHaveTextContent('Not yet')
    const cap = screen.getByTestId('env-item-live_capital_cap')
    expect(cap).toHaveTextContent('No live bot capital cap.')
    expect(within(screen.getByTestId('env-item-live_connection')).getByText('Ready')).toBeInTheDocument()

    expect(screen.getByText('6')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '6 of 8 checks ready' })).toBeInTheDocument()
    expect(toLive()).toBeDisabled()
    expect(screen.getByText('Every check below has to pass first. 6 of 8 do.')).toBeInTheDocument()

    const link = within(cap).getByRole('link', { name: 'Open Money limits' })
    expect(link).toHaveAttribute('href', '/settings/money?env=live')
    await userEvent.click(link)
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/money'))
    expect(router.state.location.search).toBe('?env=live')
  })

  it('renders the checks in Arabic with the request asking for Arabic details', async () => {
    const state = fakeBackend({ failing: ['demo_history', 'live_capital_cap'] })
    await i18n.changeLanguage('ar')
    renderPage()
    await screen.findByTestId('env-preflight')
    expect(screen.getByTestId('env-item-live_capital_cap')).toHaveTextContent('سقف لرأس مال البوت في الحقيقي')
    expect(within(screen.getByTestId('env-item-live_capital_cap')).getByRole('link', { name: 'فتح حدود المبالغ' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'الانتقال إلى الحقيقي…' })).toBeDisabled()
    const get = vi.mocked(fetch).mock.calls.find(([u]) => String(u).endsWith('/api/environment'))
    expect(new Headers(get?.[1]?.headers).get('Accept-Language')).toBe('ar')
    expect(state.calls.some((c) => c.path === '/api/environment')).toBe(true)
  })

  it('saves a tick at once, and puts it back with a toast when the save fails', async () => {
    const state = fakeBackend({ failing: ['sub_account'] })
    renderPage()
    const box = await screen.findByRole('checkbox', { name: 'The live key belongs to a sub-account only Atlas uses' })
    expect(box).not.toBeChecked()
    await userEvent.click(box)
    await waitFor(() => expect(state.calls.find((c) => c.method === 'PUT')?.body).toEqual({ sub_account: true }))
    await waitFor(() => expect(toLive()).toBeEnabled())

    state.attestFails = true
    await userEvent.click(screen.getByRole('checkbox', { name: 'The live key belongs to a sub-account only Atlas uses' }))
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.title)).toContain('Couldn’t save that'))
    expect(screen.getByRole('checkbox', { name: 'The live key belongs to a sub-account only Atlas uses' })).toBeChecked()
  })

  it('keeps Switch disabled until a password and exactly TRADE LIVE; a wrong password shows under the password', async () => {
    fakeBackend()
    renderPage()
    await screen.findByTestId('env-preflight')
    expect(toLive()).toBeEnabled()
    await userEvent.click(toLive())
    const dialog = await screen.findByRole('dialog', { name: /Switch Atlas to live\?/ })
    const go = within(dialog).getByRole('button', { name: 'Switch to live' })
    const pw = within(dialog).getByLabelText('Your Atlas password')
    const cf = within(dialog).getByLabelText('Type TRADE LIVE to confirm')
    expect(cf).toHaveAttribute('dir', 'ltr')
    expect(cf).toHaveAttribute('lang', 'en')
    expect(go).toBeDisabled()
    await userEvent.type(cf, 'TRADE LIVE')
    expect(go).toBeDisabled()
    await userEvent.clear(cf)
    await userEvent.type(pw, 'wrong')
    await userEvent.type(cf, 'trade live')
    expect(go).toBeDisabled()
    await userEvent.clear(cf)
    await userEvent.type(cf, 'TRADE LIVE ')
    expect(go).toBeDisabled()
    await userEvent.clear(cf)
    await userEvent.type(cf, 'TRADE LIVE')
    expect(go).toBeEnabled()
    await userEvent.click(go)
    await waitFor(() => expect(pw).toHaveAccessibleDescription('The password is incorrect.'))
    expect(pw).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('a successful switch closes the sheet, shows Live with the first-month end and turns the toolbar badge', async () => {
    const state = fakeBackend()
    renderPage()
    await screen.findByTestId('env-preflight')
    await waitFor(() => expect(toolbarBadge()).toHaveTextContent('Demo'))
    await userEvent.click(toLive())
    const dialog = await screen.findByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Your Atlas password'), PASSWORD)
    await userEvent.type(within(dialog).getByLabelText('Type TRADE LIVE to confirm'), 'TRADE LIVE')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Switch to live' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(state.calls.find((c) => c.path === '/api/environment/switch')?.body).toEqual({ target: 'live', password: PASSWORD, confirmation: 'TRADE LIVE' })
    expect(screen.getByTestId('env-now-live')).toHaveTextContent('Trading now')
    expect(screen.getByTestId('env-month')).toHaveTextContent('Your first live month runs until December 6.')
    expect(toolbarBadge()).toHaveTextContent('Live')
    expect(toolbarBadge()).toHaveAttribute('data-env', 'live')
    expect(screen.getByRole('button', { name: 'Switch to demo…' })).toBeInTheDocument()
    expect(useToasts.getState().toasts.map((t) => t.title)).toContain('Atlas is trading live')
  })

  it('in live, Switch to demo asks one confirmation with no password and returns to demo', async () => {
    const state = fakeBackend({ env: 'live' })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Switch to demo…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Switch Atlas back to demo?' })
    expect(within(dialog).queryByLabelText('Your Atlas password')).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Switch to demo' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(state.calls.find((c) => c.path === '/api/environment/switch')?.body).toEqual({ target: 'demo', confirm: true })
    expect(await screen.findByTestId('env-now-demo')).toBeInTheDocument()
    expect(toLive()).toBeInTheDocument()
    await waitFor(() => expect(toolbarBadge()).toHaveTextContent('Demo'))
  })
})
