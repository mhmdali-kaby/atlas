import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { askApiValue, askInputText, askShare, parseAsk } from '../onboarding/Autonomy'

/* ATL-216: Settings → Autonomy. Raising confirms in a sheet that saves; lowering saves at once. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const LIMITS = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }

type Changes = Record<string, string | number>

function fakeBackend(start: { level: number; ask: string; maxAutonomy?: number }) {
  const state = {
    version: 5,
    settings: { risk_profile: 'balanced', ...LIMITS, autonomy_level: start.level, autonomy_ask_above_usd: start.ask, onboarding_completed: 1 } as Record<string, string | number>,
    patches: [] as { version: number; changes: Changes }[],
    fail: null as null | 409 | 500,
  }
  const doc = () => ({
    data: { id: 's1', env: 'demo', version: state.version, settings: state.settings, presets: { conservative: LIMITS, balanced: LIMITS, aggressive: LIMITS }, max_enabled_autonomy_level: start.maxAutonomy ?? 4, updated_at: '2026-10-28T09:00:00Z' },
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, json?: unknown) => new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [] })
      if (path.startsWith('/api/settings/history')) return reply(200, { data: [] })
      if (path.startsWith('/api/settings/risk-preview')) return reply(200, { data: { max_notional_usd: '3000.00', sizing_equity_usd: '100000.00' } })
      if (path === '/api/settings?env=demo' && method === 'GET') return reply(200, doc())
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { version: number; changes: Changes }
        state.patches.push(body)
        if (state.fail === 500) return reply(500, { message: 'down' })
        if (state.fail === 409) {
          state.fail = null
          state.version += 1
          return reply(409, { message: 'changed', current_version: state.version })
        }
        Object.assign(state.settings, body.changes)
        state.version += 1
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/autonomy'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

const level = (name: string) => within(screen.getByRole('radiogroup', { name: 'Autonomy' })).getByRole('radio', { name })
const toastTitles = () => useToasts.getState().toasts.map((t) => t.title)

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(() => vi.unstubAllGlobals())

describe('Settings → Autonomy (ATL-216)', () => {
  it('leads with the sentence for the saved level and the largest trade from the Money limits preview', async () => {
    fakeBackend({ level: 3, ask: '1000' })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Autonomy' })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('autonomy-sentence')).toHaveTextContent('Trades up to $1,000.00 go to OKX on their own. Larger ones, up to your $3,000.00 limit, wait for you.'))
    expect(level('Auto within limits')).toHaveAttribute('aria-checked', 'true')
    expect(level('Auto within limits')).toHaveTextContent('Current')
    expect(screen.getByRole('textbox', { name: 'Ask me above' })).toHaveValue('1,000')
    expect(screen.getByText('Approval window').parentElement).toHaveTextContent('15 minutes')
  })

  it('lowering 3 → 2 saves at once, with no sheet', async () => {
    const state = fakeBackend({ level: 3, ask: '1000' })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('radio', { name: 'Approve each trade' }))
    await waitFor(() => expect(state.patches).toEqual([{ env: 'demo', version: 5, changes: { autonomy_level: 2 } }]))
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(toastTitles()).toContain('Autonomy is now level 2'))
  })

  it('arrows move focus without opening the sheet; Space chooses', async () => {
    fakeBackend({ level: 2, ask: '1000' })
    const user = userEvent.setup()
    renderPage()
    ;(await screen.findByRole('radio', { name: 'Approve each trade' })).focus()
    await user.keyboard('{ArrowDown}')
    expect(level('Auto within limits')).toHaveFocus()
    expect(screen.queryByRole('dialog')).toBeNull()
    await user.keyboard(' ')
    expect(await screen.findByRole('dialog', { name: 'Let Atlas send trades without asking?' })).toBeInTheDocument()
  })

  it('a higher amount at level 3 opens the raise sheet; a lower one saves at once', async () => {
    const state = fakeBackend({ level: 3, ask: '1000' })
    const user = userEvent.setup()
    renderPage()
    const input = await screen.findByRole('textbox', { name: 'Ask me above' })
    await user.clear(input)
    await user.type(input, '2000')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const sheet = await screen.findByRole('dialog', { name: 'Send larger trades without asking?' })
    await user.click(within(sheet).getByRole('button', { name: 'Save new amount' }))
    await waitFor(() => expect(state.patches.at(-1)?.changes).toEqual({ autonomy_ask_above_usd: '2000' }))

    const again = await screen.findByRole('textbox', { name: 'Ask me above' })
    await user.clear(again)
    await user.type(again, '500{Enter}')
    await waitFor(() => expect(state.patches.at(-1)?.changes).toEqual({ autonomy_ask_above_usd: '500' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('a 409 keeps the sheet open with the "changed in another window" notice; a 500 says nothing changed', async () => {
    const state = fakeBackend({ level: 2, ask: '1000' })
    state.fail = 409
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('radio', { name: 'Full auto' }))
    const sheet = await screen.findByRole('dialog', { name: 'Let Atlas send every trade without asking?' })
    await user.click(within(sheet).getByRole('button', { name: 'Turn on level 4' }))
    expect(await within(sheet).findByText('Settings changed in another window')).toBeInTheDocument()
    expect(within(sheet).getByRole('alert')).toHaveTextContent('version 6')
    state.fail = 500
    await user.click(within(sheet).getByRole('button', { name: 'Turn on level 4' }))
    expect(await within(sheet).findByText('Couldn’t save')).toBeInTheDocument()
    expect(state.settings.autonomy_level).toBe(2)
  })

  it('levels above max_enabled_autonomy_level are disabled', async () => {
    fakeBackend({ level: 2, ask: '1000', maxAutonomy: 2 })
    renderPage()
    expect(await screen.findByRole('radio', { name: 'Full auto' })).toHaveAttribute('aria-disabled', 'true')
    expect(level('Auto within limits')).toHaveAttribute('aria-disabled', 'true')
  })
})

describe('ask-above helpers', () => {
  it('parse, format and send the amount', () => {
    expect(parseAsk('1,000')).toBe(1000)
    expect(parseAsk('-5')).toBe(-5)
    expect(parseAsk('')).toBeNaN()
    expect(askInputText('1000.5')).toBe('1,000.50')
    expect(askApiValue('1,000')).toBe('1000')
    expect(askApiValue('-5')).toBe('-5')
  })
  it('the share of the line that goes on its own', () => {
    expect(askShare(1, 500, 3000)).toBeNull()
    expect(askShare(2, 500, 3000)).toBe(0)
    expect(askShare(3, 750, 3000)).toBe(0.25)
    expect(askShare(3, 5000, 3000)).toBe(1)
    expect(askShare(3, 0, 3000)).toBe(0)
    expect(askShare(4, 0, 3000)).toBe(1)
  })
})
