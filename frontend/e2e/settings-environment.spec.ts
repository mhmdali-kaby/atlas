import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-239: Settings → Environment, the TRADE LIVE switch, against a faked ATL-236 API. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-239'
const PASSWORD = 'correct horse'

type Key = 'live_connection' | 'demo_history' | 'live_risk_profile' | 'live_autonomy' | 'live_capital_cap' | 'ip_allowlist' | 'sub_account' | 'demo_reviewed'
const KEYS: Key[] = ['live_connection', 'demo_history', 'live_risk_profile', 'live_autonomy', 'live_capital_cap', 'ip_allowlist', 'sub_account', 'demo_reviewed']
const ATTESTED = new Set<Key>(['ip_allowlist', 'sub_account', 'demo_reviewed'])
const DETAIL: Record<Key, [string, string]> = {
  live_connection: ['Verified by OKX, with no withdraw permission.', 'No live OKX connection. Add one in Settings → Connections.'],
  demo_history: ['30 days of demo history.', '12 of 28 days of demo history.'],
  live_risk_profile: ['Live risk profile: Conservative.', 'No live risk profile yet. Choose one in Settings → Risk with Live selected.'],
  live_autonomy: ['Live autonomy is level 2.', 'Live autonomy is not set yet. Choose level 2 or lower.'],
  live_capital_cap: ['Live bot capital cap: 10000 USDT.', 'No live bot capital cap. Set one in Settings → Money limits with Live selected.'],
  ip_allowlist: ['Confirmed by the owner.', 'Confirm the IP allow-list.'],
  sub_account: ['Confirmed by the owner.', 'Confirm the sub-account.'],
  demo_reviewed: ['Confirmed by the owner.', 'Confirm you reviewed demo.'],
}
const conn = (env: 'demo' | 'live', uid: string) => ({
  id: `c-${env}`, exchange: 'okx', env, label: `OKX ${env}`, api_key_last4: 'ab12', has_secret: true, has_passphrase: true,
  permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: uid,
  last_verified_at: '2026-11-05T08:00:00Z', withdraw_off_verified_at: env === 'live' ? '2026-11-05T08:00:00Z' : null,
  last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
})

async function fakeApi(page: Page, failing: Key[] = []) {
  const state = { env: 'demo' as 'demo' | 'live', started: null as string | null, switches: [] as unknown[] }
  const ok = (k: Key) => !failing.includes(k)
  const doc = () => {
    const items = KEYS.map((key) => ({
      key, kind: ATTESTED.has(key) ? 'attested' : 'computed', ok: ok(key), detail: DETAIL[key][ok(key) ? 0 : 1],
      ...(ATTESTED.has(key) ? { confirmed_at: ok(key) ? '2026-11-05T18:40:00.000000Z' : null } : {}),
    }))
    const ends = state.started ? new Date(Date.parse(state.started) + 30 * 86_400_000).toISOString() : null
    return { data: { active_env: state.env, live_started_at: state.started, first_month_ends_at: ends, in_first_live_month: ends !== null, switched_at: state.started, confirmation_text: 'TRADE LIVE', preflight: { ok: items.every((i) => i.ok), items } } }
  }
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
    if (url.pathname === '/api/connections') return json(200, { data: [conn('demo', '4821'), ...(ok('live_connection') ? [conn('live', '9876')] : [])] })
    if (url.pathname === '/api/environment' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/environment/switch' && method === 'POST') {
      const body = req.postDataJSON() as Record<string, unknown>
      state.switches.push(body)
      if (body.target === 'demo') {
        state.env = 'demo'
        return json(200, doc())
      }
      if (body.password !== PASSWORD)
        return json(422, { message: 'The password is incorrect.', errors: { password: ['The password is incorrect.'] }, failing_items: [] })
      state.env = 'live'
      state.started = new Date().toISOString()
      return json(200, doc())
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, opts: { lang?: 'en' | 'ar'; theme?: 'light' | 'dark'; width?: number; failing?: Key[] } = {}) {
  await page.setViewportSize({ width: opts.width ?? 1280, height: 900 })
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [opts.lang ?? 'en', opts.theme ?? 'light'] as const)
  const state = await fakeApi(page, opts.failing)
  await page.goto('/settings/environment')
  await expect(page.getByTestId('env-tile-demo')).toBeVisible()
  return state
}

