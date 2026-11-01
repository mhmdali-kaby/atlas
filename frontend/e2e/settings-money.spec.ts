import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-130: Settings → Money limits against a faked Laravel (ATL-124 settings, ATL-129 risk preview). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-130'

const BALANCED = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }

const CONNECTION = {
  id: 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30', exchange: 'okx', env: 'demo', label: 'OKX demo', api_key_last4: 'ab12', has_secret: true,
  has_passphrase: true, permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123',
  last_verified_at: '2026-10-28T08:00:00Z', last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
}

/** ATL-129's answer for equity $5,068,412.56 and a $100,000 cap: 1% risk, the smallest of three caps. */
function preview(perTrade: string, daily: string) {
  const candidates: [string, number][] = [
    ['max_position_pct', 5000],
    ['max_usdt_per_trade', Number(perTrade)],
    ['max_new_exposure_per_day', Number(daily) - 3000],
  ]
  const [key, use] = candidates.reduce((a, b) => (b[1] < a[1] ? b : a))
  return {
    env: 'demo', settings_version: 6, equity_usd: '5068412.56', equity_as_of: new Date(Date.now() - 60_000).toISOString(),
    sizing_equity_usd: '100000.00', cap_binds: true, max_risk_usd: '1000.00', risk_binding_limit: 'max_loss_per_trade_pct',
    max_notional_usd: use.toFixed(2), notional_binding_limit: key, used_exposure_today_usd: '3000.00', trades_today: 1, reason: null,
  }
}

async function fakeApi(page: Page) {
  const settings: Record<string, string | number | null> = {
    risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1,
    bot_capital_cap_usdt: '100000', max_usdt_per_trade: '3000', max_new_exposure_per_day_usdt: '25000', max_trades_per_day: 10,
  }
  const history = [
    { id: 'h6', version: 6, diff: { max_usdt_per_trade: { from: '5000', to: '3000' } }, changed_by: null, created_at: '2026-10-28T08:05:00Z' },
    { id: 'h5', version: 5, diff: { bot_capital_cap_usdt: { from: null, to: '100000' } }, changed_by: null, created_at: '2026-10-28T07:40:00Z' },
  ]
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
    if (url.pathname === '/api/settings/history') return json(200, { data: history })
    if (url.pathname === '/api/settings/risk-preview') {
      const q = url.searchParams
      return json(200, { data: preview(q.get('max_usdt_per_trade') ?? String(settings.max_usdt_per_trade), q.get('max_new_exposure_per_day_usdt') ?? String(settings.max_new_exposure_per_day_usdt)) })
    }
    if (url.pathname === '/api/settings' && method === 'GET')
      return json(200, { data: { id: '9f6c', env: 'demo', version: 6, settings, presets: PRESETS, max_enabled_autonomy_level: 2, updated_at: '2026-10-28T08:05:00Z' } })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
  await fakeApi(page)
  await page.goto('/settings/money')
  await expect(page.getByTestId('ml-sentence')).toContainText('$3,000.00')
}

async function axe(page: Page, selector: string) {
  // .st-version is the shared Settings version pill (settings-risk/risk.css), 4.45:1 in light mode on
  // every Settings page; reported separately, it is not part of this page.
  const results = await new AxeBuilder({ page }).include(selector).exclude('.st-version').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('Arabic: the page is right-to-left and every stepper sits at the logical end of its row', async ({ page }) => {
  await open(page, 'ar', 'light')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.getByTestId('ml-sentence')).toContainText('سقف رأس المال')
  for (const field of ['bot_capital_cap_usdt', 'max_usdt_per_trade', 'max_new_exposure_per_day_usdt', 'max_trades_per_day']) {
    const row = page.locator(`.ml-row[data-field="${field}"]`)
    const [stepper, main, rowBox] = [await row.locator('.st-stepper').boundingBox(), await row.locator('.ml-row-main').boundingBox(), await row.boundingBox()]
    // Inline end is the left in RTL: the stepper is left of the label column, near the row's left edge.
    expect(stepper!.x + stepper!.width).toBeLessThanOrEqual(main!.x + 1)
    expect(stepper!.x - rowBox!.x).toBeLessThan(24)
    // The figure inside the field stays left-to-right.
    await expect(row.locator('.st-stepper-field')).toHaveAttribute('dir', 'ltr')
  }
  // The cut line moves from the inline start (the right in Arabic).
  const cut = page.getByTestId('ml-cut')
  const binding = cut.locator('[data-binding="true"] .ml-bar-a')
  const bar = cut.locator('[data-binding="true"] .ml-bar')
  const [a, b] = [await binding.boundingBox(), await bar.boundingBox()]
  expect(Math.abs(a!.x + a!.width - (b!.x + b!.width))).toBeLessThan(1.5)
  expect(await axe(page, '.ml-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/money-ar-light.png`, fullPage: true })
})

test('dark mode: the preview text meets 4.5:1 (axe color-contrast) in English and Arabic', async ({ page }) => {
  for (const lang of ['en', 'ar'] as const) {
    await open(page, lang, 'dark')
    await expect(page.locator('html')).toHaveClass(/dark/)
    await page.waitForTimeout(400)
    const contrast = await new AxeBuilder({ page }).include('[data-testid="ml-preview"]').withRules(['color-contrast']).analyze()
    expect(contrast.violations.flatMap((v) => v.nodes.map((n) => n.target.join(' ')))).toEqual([])
    expect(contrast.passes.find((p) => p.id === 'color-contrast')?.nodes.length ?? 0).toBeGreaterThan(5)
    expect(await axe(page, '.ml-page')).toEqual([])
    await page.screenshot({ path: `${SHOTS}/money-${lang}-dark.png`, fullPage: true })
  }
})

test('English light: typing a new max per trade moves the cut line and the badge', async ({ page }) => {
  await open(page, 'en', 'light')
  const use = page.getByTestId('ml-line-use')
  await expect(use).toHaveAttribute('data-binding', 'max_usdt_per_trade')
  const input = page.getByRole('textbox', { name: /^Max per trade/ })
  await input.fill('8000')
  await expect(use).toHaveAttribute('data-binding', 'max_position_pct')
  await expect(page.getByTestId('ml-use')).toHaveText('$5,000.00')
  await expect(page.getByTestId('ml-note-profile')).toBeVisible()
  await expect(page.getByTestId('save-bar')).toContainText('1 unsaved change')
  expect(await axe(page, '.ml-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/money-en-light-draft.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.screenshot({ path: `${SHOTS}/money-en-light-390.png`, fullPage: true })
})
