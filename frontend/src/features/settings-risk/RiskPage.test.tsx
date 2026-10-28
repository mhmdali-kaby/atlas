import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import type { PresetId, RiskLimits, SettingsValues } from '../onboarding'
import type { HistoryEntry } from './model'
import { changesFor, draftFrom, stepped, withLimit } from './model'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }

const PRESETS: Record<PresetId, RiskLimits> = {
  conservative: {
    max_loss_per_trade_pct: '0.5',
    max_position_pct: '2',
    max_daily_loss_pct: '2',
    max_weekly_loss_pct: '5',
    max_leverage: 2,
    max_open_positions: 3,
    max_share_per_instrument_pct: '15',
    max_correlated_exposure_pct: '20',
    min_expected_r: '2.0',
    max_consecutive_losses: 2,
  },
  balanced: {
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
  },
  aggressive: {
    max_loss_per_trade_pct: '2.0',
    max_position_pct: '10',
    max_daily_loss_pct: '8',
    max_weekly_loss_pct: '20',
    max_leverage: 10,
    max_open_positions: 10,
    max_share_per_instrument_pct: '40',
    max_correlated_exposure_pct: '50',
    min_expected_r: '1.5',
    max_consecutive_losses: 4,
  },
}

const TEN = Object.keys(PRESETS.balanced) as (keyof RiskLimits)[]

function initialSettings(): SettingsValues {
  return { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, onboarding_completed: 1, max_instruments_per_day: 5 }
}

/** A Laravel-shaped settings backend: versioned PATCH with 409 and the real 422 messages. */
function fakeBackend(opts: { maxAutonomy?: number } = {}) {
  const state = {
    version: 2,
    settings: initialSettings(),
    maxAutonomy: opts.maxAutonomy ?? 2,
    history: [
      { id: 'h2', version: 2, diff: { onboarding_completed: { from: 0, to: 1 } }, changed_by: null, created_at: '2026-10-28T09:00:00.000000Z' },
      { id: 'h1', version: 1, diff: { risk_profile: { from: null, to: 'balanced' } }, changed_by: null, created_at: '2026-10-28T08:00:00.000000Z' },
    ] as HistoryEntry[],
    patches: [] as { version: number; changes: Record<string, string | number> }[],
    failGet: false,
  }
  const doc = () => ({
    data: {
      id: 's1',
      env: 'demo',
      version: state.version,
      settings: state.settings,
      presets: PRESETS,
      max_enabled_autonomy_level: state.maxAutonomy,
      updated_at: '2026-10-28T09:00:00Z',
    },
  })

  /** What another window saving does. */
  const saveElsewhere = (changes: Partial<SettingsValues>) => {
    const before = state.settings
    state.settings = { ...state.settings, ...changes } as SettingsValues
    state.version += 1
    const diff = Object.fromEntries(Object.keys(changes).map((k) => [k, { from: before[k] ?? null, to: state.settings[k] ?? null }]))
    state.history.unshift({ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: '2026-10-28T12:00:00.000000Z' })
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [] })
      if (path === '/api/settings?env=demo' && method === 'GET') return state.failGet ? reply(500, { message: 'down' }) : reply(200, doc())
      if (path.startsWith('/api/settings/history')) return reply(200, { data: state.history, meta: { total: state.history.length } })
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { version: number; changes: Record<string, string | number> }
        state.patches.push(body)
        if (body.version !== state.version)
          return reply(409, { message: 'Settings were changed elsewhere. Reload them and apply your change again.', current_version: state.version })
        const loss = body.changes.max_loss_per_trade_pct
        if (loss !== undefined && !(Number(loss) > 0)) {
          return reply(422, {
            message: 'The max_loss_per_trade_pct must be greater than 0 and at most 100.',
            errors: { 'changes.max_loss_per_trade_pct': ['The max_loss_per_trade_pct must be greater than 0 and at most 100.'] },
          })
        }
        const before = state.settings
        let next = { ...before }
        const { risk_profile, ...fields } = body.changes
        if (risk_profile && risk_profile !== 'custom') next = { ...next, ...PRESETS[risk_profile as PresetId], risk_profile: risk_profile as PresetId }
        next = { ...next, ...fields } as SettingsValues
        const base = next.risk_profile === 'custom' ? null : PRESETS[next.risk_profile]
        if (risk_profile === 'custom' || (base && TEN.some((f) => Number(next[f]) !== Number(base[f])))) next.risk_profile = 'custom'
        const diff = Object.fromEntries(
          Object.keys(next)
            .filter((k) => String(next[k]) !== String(before[k]))
            .map((k) => [k, { from: before[k] ?? null, to: next[k] ?? null }]),
        )
        state.settings = next
        if (Object.keys(diff).length > 0) {
          state.version += 1
          state.history.unshift({ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: '2026-10-28T14:30:00.000000Z' })
        }
        return reply(200, doc())
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return { state, saveElsewhere }
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ['/settings/risk'] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const field = (name: string) => screen.getByRole('textbox', { name })
const profiles = () => within(screen.getByRole('radiogroup', { name: 'Risk profile' }))
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

