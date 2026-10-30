import { expect, test, type Page } from '@playwright/test'

/* ATL-118: Settings → Agents against a faked Laravel (ATL-113's PATCH /api/settings shapes). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-118'

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

const ROSTER = ['orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst', 'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager']
const STRONG = new Set(['market_scout', 'news_intelligence', 'historian', 'strategist', 'devils_advocate'])
const ENABLED = new Set(['market_scout', 'news_intelligence', 'historian'])

type Val = string | number | boolean | string[]
type Diff = Record<string, { from: unknown; to: unknown }>
const MESSAGE = 'Each entry must be a bare hostname such as "coindesk.com", with no scheme, port or path.'

async function fakeApi(page: Page) {
  const settings: Record<string, Val> = { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, onboarding_completed: 1 }
  for (const s of ROSTER) {
    settings[`agents.${s}.enabled`] = ENABLED.has(s)
    settings[`agents.${s}.model`] = STRONG.has(s) ? 'strong' : 'standard'
    settings[`agents.${s}.daily_tokens`] = 3_000_000
    settings[`agents.${s}.daily_turns`] = 300
  }
  settings['agents.web.allow_domains'] = []
  settings['agents.web.deny_domains'] = ['x.com']
  const state = {
    version: 4,
    settings,
    patches: [] as Record<string, unknown>[],
    history: [
      { id: 'h4', version: 4, diff: { 'agents.web.deny_domains': { from: [], to: ['x.com'] } } as Diff, changed_by: null, created_at: '2026-10-28T09:00:00Z' },
      { id: 'h3', version: 3, diff: { 'agents.news_intelligence.enabled': { from: false, to: true } } as Diff, changed_by: null, created_at: '2026-10-28T08:30:00Z' },
    ],
  }
  const agents = ROSTER.map((slug) => ({ slug, today: { tokens: slug === 'news_intelligence' ? 2_700_000 : 620_000, turns: 22 } }))
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    const doc = () => ({ data: { id: '9f6c', env: 'demo', version: state.version, settings: state.settings, presets: PRESETS, updated_at: '2026-10-28T09:00:00Z' } })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
    if (url.pathname === '/api/agents') return json(200, { data: agents })
    if (url.pathname === '/api/settings/history') return json(200, { data: state.history })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as { version: number; changes: Record<string, Val> }
      state.patches.push(body.changes)
      if (body.version !== state.version) return json(409, { message: 'stale', current_version: state.version })
      const errors: Record<string, string[]> = {}
      for (const list of ['agents.web.allow_domains', 'agents.web.deny_domains']) {
        const v = body.changes[list]
        if (Array.isArray(v)) v.forEach((h, i) => { if (/[\s/:@?#*%\\[\]]/.test(h)) errors[`changes.${list}.${i}`] = [MESSAGE] })
      }
      if (Object.keys(errors).length) return json(422, { message: MESSAGE, errors })
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

async function open(page: Page, lang: 'en' | 'ar' = 'en', theme: 'light' | 'dark' = 'light') {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
  const api = await fakeApi(page)
  await page.goto('/settings/agents')
  await expect(page.getByTestId('settings-version')).toBeVisible()
  return api
}

test('turning Market Scout off and saving sends agents.market_scout.enabled=false and shows the new version', async ({ page }) => {
  const api = await open(page)
  await expect(page.getByTestId('settings-version')).toHaveText('Version 4')
  const sw = page.getByRole('switch', { name: 'Use Market Scout' })
  await expect(sw).toBeChecked()
  await sw.click()
  await expect(sw).not.toBeChecked()
  await expect(page.getByTestId('agent-market_scout')).toContainText('Off. These settings apply when you turn Market Scout on.')
  await expect(page.getByTestId('save-bar')).toContainText('1 unsaved change')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await expect(page.getByTestId('settings-version')).toHaveText('Version 5')
  await expect(page.getByText('Saved as version 5', { exact: true })).toBeVisible()
  expect(api.patches).toEqual([{ 'agents.market_scout.enabled': false }])
  expect(api.settings['agents.market_scout.enabled']).toBe(false)
  await expect(page.getByTestId('save-bar')).toHaveCount(0)
  await expect(page.getByTestId('agents-activity').locator('li').first()).toContainText('Turned off Market Scout')
})

test('"http://x/" in blocked sites shows the server’s error inline and does not save', async ({ page }) => {
  const api = await open(page)
  const input = page.getByRole('textbox', { name: 'Blocked sites' })
  await input.fill('http://x/')
  await input.press('Enter')
  await expect(page.getByTestId('list-deny').locator('.sa-chip')).toHaveCount(2)
  await page.getByRole('button', { name: 'Save changes' }).click()

  const error = page.getByTestId('error-deny')
  await expect(error).toHaveText(MESSAGE)
  await expect(page.getByTestId('list-deny').locator('.sa-chip.is-invalid')).toHaveText('http://x/')
  await expect(page.getByRole('button', { name: 'Remove http://x/' })).toBeFocused()
  await expect(page.getByTestId('save-bar')).toContainText('Fix 1 field to save')
  await expect(page.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  expect(api.version).toBe(4)
  expect(api.settings['agents.web.deny_domains']).toEqual(['x.com'])
  await page.screenshot({ path: `${SHOTS}/deny-422-en-light.png`, fullPage: true })

  // Removing the refused entry clears the error and the save goes through with the rest.
  await page.keyboard.press('Delete')
  await expect(error).toHaveCount(0)
  await expect(page.getByTestId('save-bar')).toHaveCount(0)
})

test('adding and removing a site works with the keyboard alone, with visible focus', async ({ page }) => {
  const api = await open(page)
  const input = page.getByRole('textbox', { name: 'Allowed sites' })
  for (let i = 0; i < 200 && !(await input.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press('Tab')
  await expect(input).toBeFocused()
  const ring = (sel: string) => page.locator(sel).evaluate((el) => getComputedStyle(el).boxShadow)
  expect(await ring('[data-testid="list-allow"] .sa-tokens')).toMatch(/rgb/)

  await page.keyboard.type('CoinDesk.com')
  await page.keyboard.press('Enter')
  await page.keyboard.type('reuters.com.')
  await page.keyboard.press('Enter')
  const chips = page.getByTestId('list-allow').locator('.sa-chip')
  await expect(chips).toHaveText(['coindesk.com', 'reuters.com'])
  await expect(input).toBeFocused()
  await expect(input).toHaveValue('')

  // Backspace in the empty input moves to the last chip; its ring shows.
  await page.keyboard.press('Backspace')
  const last = page.getByRole('button', { name: 'Remove reuters.com' })
  await expect(last).toBeFocused()
  expect(await chips.nth(1).evaluate((el) => getComputedStyle(el).boxShadow)).toMatch(/rgb/)
  await page.screenshot({ path: `${SHOTS}/chip-focus-en-light.png` })

  // Arrow back to the first chip and remove it with Delete: focus stays on a chip.
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('button', { name: 'Remove coindesk.com' })).toBeFocused()
  await page.keyboard.press('Delete')
  await expect(chips).toHaveText(['reuters.com'])
  await expect(page.getByRole('button', { name: 'Remove reuters.com' })).toBeFocused()

  // Tab from the input reaches Add, then Tab on to Save; Enter saves.
  const save = page.getByRole('button', { name: 'Save changes' })
  for (let i = 0; i < 200 && !(await save.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('settings-version')).toHaveText('Version 5')
  expect(api.patches).toEqual([{ 'agents.web.allow_domains': ['reuters.com'] }])
})

for (const theme of ['light', 'dark'] as const) {
  test(`Arabic RTL ${theme}: the section is mirrored with no clipped controls`, async ({ page }) => {
    await open(page, 'ar', theme)
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
    expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(theme === 'dark')

    const scout = page.getByTestId('agent-market_scout')
    const sw = scout.getByRole('switch', { name: 'تشغيل مستكشف السوق' })
    const name = scout.locator('.sa-name')
    const [swBox, nameBox] = [await sw.boundingBox(), await name.boundingBox()]
    // Mirrored: the switch sits at the inline end, which is the left in RTL.
    expect(swBox && nameBox && swBox.x + swBox.width <= nameBox.x).toBe(true)

    const input = page.getByRole('textbox', { name: 'المواقع المحظورة' })
    await input.fill('reddit.com')
    await input.press('Enter')
    await page.getByRole('switch', { name: 'تشغيل مستكشف السوق' }).click()

    // Nothing overflows its box: every control inside the page column and the viewport.
    const clipped = await page.evaluate(() => {
      const col = document.querySelector('.sa-page')!.getBoundingClientRect()
      const bad: string[] = []
      for (const el of document.querySelectorAll<HTMLElement>('.sa-page [role="switch"], .sa-page [role="radiogroup"], .sa-page .st-stepper, .sa-page .sa-tokens, .sa-page .sa-chip, .sa-page button')) {
        const r = el.getBoundingClientRect()
        if (r.width === 0) continue
        if (r.left < col.left - 1 || r.right > col.right + 1 || el.scrollWidth > el.clientWidth + 1) bad.push(el.outerHTML.slice(0, 80))
      }
      return bad
    })
    expect(clipped).toEqual([])
    await page.screenshot({ path: `${SHOTS}/agents-ar-${theme}.png`, fullPage: true })
    await page.getByTestId('web-access').screenshot({ path: `${SHOTS}/web-ar-${theme}.png` })
  })
}

test('English dark full page', async ({ page }) => {
  await open(page, 'en', 'dark')
  await page.screenshot({ path: `${SHOTS}/agents-en-dark.png`, fullPage: true })
})
