import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { dayText } from '../settings-environment/model'

/* ATL-242: "Settings for: Demo | Live" on Risk, Money limits, Autonomy and Markets. */

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
const CONSERVATIVE = { ...BALANCED, max_loss_per_trade_pct: '0.5', max_position_pct: '2', max_daily_loss_pct: '2', max_weekly_loss_pct: '5', max_leverage: 2, max_open_positions: 3, max_share_per_instrument_pct: '15', max_correlated_exposure_pct: '20', min_expected_r: '2.0', max_consecutive_losses: 2 }
const AGGRESSIVE = { ...BALANCED, max_loss_per_trade_pct: '2.0', max_position_pct: '10', max_daily_loss_pct: '8', max_weekly_loss_pct: '20', max_leverage: 10, max_open_positions: 10, max_share_per_instrument_pct: '40', max_correlated_exposure_pct: '50', min_expected_r: '1.5', max_consecutive_losses: 4 }
const PRESETS = { conservative: CONSERVATIVE, balanced: BALANCED, aggressive: AGGRESSIVE }

type Env = 'demo' | 'live'
type Values = Record<string, string | number | null | string[] | boolean>

const ENDS = '2026-12-02T07:12:00.000000Z'
const REFUSED = {
  en: 'During the first live month (until 2026-12-02 10:12) live autonomy can be at most level 2.',
  ar: 'خلال الشهر الأول على الحقيقي (حتى 2026-12-02 10:12) يمكن أن تكون استقلالية الحقيقي المستوى 2 على الأكثر.',
}

function initial(): Values {
  return {
    risk_profile: 'balanced',
    ...BALANCED,
    autonomy_level: 2,
    autonomy_ask_above_usd: '1000',
    onboarding_completed: 1,
    bot_capital_cap_usdt: null,
    max_usdt_per_trade: '5000',
    max_new_exposure_per_day_usdt: '25000',
    max_trades_per_day: 10,
  }
}

