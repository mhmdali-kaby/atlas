import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import en from '../../i18n/locales/en/settings-money.json'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { changesFor, fieldError, percentOf, stepped, textsFrom, toCents } from './model'
import type { RiskPreview } from './model'
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
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }

type Money = { bot_capital_cap_usdt: string | null; max_usdt_per_trade: string; max_new_exposure_per_day_usdt: string; max_trades_per_day: number }

/** The preview the fake Laravel answers: ATL-129's shape, worked out the same way. */
function previewFor(equity: string | null, m: Money, used = '0.00'): RiskPreview {
  const base = { env: 'demo' as const, settings_version: 4, used_exposure_today_usd: used, trades_today: 0 }
  if (equity === null) {
    return {
      ...base,
      equity_usd: null,
      equity_as_of: null,
      sizing_equity_usd: null,
      cap_binds: false,
      max_risk_usd: null,
      risk_binding_limit: null,
      max_notional_usd: null,
      notional_binding_limit: null,
      reason: 'no_equity',
    }
  }
  const eq = toCents(equity)!
  const cap = m.bot_capital_cap_usdt === null ? null : toCents(m.bot_capital_cap_usdt)!
  const sizing = cap !== null && cap < eq ? cap : eq
  const candidates = [
    ['max_position_pct', percentOf(sizing, '5')],
    ['max_usdt_per_trade', toCents(m.max_usdt_per_trade)!],
    ['max_new_exposure_per_day', toCents(m.max_new_exposure_per_day_usdt)! - toCents(used)!],
  ] as const
  const [key, use] = candidates.reduce((a, b) => (b[1] < a[1] ? b : a))
  const s = (c: bigint) => `${c / 100n}.${String(c % 100n).padStart(2, '0')}`
  return {
    ...base,
    equity_usd: equity,
    equity_as_of: new Date(Date.now() - 60_000).toISOString(),
    sizing_equity_usd: s(sizing),
    cap_binds: cap !== null && cap < eq,
    max_risk_usd: s(percentOf(sizing, '1.0')),
    risk_binding_limit: 'max_loss_per_trade_pct',
    max_notional_usd: s(use),
    notional_binding_limit: key,
    reason: null,
  }
}

