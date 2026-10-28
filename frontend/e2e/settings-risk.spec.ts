import { expect, test, type Page } from '@playwright/test'

/* ATL-21: Settings → Risk & Autonomy by keyboard alone, in English/Arabic × light/dark. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-21'

const PRESETS = {
  conservative: { max_loss_per_trade_pct: '0.5', max_position_pct: '2', max_daily_loss_pct: '2', max_weekly_loss_pct: '5', max_leverage: 2, max_open_positions: 3, max_share_per_instrument_pct: '15', max_correlated_exposure_pct: '20', min_expected_r: '2.0', max_consecutive_losses: 2 },
  balanced: { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 },
  aggressive: { max_loss_per_trade_pct: '2.0', max_position_pct: '10', max_daily_loss_pct: '8', max_weekly_loss_pct: '20', max_leverage: 10, max_open_positions: 10, max_share_per_instrument_pct: '40', max_correlated_exposure_pct: '50', min_expected_r: '1.5', max_consecutive_losses: 4 },
} as const

const CONNECTION = {
  id: 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30', exchange: 'okx', env: 'demo', label: 'OKX demo', api_key_last4: 'ab12', has_secret: true,
  has_passphrase: true, permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123',
  last_verified_at: '2026-10-28T08:00:00Z', last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
}

type Diff = Record<string, { from: unknown; to: unknown }>

async function fakeApi(page: Page) {
  const state = {
    version: 2,
    settings: { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, onboarding_completed: 1 } as Record<string, string | number>,
    history: [
      { id: 'h2', version: 2, diff: { onboarding_completed: { from: 0, to: 1 } } as Diff, changed_by: null, created_at: '2026-10-28T09:00:00Z' },
      { id: 'h1', version: 1, diff: { risk_profile: { from: null, to: 'balanced' } } as Diff, changed_by: null, created_at: '2026-10-28T08:00:00Z' },
    ],
  }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    const doc = () => ({ data: { id: '9f6c', env: 'demo', version: state.version, settings: state.settings, presets: PRESETS, updated_at: '2026-10-28T08:00:00Z' } })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
    if (url.pathname === '/api/settings/history') return json(200, { data: state.history })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as { version: number; changes: Record<string, string | number> }
      if (body.version !== state.version) return json(409, { message: 'stale', current_version: state.version })
      const before = state.settings
      state.settings = { ...before, ...body.changes }
      const diff: Diff = {}
      for (const k of Object.keys(body.changes)) diff[k] = { from: before[k], to: state.settings[k] }
      state.version += 1
      state.history.unshift({ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: '2026-10-28T14:30:00Z' })
      return json(200, doc())
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

const T = {
  en: { leverage: 'Max leverage', custom: 'Custom', save: 'Save changes', change: 'Max leverage 5× → 3×', risk: 'Risk profile' },
  ar: { leverage: 'أقصى رافعة', custom: 'مخصّص', save: 'حفظ التغييرات', change: '3×', risk: 'مستوى المخاطرة' },
}

/** Tab forward until the focused element matches, like an owner with no mouse. */
async function tabTo(page: Page, matches: () => Promise<boolean>, max = 80) {
  for (let i = 0; i < max; i++) {
    await page.keyboard.press('Tab')
    if (await matches()) return
  }
  throw new Error('never reached the target by Tab')
}

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'light'], ['ar', 'dark']] as const) {
  test(`edit a limit and save by keyboard alone in ${lang} ${theme}`, async ({ page }) => {
    const t = T[lang]
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.addInitScript(([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    }, [lang, theme] as const)
    const api = await fakeApi(page)
    await page.goto('/settings/risk')
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(theme === 'dark')

    const leverage = page.getByRole('textbox', { name: t.leverage, exact: true })
    await expect(leverage).toHaveValue('5')
    await page.screenshot({ path: `${SHOTS}/risk-${lang}-${theme}.png`, fullPage: true })

    // The unit sits inside its field, after the number, in both directions.
    const unit = leverage.locator('xpath=following-sibling::span[1]')
    await expect(unit).toHaveText('×')
    const [fieldBox, unitBox] = [await leverage.boundingBox(), await unit.boundingBox()]
    expect(unitBox && fieldBox && unitBox.x > fieldBox.x + fieldBox.width / 2 && unitBox.x + unitBox.width <= fieldBox.x + fieldBox.width).toBe(true)

    await tabTo(page, () => leverage.evaluate((el) => el === document.activeElement))
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await expect(leverage).toHaveValue('3')
    await expect(page.getByRole('radiogroup', { name: t.risk }).getByRole('radio', { name: t.custom })).toHaveAttribute('aria-checked', 'true')

    const save = page.getByRole('button', { name: t.save })
    await tabTo(page, () => save.evaluate((el) => el === document.activeElement))
    await expect(save).toBeFocused()
    await page.screenshot({ path: `${SHOTS}/risk-${lang}-${theme}-unsaved.png` })
    await page.keyboard.press('Enter')

    await expect(page.locator('[data-testid="activity"] li').first()).toHaveAttribute('data-version', '3')
    await expect(page.locator('[data-testid="activity"] li').first()).toContainText(t.change)
    expect(api.version).toBe(3)
    expect(api.settings.max_leverage).toBe(3)
    await expect(page.getByTestId('save-bar')).toHaveCount(0)
  })
}

test('server validation error under the field in Arabic RTL, dark', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'ar')
    localStorage.setItem('atlas.theme', 'dark')
  })
  await fakeApi(page)
  await page.route(/\/api\/settings$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const msg = 'The max_loss_per_trade_pct must be greater than 0 and at most 100.'
    return route.fulfill({
      status: 422,
      contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' },
      body: JSON.stringify({ message: msg, errors: { 'changes.max_loss_per_trade_pct': [msg] } }),
    })
  })
  await page.goto('/settings/risk')
  const loss = page.getByRole('textbox', { name: 'أقصى خسارة للصفقة' })
  await loss.fill('0')
  await page.getByRole('button', { name: 'حفظ التغييرات' }).click()
  const error = page.getByTestId('error-max_loss_per_trade_pct')
  await expect(error).toBeVisible()
  await expect(loss).toBeFocused()
  await error.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${SHOTS}/risk-ar-dark-invalid.png` })
})