/** Two settings rows, one per env, behind GET/PATCH /api/settings (ATL-238's first-month 422 on live). */
/** `staleDoc`: the month started after the page loaded, so GET still shows no end date but PATCH refuses. */
function fakeBackend(opts: { firstMonth?: boolean; staleDoc?: boolean } = {}) {
  const rows: Record<Env, { version: number; settings: Values }> = {
    demo: { version: 7, settings: initial() },
    live: { version: 1, settings: initial() },
  }
  const patches: { env: Env; version: number; changes: Values }[] = []
  const gets: string[] = []
  const doc = (env: Env) => ({
    data: {
      id: `s-${env}`,
      env,
      version: rows[env].version,
      settings: rows[env].settings,
      presets: PRESETS,
      max_enabled_autonomy_level: 4,
      first_live_month_ends_at: env === 'live' && opts.firstMonth && !opts.staleDoc ? ENDS : null,
      max_live_autonomy_level: 2,
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
      if (url.pathname === '/api/settings/history') return reply(200, { data: [], meta: { total: 0 } })
      if (url.pathname === '/api/settings/risk-preview') {
        const env = (url.searchParams.get('env') ?? 'demo') as Env
        return reply(200, {
          data: {
            env,
            settings_version: rows[env].version,
            used_exposure_today_usd: '0.00',
            trades_today: 0,
            equity_usd: '100000.00',
            equity_as_of: new Date(Date.now() - 60_000).toISOString(),
            sizing_equity_usd: '100000.00',
            cap_binds: false,
            max_risk_usd: '1000.00',
            risk_binding_limit: 'max_loss_per_trade_pct',
            max_notional_usd: '5000.00',
            notional_binding_limit: 'max_usdt_per_trade',
            reason: null,
          },
        })
      }
      if (url.pathname === '/api/settings' && method === 'GET') {
        const env = url.searchParams.get('env') as Env
        gets.push(env)
        return reply(200, doc(env))
      }
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { env: Env; version: number; changes: Values }
        patches.push(body)
        const row = rows[body.env]
        if (body.env === 'live' && opts.firstMonth && Number(body.changes.autonomy_level ?? 0) > 2) {
          // Laravel answers in the owner's language; the SPA shows the message as it comes.
          const msg = i18n.language === 'ar' ? REFUSED.ar : REFUSED.en
          return reply(422, { message: msg, errors: { 'changes.autonomy_level': [msg] } })
        }
        const { risk_profile, ...rest } = body.changes
        if (typeof risk_profile === 'string' && risk_profile !== 'custom') Object.assign(row.settings, PRESETS[risk_profile as keyof typeof PRESETS])
        if (risk_profile !== undefined) row.settings.risk_profile = risk_profile
        Object.assign(row.settings, rest)
        row.version += 1
        return reply(200, doc(body.env))
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return { rows, patches, gets }
}

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const scopeRadio = (name: string) => within(screen.getByRole('radiogroup', { name: 'Settings for' })).getByRole('radio', { name })

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(() => vi.unstubAllGlobals())

const PANES = ['/settings/risk', '/settings/money', '/settings/autonomy', '/settings/markets'] as const

describe('Settings for: Demo | Live (ATL-242)', () => {
  it.each(PANES)('%s with no param shows the active env (Demo) and no note', async (pane) => {
    const be = fakeBackend()
    renderAt(pane)
    await waitFor(() => expect(scopeRadio('Demo')).toHaveAttribute('aria-checked', 'true'))
    expect(scopeRadio('Live')).toHaveAttribute('aria-checked', 'false')
    expect(screen.queryByTestId('env-scope-notice')).toBeNull()
    await waitFor(() => expect(be.gets).toContain('demo'))
    expect(be.gets).not.toContain('live')
  })

  it.each(PANES)('%s?env=live selects Live, reads the live row and shows the applies-after-switch note', async (pane) => {
    const be = fakeBackend()
    renderAt(`${pane}?env=live`)
    await waitFor(() => expect(scopeRadio('Live')).toHaveAttribute('aria-checked', 'true'))
    expect(screen.getByTestId('env-scope-notice')).toHaveTextContent('You’re editing the live settings. They apply once Atlas switches to live in Settings → Environment.')
    await waitFor(() => expect(be.gets).toContain('live'))
    expect(be.gets).not.toContain('demo')
  })

  it('choosing Demo removes the note and reads the demo row; choosing Live brings it back', async () => {
    const be = fakeBackend()
    const user = userEvent.setup()
    const router = renderAt('/settings/risk?env=live')
    await screen.findByTestId('env-scope-notice')
    await user.click(scopeRadio('Demo'))
    await waitFor(() => expect(screen.queryByTestId('env-scope-notice')).toBeNull())
    expect(router.state.location.search).toBe('?env=demo')
    await waitFor(() => expect(be.gets).toContain('demo'))
    await user.click(scopeRadio('Live'))
    expect(await screen.findByTestId('env-scope-notice')).toBeInTheDocument()
  })

  it('Risk with Live selected saves the risk profile with env=live; demo is unchanged', async () => {
    const be = fakeBackend()
    const demoBefore = structuredClone(be.rows.demo)
    const user = userEvent.setup()
    renderAt('/settings/risk?env=live')
    await user.click(await screen.findByRole('radio', { name: /Conservative/ }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(be.patches).toEqual([{ env: 'live', version: 1, changes: expect.objectContaining({ risk_profile: 'conservative' }) }]))
    expect(be.rows.live.settings.risk_profile).toBe('conservative')
    expect(be.rows.demo).toEqual(demoBefore)
  })

  it('Money limits with Live selected saves the bot capital cap with env=live', async () => {
    const be = fakeBackend()
    const demoBefore = structuredClone(be.rows.demo)
    const user = userEvent.setup()
    renderAt('/settings/money?env=live')
    const cap = await screen.findByRole('textbox', { name: /^Bot capital cap/ })
    await user.clear(cap)
    await user.type(cap, '10000')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(be.patches).toEqual([{ env: 'live', version: 1, changes: { bot_capital_cap_usdt: '10000' } }]))
    expect(be.rows.live.settings.bot_capital_cap_usdt).toBe('10000')
    expect(be.rows.demo).toEqual(demoBefore)
  })

  it('Autonomy with Live selected lowers to 2 on the live row; demo is unchanged', async () => {
    const be = fakeBackend()
    be.rows.live.settings.autonomy_level = 3
    const demoBefore = structuredClone(be.rows.demo)
    const user = userEvent.setup()
    renderAt('/settings/autonomy?env=live')
    await user.click(await screen.findByRole('radio', { name: 'Approve each trade' }))
    await waitFor(() => expect(be.patches).toEqual([{ env: 'live', version: 1, changes: { autonomy_level: 2 } }]))
    expect(be.rows.live.settings.autonomy_level).toBe(2)
    expect(be.rows.demo).toEqual(demoBefore)
  })

  it.each(['en', 'ar'] as const)('a first live month that began after the page loaded: live autonomy 3 is refused with the API message (%s)', async (lang) => {
    await i18n.changeLanguage(lang)
    const be = fakeBackend({ firstMonth: true, staleDoc: true })
    const user = userEvent.setup()
    renderAt('/settings/autonomy?env=live')
    const name = lang === 'en' ? 'Auto within limits' : i18n.t('au.l3', { ns: 'onboarding' })
    await user.click(await screen.findByRole('radio', { name }))
    const sheet = await screen.findByRole('dialog')
    await user.click(within(sheet).getByRole('button', { name: lang === 'en' ? 'Turn on level 3' : i18n.t('sh.confirm3', { ns: 'autonomy' }) }))
    expect(await within(sheet).findByTestId('au-sheet-refused')).toHaveTextContent(REFUSED[lang])
    expect(be.patches.at(-1)?.env).toBe('live')
    expect(be.rows.live.settings.autonomy_level).toBe(2)
  })

  it('with no first month running, live autonomy 3 saves', async () => {
    const be = fakeBackend()
    const user = userEvent.setup()
    renderAt('/settings/autonomy?env=live')
    await user.click(await screen.findByRole('radio', { name: 'Auto within limits' }))
    const sheet = await screen.findByRole('dialog')
    await user.click(within(sheet).getByRole('button', { name: 'Turn on level 3' }))
    await waitFor(() => expect(be.rows.live.settings.autonomy_level).toBe(3))
    expect(be.patches.at(-1)?.env).toBe('live')
    expect(be.rows.demo.settings.autonomy_level).toBe(2)
  })
})

describe('First live month on Autonomy and Money limits (ATL-240)', () => {
  it.each(['en', 'ar'] as const)('live autonomy 3 and 4 are disabled with the end date (%s)', async (lang) => {
    await i18n.changeLanguage(lang)
    const be = fakeBackend({ firstMonth: true })
    const user = userEvent.setup()
    renderAt('/settings/autonomy?env=live')
    const name3 = lang === 'en' ? 'Auto within limits' : i18n.t('au.l3', { ns: 'onboarding' })
    const name4 = i18n.t('au.l4', { ns: 'onboarding' })
    const l3 = await screen.findByRole('radio', { name: name3 })
    const l4 = screen.getByRole('radio', { name: name4 })
    expect(l3).toHaveAttribute('aria-disabled', 'true')
    expect(l4).toHaveAttribute('aria-disabled', 'true')
    const date = dayText(ENDS, lang, true)
    if (lang === 'en') expect(date).toBe('December 2')
    expect(l3).toHaveAccessibleDescription(expect.stringContaining(i18n.t('firstMonth', { ns: 'settings-autonomy', date })))
    expect(l4).toHaveAccessibleDescription(expect.stringContaining(i18n.t('firstMonth', { ns: 'settings-autonomy', date })))
    expect(screen.getByText(i18n.t('firstMonthNote', { ns: 'settings-autonomy', date }))).toBeInTheDocument()
    await user.click(l3)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(be.patches).toEqual([])
    // 1 and 2 still save.
    await user.click(screen.getByRole('radio', { name: lang === 'en' ? 'Advise only' : i18n.t('au.l1', { ns: 'onboarding' }) }))
    await waitFor(() => expect(be.patches).toEqual([{ env: 'live', version: 1, changes: { autonomy_level: 1 } }]))
  })

  it('the demo row is never limited by the live month', async () => {
    fakeBackend({ firstMonth: true })
    renderAt('/settings/autonomy?env=demo')
    expect(await screen.findByRole('radio', { name: 'Auto within limits' })).not.toHaveAttribute('aria-disabled')
  })

  it('Money limits marks the live cap as required: no Remove cap, and an empty cap cannot be saved', async () => {
    const be = fakeBackend({ firstMonth: true })
    be.rows.live.settings.bot_capital_cap_usdt = '10000'
    const user = userEvent.setup()
    renderAt('/settings/money?env=live')
    const cap = await screen.findByRole('textbox', { name: /^Bot capital cap/ })
    expect(screen.getByText(/Required before switching to live, and for the first 30 days after/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove cap' })).not.toBeInTheDocument()
    await user.clear(cap)
    expect(await screen.findByTestId('error-bot_capital_cap_usdt')).toHaveTextContent('A bot capital cap is required until December 2.')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
    expect(be.patches).toEqual([])
  })

  it('outside the first month the live cap can still be removed', async () => {
    const be = fakeBackend()
    be.rows.live.settings.bot_capital_cap_usdt = '10000'
    renderAt('/settings/money?env=live')
    await screen.findByRole('textbox', { name: /^Bot capital cap/ })
    expect(screen.queryByText(/Required before switching to live/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('error-bot_capital_cap_usdt')).not.toBeInTheDocument()
  })
})