function fakeBackend(opts: { equity?: string | null; money?: Partial<Money>; reject422?: boolean; env?: 'demo' | 'live' } = {}) {
  const env = opts.env ?? 'demo'
  const state = {
    version: 4,
    equity: opts.equity === undefined ? '5068412.56' : opts.equity,
    money: { bot_capital_cap_usdt: '100000', max_usdt_per_trade: '5000', max_new_exposure_per_day_usdt: '25000', max_trades_per_day: 10, ...opts.money } as Money,
    previews: [] as URLSearchParams[],
    patches: [] as { version: number; changes: Record<string, unknown> }[],
  }
  const doc = () => ({
    data: {
      id: 's1',
      env,
      version: state.version,
      settings: { risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1, ...state.money },
      presets: PRESETS,
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
      if (url.pathname === '/api/environment') {
        return reply(200, {
          data: { active_env: env, live_started_at: null, first_month_ends_at: null, in_first_live_month: false, switched_at: null, confirmation_text: 'TRADE LIVE', preflight: { ok: true, items: [] } },
        })
      }
      if (path === `/api/settings?env=${env}` && method === 'GET') return reply(200, doc())
      if (path.startsWith('/api/settings/history')) return reply(200, { data: [], meta: { total: 0 } })
      if (url.pathname === '/api/settings/risk-preview') {
        state.previews.push(url.searchParams)
        const draft = { ...state.money }
        for (const k of ['bot_capital_cap_usdt', 'max_usdt_per_trade', 'max_new_exposure_per_day_usdt'] as const) {
          const v = url.searchParams.get(k)
          if (v !== null) (draft as Record<string, unknown>)[k] = k === 'bot_capital_cap_usdt' && (v === '' || v === 'null') ? null : v
        }
        return reply(200, { data: previewFor(state.equity, draft) })
      }
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { version: number; changes: Record<string, unknown> }
        state.patches.push(body)
        if (opts.reject422 && body.changes.max_usdt_per_trade !== undefined) {
          const msg = 'The max_usdt_per_trade must be greater than 0 and at most 1000000000000.'
          return reply(422, { message: msg, errors: { 'changes.max_usdt_per_trade': [msg] } })
        }
        state.money = { ...state.money, ...(body.changes as Partial<Money>) }
        state.version += 1
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/money'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const field = (name: string) => screen.getByRole('textbox', { name: new RegExp(`^${name}(, changed)?$`) })
const perTradeCalls = (state: ReturnType<typeof fakeBackend>) => state.previews.map((q) => q.get('max_usdt_per_trade')).filter((v) => v !== null)

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

describe('Settings → Money limits (ATL-130)', () => {
  it('max per trade 3000 calls the preview with that draft after the debounce and marks max per trade as binding', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Money limits' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('ml-sentence')).toHaveTextContent('$5,000.00'))
    // Saved: max position 5% of $100,000 binds at $5,000.
    expect(screen.getByTestId('ml-line-use')).toHaveAttribute('data-binding', 'max_position_pct')

    const input = field('Max per trade')
    await user.clear(input)
    await user.type(input, '3000')
    // Nothing sent before the 300 ms debounce.
    expect(perTradeCalls(state)).toEqual([])
    await waitFor(() => expect(perTradeCalls(state)).toEqual(['3000']))

    await waitFor(() => expect(screen.getByTestId('ml-use')).toHaveTextContent('$3,000.00'))
    expect(screen.getByTestId('ml-risk')).toHaveTextContent('$1,000.00')
    expect(screen.getByTestId('ml-sentence')).toHaveTextContent('your next trade can risk at most $1,000.00 and use at most $3,000.00')
    expect(screen.getByTestId('ml-line-use')).toHaveAttribute('data-binding', 'max_usdt_per_trade')
    // The binding limit is marked in the form and in the chart.
    const row = screen.getByTestId('ml-form').querySelector('[data-field="max_usdt_per_trade"]') as HTMLElement
    expect(row).toHaveAttribute('data-binding', 'true')
    expect(within(row).getByText('Sets the limit')).toBeInTheDocument()
    expect(screen.getByTestId('ml-cut').querySelector('[data-key="max_usdt_per_trade"]')).toHaveAttribute('data-binding', 'true')
    expect(screen.getByTestId('ml-cut').querySelector('[data-key="max_position_pct"]')).not.toHaveAttribute('data-binding')
    expect(screen.getByText('Next trade, with your unsaved changes')).toBeInTheDocument()
    expect(screen.getByTestId('ml-line-use')).toHaveTextContent('was $5,000.00')
  })

  it('a server 422 on max_usdt_per_trade shows under that field and keeps the typed value', async () => {
    const state = fakeBackend({ reject422: true })
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('textbox', { name: 'Max per trade' })
    await user.clear(input)
    await user.type(input, '2500.5')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const error = await screen.findByTestId('error-max_usdt_per_trade')
    expect(error).toHaveTextContent('The max_usdt_per_trade must be greater than 0 and at most 1000000000000.')
    // Kept as typed, grouped on blur as every field is.
    expect(field('Max per trade')).toHaveValue('2,500.5')
    expect(field('Max per trade')).toHaveAttribute('aria-invalid', 'true')
    expect(state.patches).toEqual([{ env: 'demo', version: 4, changes: { max_usdt_per_trade: '2500.5' } }])
    expect(screen.getByTestId('save-bar')).toHaveTextContent('Fix 1 limit to save')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 4')
  })

  it('no_equity renders the locale notice and no $ figures', async () => {
    fakeBackend({ equity: null })
    renderPage()
    const notice = await screen.findByTestId('ml-no-equity')
    expect(notice).toHaveTextContent(en.noEq.title)
    expect(notice).toHaveTextContent(en.noEq.body.demo)
    expect(within(notice).getByRole('link', { name: en.noEq.btn })).toHaveAttribute('href', '/settings/connections')
    expect(screen.getByTestId('ml-preview').textContent).not.toMatch(/\$/)
    expect(screen.queryByTestId('ml-sentence')).toBeNull()
    // The form stays editable.
    expect(field('Max per trade')).toBeEnabled()
  })

  it('decision 250: with cap_binds the sentence names the cap and each figure names its own binding limit', async () => {
    fakeBackend({ equity: '100000.00', money: { bot_capital_cap_usdt: '10000', max_usdt_per_trade: '1000' } })
    renderPage()
    const sentence = await screen.findByTestId('ml-sentence')
    await waitFor(() => expect(sentence).toHaveTextContent('With equity $10,000.00 (your bot capital cap), your next trade can risk at most $100.00 and use at most $500.00.'))
    expect(en.s.cap.startsWith('With equity {{sizing}} (your bot capital cap)')).toBe(true)
    const risk = screen.getByTestId('ml-line-risk')
    expect(risk).toHaveAttribute('data-binding', 'max_loss_per_trade_pct')
    expect(risk).toHaveTextContent('1% of $10,000.00, the max loss per trade in your Balanced profile.')
    const use = screen.getByTestId('ml-line-use')
    expect(use).toHaveAttribute('data-binding', 'max_position_pct')
    expect(use).toHaveTextContent('Set by Max position, the smallest of these limits.')
    expect(screen.getByTestId('ml-equity')).toHaveTextContent('Your bot capital cap lets Atlas size trades from $10,000.00 of it.')
    expect(screen.getByTestId('ml-note-profile')).toHaveTextContent('Your Balanced profile’s max position, 5% or $500.00, is below these limits')
  })

  it('an invalid draft is caught on the client, holds the last preview and blocks Save', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('textbox', { name: 'Max new exposure per day' })
    await waitFor(() => expect(state.previews.length).toBeGreaterThan(0))
    const before = state.previews.length
    await user.clear(input)
    await user.type(input, '12.5.0')
    expect(screen.getByTestId('error-max_new_exposure_per_day_usdt')).toHaveTextContent(en.err.digits)
    expect(screen.getByTestId('ml-invalid')).toHaveTextContent(en.invalid)
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await new Promise((r) => setTimeout(r, 450))
    expect(state.previews.slice(before).some((q) => q.get('max_new_exposure_per_day_usdt') === '12.5.0')).toBe(false)
  })

  it('removing the cap previews and saves null', async () => {
    const state = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Remove cap' }))
    expect(field('Bot capital cap')).toHaveValue('')
    await waitFor(() => expect(state.previews.some((q) => q.get('bot_capital_cap_usdt') === '')).toBe(true))
    await waitFor(() => expect(screen.getByTestId('ml-sentence')).toHaveTextContent('With equity $5,068,412.56, your next trade can risk at most $50,684.12'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(state.patches).toEqual([{ env: 'demo', version: 4, changes: { bot_capital_cap_usdt: null } }]))
    await waitFor(() => expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 5'))
  })

  it('in live, the subtitle names the OKX live account, in English and Arabic (ATL-244)', async () => {
    fakeBackend({ env: 'live' })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Money limits' })).toBeInTheDocument()
    expect(await screen.findByText(/Caps in USDT on what Atlas may put into trades on the OKX live account\./)).toBeInTheDocument()
    expect(screen.queryByText(/OKX demo account/)).toBeNull()

    await i18n.changeLanguage('ar')
    expect(await screen.findByText(/حساب OKX الحقيقي/)).toBeInTheDocument()
    expect(screen.queryByText(/التجريبي/)).toBeNull()
  })
})

describe('money model', () => {
  const doc = {
    id: 's',
    env: 'demo',
    version: 1,
    settings: { max_usdt_per_trade: '3000', bot_capital_cap_usdt: null, max_new_exposure_per_day_usdt: '25000', max_trades_per_day: 10 },
    presets: PRESETS,
    updated_at: '',
  } as unknown as SettingsDoc

  it('validates like the server', () => {
    expect(fieldError('max_usdt_per_trade', '')).toBe('empty')
    expect(fieldError('max_usdt_per_trade', '0')).toBe('min')
    expect(fieldError('max_usdt_per_trade', '0.00000001')).toBeNull()
    expect(fieldError('max_usdt_per_trade', '1.123456789')).toBe('decimals')
    expect(fieldError('max_usdt_per_trade', '1000000000000')).toBeNull()
    expect(fieldError('max_usdt_per_trade', '1000000000000.01')).toBe('max')
    expect(fieldError('max_usdt_per_trade', '3,000')).toBeNull()
    expect(fieldError('max_usdt_per_trade', '٣٠٠٠')).toBeNull()
    expect(fieldError('max_usdt_per_trade', '3e3')).toBe('digits')
    expect(fieldError('bot_capital_cap_usdt', '')).toBeNull()
    expect(fieldError('max_trades_per_day', '0')).toBe('int')
    expect(fieldError('max_trades_per_day', '1000')).toBeNull()
    expect(fieldError('max_trades_per_day', '1001')).toBe('int')
    expect(fieldError('max_trades_per_day', '2.5')).toBe('int')
  })

  it('steps by 10^(digits − 2) and starts an empty cap at 100,000', () => {
    expect(stepped('max_usdt_per_trade', '3,000', 1)).toBe('3,100')
    expect(stepped('max_usdt_per_trade', '3,000', -1)).toBe('2,900')
    expect(stepped('max_usdt_per_trade', '25,000', 1)).toBe('26,000')
    expect(stepped('bot_capital_cap_usdt', '', 1)).toBe('100,000')
    expect(stepped('bot_capital_cap_usdt', '', -1)).toBe('')
    expect(stepped('max_trades_per_day', '10', 1)).toBe('11')
  })

  it('sends only changed keys, grouped text equal to the saved value is no change', () => {
    const texts = textsFrom(doc)
    expect(texts.max_usdt_per_trade).toBe('3,000')
    expect(changesFor(texts, doc)).toEqual({})
    expect(changesFor({ ...texts, max_usdt_per_trade: '3000.00', max_trades_per_day: '8', bot_capital_cap_usdt: '50,000' }, doc)).toEqual({
      max_trades_per_day: 8,
      bot_capital_cap_usdt: '50000',
    })
  })
})
