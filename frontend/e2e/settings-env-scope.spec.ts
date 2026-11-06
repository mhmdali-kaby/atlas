import { expect, test, type Page } from '@playwright/test'

/* ATL-242: "Settings for: Demo | Live" on Risk, Money limits, Autonomy and Markets, against a faked
   Laravel with one settings row per env (ATL-238's first-month 422 on the live row). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-242'

const BALANCED = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }
const CONSERVATIVE = { max_loss_per_trade_pct: '0.5', max_position_pct: '2', max_daily_loss_pct: '2', max_weekly_loss_pct: '5', max_leverage: 2, max_open_positions: 3, max_share_per_instrument_pct: '15', max_correlated_exposure_pct: '20', min_expected_r: '2.0', max_consecutive_losses: 2 }
const AGGRESSIVE = { max_loss_per_trade_pct: '2.0', max_position_pct: '10', max_daily_loss_pct: '8', max_weekly_loss_pct: '20', max_leverage: 10, max_open_positions: 10, max_share_per_instrument_pct: '40', max_correlated_exposure_pct: '50', min_expected_r: '1.5', max_consecutive_losses: 4 }
const PRESETS = { conservative: CONSERVATIVE, balanced: BALANCED, aggressive: AGGRESSIVE }

type Env = 'demo' | 'live'
type Lang = 'en' | 'ar'
interface Patch { env: Env; version: number; changes: Record<string, unknown> }

const ENDS = '2026-12-02T07:12:00.000000Z'
const REFUSED: Record<Lang, string> = {
  en: 'During the first live month (until 2026-12-02 10:12) live autonomy can be at most level 2.',
  ar: 'خلال الشهر الأول على الحقيقي (حتى 2026-12-02 10:12) يمكن أن تكون استقلالية الحقيقي المستوى 2 على الأكثر.',
}
const T = {
  en: { scope: 'Settings for', demo: 'Demo', live: 'Live', note: 'You’re editing the live settings. They apply once Atlas switches to live in Settings → Environment.' },
  ar: { scope: 'إعدادات', demo: 'تجريبي', live: 'حقيقي', note: 'أنت تعدّل إعدادات الحساب الحقيقي. تُطبَّق بعد أن ينتقل أطلس إلى الحقيقي من الإعدادات ← البيئة.' },
}

const initial = () => ({
  risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, autonomy_ask_above_usd: '1000', onboarding_completed: 1,
  bot_capital_cap_usdt: null as string | null, max_usdt_per_trade: '5000', max_new_exposure_per_day_usdt: '25000', max_trades_per_day: 10,
  max_instruments_per_day: 5, whitelist: [] as string[], blocklist: [] as string[],
}) as Record<string, unknown>

async function fakeApi(page: Page, lang: Lang, opts: { firstMonth?: boolean } = {}) {
  const rows: Record<Env, { version: number; settings: Record<string, unknown> }> = {
    demo: { version: 7, settings: initial() },
    live: { version: 1, settings: initial() },
  }
  const state = { rows, patches: [] as Patch[], gets: [] as Env[] }
  const doc = (env: Env) => ({
    id: `s-${env}`, env, version: rows[env].version, settings: rows[env].settings, presets: PRESETS, max_enabled_autonomy_level: 4,
    first_live_month_ends_at: env === 'live' && opts.firstMonth ? ENDS : null, max_live_autonomy_level: 2, updated_at: '2026-10-28T08:05:00Z',
  })
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    const env = (url.searchParams.get('env') ?? 'demo') as Env
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: { ...OWNER, locale: lang } })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/settings/history') return json(200, { data: [], meta: { total: 0 } })
    if (url.pathname === '/api/instruments') return json(200, { data: [] })
    if (url.pathname === '/api/settings/markets/eligibility')
      return json(200, { data: { env, as_of: new Date().toISOString(), settings_version: rows[env].version, draft: [], instruments: [] } })
    if (url.pathname === '/api/settings/risk-preview')
      return json(200, {
        data: {
          env, settings_version: rows[env].version, equity_usd: '100000.00', equity_as_of: new Date(Date.now() - 60_000).toISOString(),
          sizing_equity_usd: '100000.00', cap_binds: false, max_risk_usd: '1000.00', risk_binding_limit: 'max_loss_per_trade_pct',
          max_notional_usd: '5000.00', notional_binding_limit: 'max_usdt_per_trade', used_exposure_today_usd: '0.00', trades_today: 0, reason: null,
        },
      })
    if (url.pathname === '/api/settings' && method === 'GET') {
      state.gets.push(env)
      return json(200, { data: doc(env) })
    }
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = req.postDataJSON() as Patch
      state.patches.push(body)
      const row = rows[body.env]
      if (body.env === 'live' && opts.firstMonth && Number(body.changes.autonomy_level ?? 0) > 2)
        return json(422, { message: REFUSED[lang], errors: { 'changes.autonomy_level': [REFUSED[lang]] } })
      const { risk_profile, ...rest } = body.changes
      if (typeof risk_profile === 'string' && risk_profile !== 'custom') Object.assign(row.settings, PRESETS[risk_profile as keyof typeof PRESETS])
      if (risk_profile !== undefined) row.settings.risk_profile = risk_profile
      Object.assign(row.settings, rest)
      row.version += 1
      return json(200, { data: doc(body.env) })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, path: string, lang: Lang, opts: { firstMonth?: boolean } = {}) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.addInitScript((l) => localStorage.setItem('atlas.lang', l), lang)
  const state = await fakeApi(page, lang, opts)
  await page.goto(path)
  return state
}

const scope = (page: Page, lang: Lang) => page.getByRole('radiogroup', { name: T[lang].scope })
const levels = (page: Page) => page.getByRole('radiogroup', { name: /^(Autonomy|الاستقلالية)$/ })
const SAVE: Record<Lang, string> = { en: 'Save changes', ar: 'حفظ التغييرات' }
const CONFIRM3: Record<Lang, string> = { en: 'Turn on level 3', ar: 'تفعيل المستوى 3' }
const PANES = ['risk', 'money', 'autonomy', 'markets'] as const

for (const lang of ['en', 'ar'] as const) {
  test.describe(`Settings for: Demo | Live (${lang})`, () => {
    for (const pane of PANES) {
      test(`${pane}: no param shows Demo with no note; ?env=live shows Live and the note`, async ({ page }) => {
        const state = await open(page, `/settings/${pane}`, lang)
        const group = scope(page, lang)
        await expect(group.getByRole('radio', { name: T[lang].demo })).toHaveAttribute('aria-checked', 'true')
        await expect(page.getByTestId('env-scope-notice')).toHaveCount(0)
        expect(state.gets).not.toContain('live')

        await page.goto(`/settings/${pane}?env=live`)
        await expect(group.getByRole('radio', { name: T[lang].live })).toHaveAttribute('aria-checked', 'true')
        await expect(page.getByTestId('env-scope-notice')).toHaveText(T[lang].note)
        await expect.poll(() => state.gets.includes('live')).toBe(true)
        if (pane === 'risk') await page.screenshot({ path: `${SHOTS}/risk-live-${lang}.png`, fullPage: true })

        await group.getByRole('radio', { name: T[lang].demo }).click()
        await expect(page.getByTestId('env-scope-notice')).toHaveCount(0)
        await expect(page).toHaveURL(/env=demo/)
      })
    }

    test('with Live selected, a risk profile, a bot capital cap and autonomy 2 change only the live row', async ({ page }) => {
      const state = await open(page, '/settings/risk?env=live', lang)
      state.rows.live.settings.autonomy_level = 3
      const demoBefore = JSON.stringify(state.rows.demo)

      await page.locator('.ob-profile-card[data-value="conservative"]').click()
      await page.getByRole('button', { name: SAVE[lang] }).click()
      await expect.poll(() => state.patches.length).toBe(1)
      expect(state.patches[0]).toMatchObject({ env: 'live', changes: { risk_profile: 'conservative' } })

      await page.goto('/settings/money?env=live')
      const cap = page.getByRole('textbox', { name: lang === 'en' ? /^Bot capital cap/ : /^سقف رأس المال/ })
      await cap.fill('10000')
      await page.getByRole('button', { name: SAVE[lang] }).click()
      await expect.poll(() => state.patches.length).toBe(2)
      expect(state.patches[1]).toEqual({ env: 'live', version: 2, changes: { bot_capital_cap_usdt: '10000' } })

      await page.goto('/settings/autonomy?env=live')
      await levels(page).locator('[role="radio"][data-value="2"]').click()
      await expect.poll(() => state.patches.length).toBe(3)
      expect(state.patches[2]).toEqual({ env: 'live', version: 3, changes: { autonomy_level: 2 } })

      expect(state.rows.live.settings).toMatchObject({ risk_profile: 'conservative', bot_capital_cap_usdt: '10000', autonomy_level: 2 })
      expect(JSON.stringify(state.rows.demo)).toBe(demoBefore)
    })

    test('during the first live month, live autonomy 3 is refused with the API message', async ({ page }) => {
      const state = await open(page, '/settings/autonomy?env=live', lang, { firstMonth: true })
      await levels(page).locator('[role="radio"][data-value="3"]').click()
      const sheet = page.getByRole('dialog')
      await sheet.getByRole('button', { name: CONFIRM3[lang] }).click()
      await expect(sheet.getByTestId('au-sheet-refused')).toContainText(REFUSED[lang])
      await page.screenshot({ path: `${SHOTS}/autonomy-refused-${lang}.png` })
      expect(state.rows.live.settings.autonomy_level).toBe(2)
    })

    test('with no first month running, live autonomy 3 saves', async ({ page }) => {
      const state = await open(page, '/settings/autonomy?env=live', lang)
      await levels(page).locator('[role="radio"][data-value="3"]').click()
      const sheet = page.getByRole('dialog')
      await sheet.getByRole('button', { name: CONFIRM3[lang] }).click()
      await expect(sheet).toBeHidden()
      expect(state.patches.at(-1)).toMatchObject({ env: 'live', changes: { autonomy_level: 3 } })
      expect(state.rows.demo.settings.autonomy_level).toBe(2)
    })
  })
}
