import { expect, test, type Page } from '@playwright/test'

/* ATL-19: onboarding steps 3–4 (risk profile, autonomy) and Finish → /portfolio. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-19'

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

/** A fake Laravel: auth, a verified demo connection and GET/PATCH /api/settings with the version check. */
async function fakeApi(page: Page) {
  const state = {
    version: 1,
    settings: { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, onboarding_completed: 0 } as Record<string, string | number>,
    patches: [] as unknown[],
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
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as { version: number; changes: Record<string, string | number> }
      state.patches.push(body)
      if (body.version !== state.version) return json(409, { message: 'stale', current_version: state.version })
      const profile = body.changes.risk_profile as keyof typeof PRESETS | undefined
      state.settings = { ...state.settings, ...(profile && profile in PRESETS ? PRESETS[profile] : {}), ...body.changes }
      state.version += 1
      return json(200, doc())
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function prefs(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
}

const T = {
  en: { risk: 'Risk profile', autonomy: 'Autonomy', conservative: 'Conservative', balanced: 'Balanced', compare: 'Compare all ten limits', later: 'Available in a later phase' },
  ar: { risk: 'مستوى المخاطرة', autonomy: 'الاستقلالية', conservative: 'متحفّظ', balanced: 'متوازن', compare: 'قارن الحدود العشرة كلها', later: 'متاح في مرحلة لاحقة' },
}

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'light'], ['ar', 'dark']] as const) {
  test(`risk and autonomy steps by keyboard alone in ${lang} ${theme}`, async ({ page }) => {
    const t = T[lang]
    await page.setViewportSize({ width: 1280, height: 900 })
    await prefs(page, lang, theme)
    const api = await fakeApi(page)

    // A verified owner who has not finished setup lands on the risk step.
    await page.goto('/')
    await expect(page).toHaveURL(/\/onboarding\?step=risk$/)
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(theme === 'dark')
    const profiles = page.getByRole('radiogroup', { name: t.risk })
    await expect(profiles.getByRole('radio')).toHaveCount(3)
    await expect(profiles.getByRole('radio', { name: t.balanced })).toHaveAttribute('aria-checked', 'true')
    await page.getByRole('button', { name: t.compare }).click()
    await expect(page.locator('section[data-step="risk"]')).toHaveCSS('opacity', '1')
    await page.screenshot({ path: `${SHOTS}/risk-${lang}-${theme}.png`, fullPage: true })

    // Keyboard only: focus the checked card, move to the previous one (Left in LTR, Right in RTL).
    await profiles.getByRole('radio', { name: t.balanced }).focus()
    await page.keyboard.press(lang === 'ar' ? 'ArrowRight' : 'ArrowLeft')
    await expect(profiles.getByRole('radio', { name: t.conservative })).toHaveAttribute('aria-checked', 'true')
    await expect(profiles.getByRole('radio', { name: t.conservative })).toBeFocused()
    // Tab out of the group to Continue (disclosure, then Back, then Continue), press Enter.
    await page.keyboard.press('Tab')
    await page.keyboard.press('Tab')
    await page.keyboard.press('Tab')
    await expect(page.getByTestId('ob-next')).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/step=autonomy$/)
    expect(api.settings.risk_profile).toBe('conservative')
    expect(api.version).toBe(2)
    const levels = page.getByRole('radiogroup', { name: t.autonomy })
    await expect(levels.getByRole('radio')).toHaveCount(4)
    await expect(levels.getByRole('radio', { checked: true })).toHaveAttribute('data-value', '2')
    await expect(levels.locator('[aria-disabled="true"]')).toHaveCount(2)
    await expect(levels.getByText(t.later)).toHaveCount(2)
    await expect(page.locator('section[data-step="autonomy"]')).toHaveCSS('opacity', '1')
    await page.screenshot({ path: `${SHOTS}/autonomy-${lang}-${theme}.png`, fullPage: true })

    // Down from level 2 skips the disabled 3 and 4 and wraps to 1.
    await levels.locator('[data-value="2"]').focus()
    await page.keyboard.press('ArrowDown')
    await expect(levels.locator('[data-value="1"]')).toHaveAttribute('aria-checked', 'true')
    await page.keyboard.press('Tab')
    await page.keyboard.press('Tab')
    await expect(page.getByTestId('ob-next')).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/\/portfolio$/)
    expect(api.settings.autonomy_level).toBe(1)
    expect(api.settings.onboarding_completed).toBe(1)
    await page.goto('/onboarding')
    await expect(page).toHaveURL(/\/portfolio$/)
  })
}

test('the risk step fits a narrow window', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await prefs(page, 'ar', 'light')
  await fakeApi(page)
  await page.goto('/onboarding?step=risk')
  await expect(page.getByRole('radiogroup', { name: T.ar.risk }).getByRole('radio')).toHaveCount(3)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: `${SHOTS}/risk-ar-narrow.png`, fullPage: true })
})
