import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-216: Settings → Autonomy, levels 3 and 4 and the "Ask me above" amount, against a faked Laravel (ATL-212 rules). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-216'

const BALANCED = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }
const CONNECTION = {
  id: 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30', exchange: 'okx', env: 'demo', label: 'OKX demo', api_key_last4: 'ab12', has_secret: true,
  has_passphrase: true, permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123',
  last_verified_at: '2026-10-28T08:00:00Z', last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
}
/** The API's own 422 text for a negative amount (lang/en/settings.php decimal_range_inclusive). */
const RANGE_MESSAGE = 'The changes.autonomy ask above usd must be between 0 and 1000000000000.'

interface Patch { env: string; version: number; changes: Record<string, unknown> }

async function fakeApi(page: Page) {
  const state = {
    version: 7,
    settings: { risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, autonomy_ask_above_usd: '1000', onboarding_completed: 1 } as Record<string, string | number>,
    patches: [] as Patch[],
    saved: [] as Patch[],
    history: [{ id: 'h7', version: 7, diff: { autonomy_level: { from: 1, to: 2 } }, changed_by: null, created_at: '2026-10-28T08:05:00Z' }] as unknown[],
  }
  const doc = () => ({ id: '9f6c', env: 'demo', version: state.version, settings: state.settings, presets: PRESETS, max_enabled_autonomy_level: 4, updated_at: '2026-10-28T08:05:00Z' })
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
    if (url.pathname === '/api/settings/history') return json(200, { data: state.history })
    if (url.pathname === '/api/settings/risk-preview')
      return json(200, {
        data: {
          env: 'demo', settings_version: state.version, equity_usd: '5068412.56', equity_as_of: new Date(Date.now() - 60_000).toISOString(),
          sizing_equity_usd: '100000.00', cap_binds: true, max_risk_usd: '1000.00', risk_binding_limit: 'max_loss_per_trade_pct',
          max_notional_usd: '3000.00', notional_binding_limit: 'max_usdt_per_trade', used_exposure_today_usd: '0.00', trades_today: 0, reason: null,
        },
      })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, { data: doc() })
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = req.postDataJSON() as Patch
      state.patches.push(body)
      if (body.version !== state.version) return json(409, { message: 'Settings changed.', current_version: state.version })
      const ask = body.changes.autonomy_ask_above_usd
      if (ask !== undefined && (typeof ask !== 'string' || !/^\d+(\.\d+)?$/.test(ask)))
        return json(422, { message: RANGE_MESSAGE, errors: { 'changes.autonomy_ask_above_usd': [RANGE_MESSAGE] } })
      const diff: Record<string, { from: unknown; to: unknown }> = {}
      for (const [k, v] of Object.entries(body.changes)) diff[k] = { from: state.settings[k] ?? null, to: v }
      Object.assign(state.settings, body.changes)
      state.version += 1
      state.saved.push(body)
      state.history.unshift({ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: new Date().toISOString() })
      return json(200, { data: doc() })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, lang: 'en' | 'ar' = 'en', theme: 'light' | 'dark' = 'light', width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
  const state = await fakeApi(page)
  await page.goto('/settings/autonomy')
  await expect(page.getByTestId('autonomy-now')).toBeVisible()
  return state
}

const levels = (page: Page) => page.getByRole('radiogroup', { name: /Autonomy|الاستقلالية/ })

test('level 3 with 250: the sheet saves autonomy_level 3 and "250", and a reload shows them', async ({ page }) => {
  const state = await open(page)
  await levels(page).locator('[data-value="3"]').click()
  const sheet = page.getByRole('dialog', { name: 'Let Atlas send trades without asking?' })
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('You won’t see an approval card for it')
  const field = sheet.getByRole('textbox', { name: 'Ask me above' })
  await expect(field).toBeFocused()
  await field.fill('250')
  await expect(sheet.getByTestId('au-sheet-sentence')).toContainText('Trades up to $250.00 go to OKX on their own')
  await sheet.getByRole('button', { name: 'Turn on level 3' }).click()
  await expect(sheet).toBeHidden()

  expect(state.saved).toHaveLength(1)
  expect(state.saved[0]?.changes).toEqual({ autonomy_level: 3, autonomy_ask_above_usd: '250' })
  await expect(page.getByText('Level 3 is on', { exact: true })).toBeVisible()
  // The approval window and the timeout behaviour are unchanged, read-only.
  await expect(page.getByText('Atlas rejects the trade')).toBeVisible()

  await page.reload()
  await expect(levels(page).locator('[data-value="3"]')).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('textbox', { name: 'Ask me above' })).toHaveValue('250')
  await expect(page.getByTestId('autonomy-sentence')).toContainText('$250.00')
  await expect(page.getByTestId('autonomy-now')).toHaveAttribute('data-level', '3')
  await page.screenshot({ path: `${SHOTS}/autonomy-en-light-level3.png`, fullPage: true })
})