test('the sheet stays disabled until a password and exactly TRADE LIVE; a wrong password shows under it', async ({ page }) => {
  const state = await open(page)
  await page.getByRole('button', { name: 'Switch to live…' }).click()
  const sheet = page.getByRole('dialog', { name: /Switch Atlas to live\?/ })
  await expect(sheet).toBeVisible()
  const pw = sheet.getByLabel('Your Atlas password')
  const cf = sheet.getByLabel('Type TRADE LIVE to confirm')
  const go = sheet.getByRole('button', { name: 'Switch to live' })
  await expect(pw).toBeFocused()
  await expect(go).toBeDisabled()
  await pw.fill('wrong password')
  await cf.fill('trade live')
  await expect(go).toBeDisabled()
  await cf.fill('TRADE LIVE')
  await expect(go).toBeEnabled()
  await page.screenshot({ path: `${SHOTS}/sheet-en-light.png` })
  await go.click()
  await expect(sheet.getByText('The password is incorrect.')).toBeVisible()
  await expect(pw).toHaveAttribute('aria-invalid', 'true')
  await expect(pw).toBeFocused()
  expect(state.env).toBe('demo')
  await page.screenshot({ path: `${SHOTS}/sheet-wrong-password.png` })
})

test('a successful switch closes the sheet, shows Live with the first-month end, and the toolbar badge turns Live without a reload', async ({ page }) => {
  const state = await open(page)
  const badge = page.locator('.shell-env-badge')
  await expect(badge).toHaveText('Demo')
  let reloaded = false
  page.on('load', () => (reloaded = true))
  await page.getByRole('button', { name: 'Switch to live…' }).click()
  const sheet = page.getByRole('dialog', { name: /Switch Atlas to live\?/ })
  await sheet.getByLabel('Your Atlas password').fill(PASSWORD)
  await sheet.getByLabel('Type TRADE LIVE to confirm').fill('TRADE LIVE')
  await sheet.getByRole('button', { name: 'Switch to live' }).click()
  await expect(sheet).toBeHidden()
  expect(state.switches[0]).toEqual({ target: 'live', password: PASSWORD, confirmation: 'TRADE LIVE' })
  await expect(page.getByTestId('env-now-live')).toBeVisible()
  await expect(page.getByTestId('env-month')).toContainText('Your first live month runs until')
  await expect(badge).toHaveText('Live')
  await expect(badge).toHaveAttribute('data-env', 'live')
  expect(reloaded).toBe(false)
  await page.screenshot({ path: `${SHOTS}/live-en-light.png`, fullPage: true })

  await page.getByRole('button', { name: 'Switch to demo…' }).click()
  const back = page.getByRole('dialog', { name: 'Switch Atlas back to demo?' })
  await back.getByRole('button', { name: 'Switch to demo' }).click()
  await expect(back).toBeHidden()
  await expect(badge).toHaveText('Demo')
  expect(state.switches[1]).toEqual({ target: 'demo', confirm: true })
})

for (const [lang, theme, width] of [
  ['en', 'light', 1280],
  ['en', 'dark', 1280],
  ['ar', 'light', 1280],
  ['ar', 'dark', 390],
  ['en', 'light', 390],
] as const) {
  test(`two failing checks render in ${lang} ${theme} at ${width}px with a disabled switch`, async ({ page }) => {
    await open(page, { lang, theme, width, failing: ['demo_history', 'live_capital_cap'] })
    await expect(page.getByTestId('env-preflight').locator('.env-row')).toHaveCount(8)
    const toLive = page.getByRole('button', { name: lang === 'ar' ? 'الانتقال إلى الحقيقي…' : 'Switch to live…' })
    await expect(toLive).toBeDisabled()
    await expect(page.getByTestId('env-item-live_capital_cap').getByRole('link')).toHaveAttribute('href', '/settings/money?env=live')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await page.screenshot({ path: `${SHOTS}/two-failing-${lang}-${theme}-${width}.png`, fullPage: true })
    if (width === 1280) {
      const axe = await new AxeBuilder({ page }).include('[data-testid="environment-page"]').analyze()
      expect(axe.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(" ")} ${n.failureSummary}`))).toEqual([])
    }
  })
}