describe('Settings → Risk & Autonomy (ATL-21)', () => {
  it('shows the current profile, all ten limits from GET /api/settings, autonomy and the Activity list', async () => {
    fakeBackend()
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Risk & Autonomy' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Risk & Autonomy' })).toHaveAttribute('aria-current', 'page')
    await screen.findByRole('radiogroup', { name: 'Risk profile' })
    expect(profiles().getByRole('radio', { name: 'Balanced' })).toHaveAttribute('aria-checked', 'true')
    expect(profiles().getByRole('radio', { name: 'Custom' })).toHaveAttribute('aria-checked', 'false')
    expect(field('Max loss per trade')).toHaveValue('1.0')
    expect(field('Max position size')).toHaveValue('5')
    expect(field('Max daily loss')).toHaveValue('4.0')
    expect(field('Max weekly loss')).toHaveValue('10')
    expect(field('Max leverage')).toHaveValue('5')
    expect(field('Max open positions')).toHaveValue('6')
    expect(field('Max share in one instrument')).toHaveValue('25')
    expect(field('Max correlated exposure')).toHaveValue('35')
    expect(field('Min expected R')).toHaveValue('1.8')
    expect(field('Losses in a row before pause')).toHaveValue('3')
    expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 2')
    const autonomy = within(screen.getByRole('radiogroup', { name: 'Autonomy' }))
    expect(autonomy.getByRole('radio', { name: 'Approve each trade' })).toHaveAttribute('aria-checked', 'true')
    expect(autonomy.getByRole('radio', { name: 'Auto within limits' })).toHaveAttribute('aria-disabled', 'true')
    expect(autonomy.getByRole('radio', { name: 'Full auto' })).toHaveAttribute('aria-disabled', 'true')
    const activity = await screen.findByTestId('activity')
    expect(activity).toHaveTextContent('Version 2')
    expect(activity).toHaveTextContent('Setup finished')
    expect(activity).toHaveTextContent('Settings created')
    // 08:00 UTC → 11:00 Baghdad
    expect(activity).toHaveTextContent('Oct 28, 2026, 11:00')
    expect(screen.queryByTestId('save-bar')).toBeNull()
  })

  it('disables autonomy levels above GET /api/settings’s max_enabled_autonomy_level (ATL-33 AC2)', async () => {
    fakeBackend({ maxAutonomy: 1 })
    renderPage()
    const autonomy = within(await screen.findByRole('radiogroup', { name: 'Autonomy' }))
    expect(autonomy.getByRole('radio', { name: 'Advise only' })).not.toHaveAttribute('aria-disabled')
    expect(autonomy.getByRole('radio', { name: 'Approve each trade' })).toHaveAttribute('aria-disabled', 'true')
    expect(autonomy.getByRole('radio', { name: 'Auto within limits' })).toHaveAttribute('aria-disabled', 'true')
    expect(autonomy.getByRole('radio', { name: 'Full auto' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('Max leverage 5 → 3 on Balanced switches to Custom, and Save adds a version describing it', async () => {
    const { state } = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const leverage = await screen.findByRole('textbox', { name: 'Max leverage' })
    await user.click(screen.getByRole('button', { name: 'Decrease Max leverage' }))
    await user.click(screen.getByRole('button', { name: 'Decrease Max leverage' }))
    expect(leverage).toHaveValue('3')
    expect(profiles().getByRole('radio', { name: 'Custom' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('Balanced:', { exact: false })).toBeInTheDocument()
    await user.click(within(screen.getByTestId('save-bar')).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 3'))
    expect(state.patches.at(-1)).toEqual({ env: 'demo', version: 2, changes: { risk_profile: 'custom', max_leverage: 3 } })
    const newest = await waitFor(() => {
      const row = within(screen.getByTestId('activity')).getAllByRole('listitem')[0]
      expect(row).toHaveAttribute('data-version', '3')
      return row as HTMLElement
    })
    expect(newest).toHaveTextContent('Current')
    expect(newest).toHaveTextContent('Risk profile Balanced → Custom')
    expect(newest).toHaveTextContent('Max leverage 5× → 3×')
    expect(toastTitles()).toContain('Saved as version 3')
    expect(screen.queryByTestId('save-bar')).toBeNull()
  })

  it('0 for max loss per trade shows the server’s validation error under that field and saves nothing', async () => {
    const { state } = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const loss = await screen.findByRole('textbox', { name: 'Max loss per trade' })
    await user.clear(loss)
    await user.type(loss, '0')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const error = await screen.findByTestId('error-max_loss_per_trade_pct')
    expect(error.textContent?.replace(/[\u2066\u2069]/g, '')).toBe('Max loss per trade must be more than 0% and at most 100%.')
    expect(loss).toHaveAttribute('aria-invalid', 'true')
    expect(loss).toHaveAccessibleDescription(/must be more than/)
    expect(loss).toHaveFocus()
    expect(state.version).toBe(2)
    expect(state.settings.max_loss_per_trade_pct).toBe('1.0')
    expect(state.patches).toHaveLength(1)
    expect(within(screen.getByTestId('activity')).getAllByRole('listitem')[0]).toHaveAttribute('data-version', '2')
    // What was typed stays; editing the field clears the server error.
    expect(loss).toHaveValue('0.0')
    await user.type(loss, '8')
    expect(screen.queryByTestId('error-max_loss_per_trade_pct')).toBeNull()
  })

  it('an empty limit blocks Save with an error under the field', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const share = await screen.findByRole('textbox', { name: 'Max share in one instrument' })
    await user.clear(share)
    expect(screen.getByTestId('error-max_share_per_instrument_pct')).toHaveTextContent('Enter a value for Max share in one instrument.')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('a 409 shows “Settings changed elsewhere — reloaded” and reloads the current values', async () => {
    const { state, saveElsewhere } = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('textbox', { name: 'Max leverage' })
    await user.click(screen.getByRole('button', { name: 'Decrease Max leverage' }))
    saveElsewhere({ risk_profile: 'custom', max_open_positions: 4 })
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const notice = await screen.findByTestId('conflict-notice')
    expect(notice).toHaveTextContent('Settings changed elsewhere — reloaded')
    expect(notice).toHaveTextContent('Version 3 was saved in another window')
    expect(field('Max open positions')).toHaveValue('4')
    expect(field('Max leverage')).toHaveValue('5')
    expect(profiles().getByRole('radio', { name: 'Custom' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByTestId('settings-version')).toHaveTextContent('Version 3')
    expect(state.patches).toHaveLength(1)
    expect(state.version).toBe(3)
    await waitFor(() => expect(within(screen.getByTestId('activity')).getAllByRole('listitem')[0]).toHaveTextContent('Saved in another window'))
    expect(screen.queryByTestId('save-bar')).toBeNull()
  })

  it('choosing a preset replaces the ten limits, and Revert puts the saved values back', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('radiogroup', { name: 'Risk profile' })
    await user.click(profiles().getByRole('radio', { name: 'Aggressive' }))
    expect(field('Max leverage')).toHaveValue('10')
    expect(field('Max loss per trade')).toHaveValue('2.0')
    await user.click(screen.getByRole('button', { name: 'Revert' }))
    expect(field('Max leverage')).toHaveValue('5')
    expect(profiles().getByRole('radio', { name: 'Balanced' })).toHaveAttribute('aria-checked', 'true')
  })

  it('autonomy level 1 is saved; arrow keys in a field step it', async () => {
    const { state } = fakeBackend()
    const user = userEvent.setup()
    renderPage()
    const r = await screen.findByRole('textbox', { name: 'Min expected R' })
    await user.click(r)
    await user.keyboard('{ArrowUp}')
    expect(r).toHaveValue('1.9')
    await user.keyboard('{ArrowDown}')
    expect(r).toHaveValue('1.8')
    await user.click(screen.getByRole('radio', { name: 'Advise only' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(state.patches.at(-1)?.changes).toEqual({ autonomy_level: 1 }))
  })

  it('a failed load shows the error with Try again', async () => {
    const { state } = fakeBackend()
    state.failGet = true
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByTestId('risk-error')).toHaveTextContent('Couldn’t load your settings')
    state.failGet = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('textbox', { name: 'Max leverage' })).toHaveValue('5')
  })

  it('works in Arabic: RTL, units isolated beside their numbers, localized server error', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    const user = userEvent.setup()
    renderPage()
    const loss = await screen.findByRole('textbox', { name: 'أقصى خسارة للصفقة' })
    expect(document.documentElement).toHaveAttribute('dir', 'rtl')
    expect(loss.closest('[dir="ltr"]')).not.toBeNull()
    await user.clear(loss)
    await user.type(loss, '0')
    await user.click(screen.getByRole('button', { name: 'حفظ التغييرات' }))
    const error = await screen.findByTestId('error-max_loss_per_trade_pct')
    expect(error.textContent).toContain('⁦0%⁩')
    expect(error.textContent).toContain('⁦100%⁩')
    expect(error.textContent).not.toContain('max_loss_per_trade_pct')
  })
})

describe('settings-risk model', () => {
  const doc = { id: 's', env: 'demo' as const, version: 2, settings: initialSettings(), presets: PRESETS, updated_at: '' }

  it('steps on the grid and inside the stepper bounds', () => {
    expect(stepped('max_loss_per_trade_pct', '1.0', 1)).toBe('1.1')
    expect(stepped('max_loss_per_trade_pct', '0.1', -1)).toBe('0.1')
    expect(stepped('max_daily_loss_pct', '4.2', 1)).toBe('4.5')
    expect(stepped('max_leverage', '20', 1)).toBe('20')
    expect(stepped('max_leverage', '', 1)).toBe('2')
  })

  it('a limit equal to a preset returns to it; any other value is Custom', () => {
    const d = draftFrom(doc)
    expect(withLimit(d, 'max_leverage', '3', PRESETS).profile).toBe('custom')
    expect(withLimit(withLimit(d, 'max_leverage', '3', PRESETS), 'max_leverage', '5', PRESETS).profile).toBe('balanced')
  })

  it('sends integers for counts and decimal strings for percentages', () => {
    const d = withLimit(withLimit(draftFrom(doc), 'max_open_positions', '4', PRESETS), 'max_daily_loss_pct', '3,5', PRESETS)
    expect(changesFor(d, doc)).toEqual({ risk_profile: 'custom', max_open_positions: 4, max_daily_loss_pct: '3.5' })
    expect(changesFor(draftFrom(doc), doc)).toEqual({})
  })
})