test('level 4 then Cancel leaves the saved level unchanged', async ({ page }) => {
  const state = await open(page)
  await levels(page).locator('[data-value="4"]').click()
  const sheet = page.getByRole('dialog', { name: 'Let Atlas send every trade without asking?' })
  await expect(sheet).toBeVisible()
  await expect(sheet.getByRole('button', { name: 'Turn on level 4' })).toBeFocused()
  await page.screenshot({ path: `${SHOTS}/autonomy-en-light-sheet4.png` })
  await sheet.getByRole('button', { name: 'Cancel' }).click()
  await expect(sheet).toBeHidden()
  expect(state.patches).toHaveLength(0)
  await expect(levels(page).locator('[data-value="2"]')).toHaveAttribute('aria-checked', 'true')
  await expect(levels(page).locator('[data-value="4"]')).toHaveAttribute('aria-checked', 'false')
  await expect(levels(page).locator('[data-value="4"]')).toBeFocused()
  await page.reload()
  await expect(levels(page).locator('[data-value="2"]')).toHaveAttribute('aria-checked', 'true')
  expect(state.settings.autonomy_level).toBe(2)
})

test('-5 shows the API’s 422 message under the field and saves nothing', async ({ page }) => {
  const state = await open(page)
  await levels(page).locator('[data-value="3"]').click()
  const sheet = page.getByRole('dialog', { name: 'Let Atlas send trades without asking?' })
  const field = sheet.getByRole('textbox', { name: 'Ask me above' })
  await field.fill('-5')
  await sheet.getByRole('button', { name: 'Turn on level 3' }).click()
  await expect(sheet.getByTestId('ask-error')).toHaveText(RANGE_MESSAGE)
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await expect(field).toBeFocused()
  await expect(sheet).toBeVisible()
  expect(state.patches.at(-1)?.changes).toEqual({ autonomy_level: 3, autonomy_ask_above_usd: '-5' })
  expect(state.saved).toHaveLength(0)
  expect(state.settings.autonomy_level).toBe(2)
  expect(state.version).toBe(7)
  await page.screenshot({ path: `${SHOTS}/autonomy-en-light-422.png` })
})

test('Arabic dark at 390 px: no horizontal scroll, and axe reports no violations (page and sheet)', async ({ page }) => {
  await open(page, 'ar', 'dark', 390)
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(page.getByTestId('autonomy-sentence')).toContainText('تنتظر كل صفقة موافقتك')
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(await overflow()).toBeLessThanOrEqual(0)
  await page.waitForTimeout(400)
  // The pane itself. The shell's account-menu button (toolbar ⋯ at phone width) trips
  // label-content-name-mismatch on every page; it is reported separately, not part of this pane.
  const results = await new AxeBuilder({ page }).include('.au-page').analyze()
  expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
  await page.screenshot({ path: `${SHOTS}/autonomy-ar-dark-390.png`, fullPage: true })

  await levels(page).locator('[data-value="3"]').click()
  const sheet = page.getByRole('dialog', { name: 'هل تسمح لأطلس بإرسال الصفقات دون سؤالك؟' })
  await expect(sheet).toBeVisible()
  await page.waitForTimeout(400)
  expect(await overflow()).toBeLessThanOrEqual(0)
  const sheetAxe = await new AxeBuilder({ page }).include('[data-sheet]').analyze()
  expect(sheetAxe.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
  await page.screenshot({ path: `${SHOTS}/autonomy-ar-dark-390-sheet.png` })
})
