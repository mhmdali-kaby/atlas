import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import en from '../../i18n/locales/en/settings-markets.json'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { canon, eligibilityParams, numError, stepped, toggled, usSession, draftFrom } from './model'
import type { SettingsDoc } from '../onboarding'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const BALANCED = {
  max_loss_per_trade_pct: '1.0',
  max_position_pct: '5',
  max_daily_loss_pct: '4',
  max_weekly_loss_pct: '10',
  max_leverage: 5,
  max_open_positions: 6,
  max_share_per_instrument_pct: '25',
  max_correlated_exposure_pct: '35',
  min_expected_r: '1.8',
  max_consecutive_losses: 3,
}
const MARKETS = {
  enabled_markets: ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'],
  whitelist: [] as string[],
  blocklist: [] as string[],
  avoid_stock_perps_outside_us_hours: true,
  max_instruments_per_day: 5,
  max_spread_bps: '8',
  min_24h_volume_usd_crypto_perp: '20000000',
  min_24h_volume_usd_stock_perp: '5000000',
  min_24h_volume_usd_commodity_perp: '5000000',
  min_24h_volume_usd_spot: '10000000',
}
const UNIVERSE = [
  { inst_id: 'XAU-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAU', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
  { inst_id: 'XAG-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAG', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
  { inst_id: 'THIN-USDT-SWAP', inst_type: 'SWAP', asset_class: 'crypto', base_ccy: 'THIN', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
]

type Eligible = { inst_id: string; fails: string[]; market: string; min_24h_volume_usd: string | null }

function fakeBackend(opts: { markets?: Partial<typeof MARKETS>; reject422?: string; eligibility?: Eligible[] } = {}) {
  const state = {
    version: 4,
    markets: { ...MARKETS, ...opts.markets } as Record<string, unknown>,
    patches: [] as { version: number; changes: Record<string, unknown> }[],
    eligibilityCalls: [] as URLSearchParams[],
  }
  const doc = () => ({
    data: {
      id: 's1',
      env: 'demo',
      version: state.version,
      settings: { risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1, ...state.markets },
      presets: { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED },
      max_enabled_autonomy_level: 2,
      updated_at: '2026-10-28T09:00:00Z',
    },
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input))
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [] })
      if (path === '/api/settings?env=demo' && method === 'GET') return reply(200, doc())
      if (path.startsWith('/api/settings/history')) return reply(200, { data: [], meta: { total: 0 } })
      if (url.pathname === '/api/instruments') {
        const q = (url.searchParams.get('q') ?? '').toUpperCase()
        return reply(200, { data: UNIVERSE.filter((i) => i.inst_id.startsWith(q) || i.base_ccy.startsWith(q)) })
      }
      if (url.pathname === '/api/settings/markets/eligibility') {
        state.eligibilityCalls.push(url.searchParams)
        const ids = url.searchParams.getAll('inst_ids[]')
        const rows = ids.map((id) => {
          const e = opts.eligibility?.find((x) => x.inst_id === id)
          return {
            inst_id: id,
            eligible: !e || e.fails.length === 0,
            fails: e?.fails ?? [],
            market: e?.market ?? 'crypto_perp',
            on_whitelist: true,
            volume_24h_usd: '2400000',
            min_24h_volume_usd: e?.min_24h_volume_usd ?? null,
            spread_bps: null,
            max_spread_bps: '8',
            notes: ['spread_unknown'],
          }
        })
        return reply(200, { data: { env: 'demo', as_of: '2026-10-30T08:00:00Z', settings_version: 4, draft: [], instruments: rows } })
      }
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { version: number; changes: Record<string, unknown> }
        state.patches.push(body)
        if (opts.reject422 && body.changes[opts.reject422] !== undefined) {
          const msg = `The ${opts.reject422} must be at least 0 and at most 1000000000000.`
          return reply(422, { message: msg, errors: { [`changes.${opts.reject422}`]: [msg] } })
        }
        state.markets = { ...state.markets, ...body.changes }
        state.version += 1
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/markets'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

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

describe('Settings → Markets (ATL-131)', () => {
  it('adding XAU-USDT-SWAP to the blocklist via the picker and saving sends one PATCH with blocklist ["XAU-USDT-SWAP"]', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('combobox', { name: 'Blocked instruments' })
    await user.type(input, 'xau')
    const option = await screen.findByRole('option', { name: /XAU-USDT-SWAP/ })
    expect(option).toHaveAttribute('aria-selected', 'true')
    await user.click(option)

    const list = screen.getByTestId('mk-list-deny')
    expect(within(list).getByText('XAU-USDT-SWAP')).toBeInTheDocument()
    expect(input).toHaveValue('')
    expect(screen.getByTestId('save-bar')).toHaveTextContent('1 unsaved change')

    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(state.patches).toHaveLength(1))
    expect(state.patches[0]).toEqual({ env: 'demo', version: 4, changes: { blocklist: ['XAU-USDT-SWAP'] } })
    await waitFor(() => expect(screen.queryByTestId('save-bar')).not.toBeInTheDocument())
    expect(state.patches).toHaveLength(1)
  })

  it('refuses an instId that is not in the env universe', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('combobox', { name: 'Allowed instruments' })
    await user.type(input, 'NOPE-USDT-SWAP{Enter}')
    expect(await screen.findByTestId('error-whitelist')).toHaveTextContent('NOPE-USDT-SWAP isn’t in the OKX demo universe.')
    expect(screen.queryByTestId('save-bar')).not.toBeInTheDocument()
    expect(state.patches).toHaveLength(0)
  })

  it('renders dated futures and options as disabled, off switches with the later-phase caption from the locale file', async () => {
    fakeBackend()
    renderPage()
    for (const name of ['Dated futures', 'Options']) {
      const sw = await screen.findByRole('switch', { name })
      expect(sw).toBeDisabled()
      expect(sw).toHaveAttribute('aria-checked', 'false')
      expect(sw).toHaveAccessibleDescription(en.later)
    }
    expect(screen.getByTestId('mk-later-futures')).toHaveTextContent(en.later)
    expect(screen.getByTestId('mk-later-options')).toHaveTextContent(en.later)
    // The four live classes are switches the owner can use.
    expect(screen.getByRole('switch', { name: 'Trade Spot' })).toBeEnabled()
    expect(screen.getByRole('switch', { name: 'Trade Commodity perpetuals' })).toHaveAttribute('aria-checked', 'true')
  })

  it('turning a class off sends enabled_markets in kinds order', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('switch', { name: 'Trade Crypto perpetuals' }))
    expect(screen.getByText('Crypto perpetuals are off')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(state.patches).toHaveLength(1))
    expect(state.patches[0]?.changes).toEqual({ enabled_markets: ['spot', 'stock_perp', 'commodity_perp'] })
  })

  it('max instruments per day stops at 1 and 10', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('textbox', { name: /^Max instruments per day/ })
    const inc = screen.getByRole('button', { name: 'Increase Max instruments per day' })
    const dec = screen.getByRole('button', { name: 'Decrease Max instruments per day' })
    expect(input).toHaveValue('5')
    for (let i = 0; i < 5; i++) await user.click(inc)
    expect(input).toHaveValue('10')
    expect(inc).toBeDisabled()
    await user.type(input, '{ArrowUp}')
    expect(input).toHaveValue('10')
    for (let i = 0; i < 9; i++) await user.click(dec)
    expect(input).toHaveValue('1')
    expect(dec).toBeDisabled()
    await user.type(input, '{ArrowDown}')
    expect(input).toHaveValue('1')
    // Typing past the bound is caught before it reaches the server.
    await user.clear(input)
    await user.type(input, '11')
    expect(screen.getByTestId('error-max_instruments_per_day')).toHaveTextContent(en.err.perDay)
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('a 422 on a min volume shows under that field and keeps the typed value', async () => {
    const state = fakeBackend({ reject422: 'min_24h_volume_usd_crypto_perp' })
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('textbox', { name: /^Minimum 24h volume for Crypto perpetuals/ })
    expect(input).toHaveValue('20,000,000')
    await user.clear(input)
    await user.type(input, '25000000')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const error = await screen.findByTestId('error-min_24h_volume_usd_crypto_perp')
    expect(error).toHaveTextContent('The min_24h_volume_usd_crypto_perp must be at least 0 and at most 1000000000000.')
    expect(state.patches[0]?.changes).toEqual({ min_24h_volume_usd_crypto_perp: '25000000' })
    expect(input).toHaveValue('25,000,000')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    // Only that field: the others carry no error.
    expect(screen.queryByTestId('error-min_24h_volume_usd_spot')).not.toBeInTheDocument()
    // Editing the field clears the server's message.
    await user.type(input, '1')
    expect(screen.queryByTestId('error-min_24h_volume_usd_crypto_perp')).not.toBeInTheDocument()
  })

  it('decision 250: a non-empty whitelist says only these can be traded, and each one shows the filter it fails (blocklist wins)', async () => {
    const state = fakeBackend({
      markets: { whitelist: ['THIN-USDT-SWAP', 'XAU-USDT-SWAP', 'XAG-USDT-SWAP'], blocklist: ['XAU-USDT-SWAP'] },
      eligibility: [
        { inst_id: 'THIN-USDT-SWAP', fails: ['min_24h_volume'], market: 'crypto_perp', min_24h_volume_usd: '20000000' },
        { inst_id: 'XAU-USDT-SWAP', fails: ['blocked'], market: 'commodity_perp', min_24h_volume_usd: '5000000' },
        { inst_id: 'XAG-USDT-SWAP', fails: [], market: 'commodity_perp', min_24h_volume_usd: '5000000' },
      ],
    })
    renderPage()
    expect(await screen.findByTestId('mk-allow-note')).toHaveTextContent(en.allowOnly)
    expect(en.allowOnly).toMatch(/^Only these instruments can be traded/)

    await waitFor(() => expect(screen.getByTestId('mk-warn-THIN-USDT-SWAP')).toHaveTextContent('Below the $20M volume minimum today'))
    expect(screen.getByTestId('mk-warn-XAU-USDT-SWAP')).toHaveTextContent(en.fail.blocked)
    expect(en.fail.blocked).toMatch(/blocked/)
    expect(screen.queryByTestId('mk-warn-XAG-USDT-SWAP')).not.toBeInTheDocument()
    const call = state.eligibilityCalls[0]
    expect(call?.getAll('inst_ids[]')).toEqual(['THIN-USDT-SWAP', 'XAU-USDT-SWAP', 'XAG-USDT-SWAP'])
    expect(call?.get('env')).toBe('demo')
  })

  it('an empty whitelist keeps the default note and asks nothing', async () => {
    const state = fakeBackend()
    renderPage()
    expect(await screen.findByTestId('mk-allow-note')).toHaveTextContent(en.allowNote)
    expect(state.eligibilityCalls).toHaveLength(0)
  })
})

describe('markets model', () => {
  it('validates the fields with the design rules', () => {
    expect(numError('min_24h_volume_usd_spot', '20,000,000')).toBeNull()
    expect(numError('min_24h_volume_usd_spot', '')).toBe('empty')
    expect(numError('min_24h_volume_usd_spot', '0')).toBe('zero')
    expect(numError('min_24h_volume_usd_spot', '1.234')).toBe('decimals')
    expect(numError('min_24h_volume_usd_spot', '200000000000')).toBe('max')
    expect(numError('max_spread_bps', '8.5')).toBeNull()
    expect(numError('max_spread_bps', '0.5')).toBe('spread')
    expect(numError('max_spread_bps', '101')).toBe('spread')
    expect(numError('max_instruments_per_day', '٧')).toBeNull()
    expect(numError('max_instruments_per_day', '0')).toBe('perDay')
  })

  it('steps and canonicalises', () => {
    expect(stepped('min_24h_volume_usd_crypto_perp', '20,000,000', 1, '20000000')).toBe('21,000,000')
    expect(stepped('max_spread_bps', '8.5', -1, '8')).toBe('8')
    expect(stepped('max_instruments_per_day', '10', 1, '5')).toBe('10')
    expect(canon('020,000,000.50')).toBe('20000000.5')
    expect(toggled(['commodity_perp', 'spot'], 'crypto_perp', true)).toEqual(['spot', 'crypto_perp', 'commodity_perp'])
  })

  it('sends only valid changed draft values to the eligibility API, an empty list as an empty value', () => {
    const doc = { env: 'demo', version: 1, settings: { ...MARKETS, whitelist: ['BTC-USDT-SWAP'], blocklist: ['XAU-USDT-SWAP'] } } as unknown as SettingsDoc
    const saved = draftFrom(doc)
    const qs = new URLSearchParams(eligibilityParams({ ...saved, blocklist: [], max_spread_bps: '500', min_24h_volume_usd_spot: '1' }, saved, 'demo') ?? '')
    expect(qs.getAll('inst_ids[]')).toEqual(['BTC-USDT-SWAP'])
    expect(qs.get('draft[blocklist]')).toBe('')
    expect(qs.has('draft[max_spread_bps]')).toBe(false)
    expect(qs.get('draft[min_24h_volume_usd_spot]')).toBe('1')
  })

  it('computes the US session from the New York calendar', () => {
    // 28 Oct 2026: New York on daylight time → 13:30–20:00 UTC (16:30–23:00 Baghdad).
    const s = usSession(Date.parse('2026-10-28T14:00:00Z'))
    expect(new Date(s.open).toISOString()).toBe('2026-10-28T13:30:00.000Z')
    expect(new Date(s.close).toISOString()).toBe('2026-10-28T20:00:00.000Z')
    expect(s.isOpen).toBe(true)
    // 3 Nov 2026: standard time → 14:30–21:00 UTC.
    expect(new Date(usSession(Date.parse('2026-11-03T12:00:00Z')).open).toISOString()).toBe('2026-11-03T14:30:00.000Z')
    // Sunday 1 Nov 2026, 15:00 New York: inside the hours, but there is no session on a weekend.
    expect(usSession(Date.parse('2026-11-01T20:00:00Z')).isOpen).toBe(false)
  })
})
