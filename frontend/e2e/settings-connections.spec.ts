import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-20: Settings → Connections against a fake Laravel serving the ATL-4/ATL-8 connection
 * contract. Screenshots go to ../../evidence/ATL-20/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-20'
const ID = 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30'
const LIVE_ID = 'b1d8d4b3-7a5f-4a1c-8b80-1e7c9d2f3a41'
const LIVE_SHOTS = '../../evidence/ATL-241'

function connection(over: Record<string, unknown> = {}) {
  return {
    id: ID,
    exchange: 'okx',
    env: 'demo',
    label: 'OKX demo',
    api_key_last4: 'ab12',
    has_secret: true,
    has_passphrase: true,
    permissions: ['read_only', 'trade'],
    account_mode: 'futures',
    position_mode: 'net_mode',
    uid_last4: '6123',
    last_verified_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    last_test_error: null,
    created_at: '2026-10-28T07:00:00.000000Z',
    updated_at: '2026-10-28T08:00:00.000000Z',
    ...over,
  }
}

/** ATL-235's live row; verified = last_verified_at and withdraw_off_verified_at. */
function liveConnection(over: Record<string, unknown> = {}) {
  const at = new Date(Date.now() - 20 * 60_000).toISOString()
  return connection({ id: LIVE_ID, env: 'live', label: 'OKX live', api_key_last4: 'lv42', uid_last4: '9876', last_verified_at: at, withdraw_off_verified_at: at, ...over })
}
const refusedLive = () =>
  liveConnection({ permissions: ['read_only', 'trade', 'withdraw'], last_verified_at: null, withdraw_off_verified_at: null, last_test_error: 'withdraw_permission_enabled' })

async function fakeApi(
  page: Page,
  opts: { stored?: Record<string, unknown> | null; failTest?: boolean; live?: Record<string, unknown> | null; liveWithdraw?: boolean } = {},
) {
  let stored: Record<string, unknown> | null = opts.stored === undefined ? connection() : opts.stored
  let live: Record<string, unknown> | null = opts.live ?? null
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS') {
      return route.fulfill({
        status: 204,
        headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE' },
      })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections' && method === 'GET') return json(200, { data: [stored, live].filter(Boolean) })
    if (url.pathname === '/api/connections' && method === 'POST') {
      const body = req.postDataJSON() as Record<string, string>
      live = liveConnection({ label: body.label, api_key_last4: body.api_key.slice(-4), permissions: null, last_verified_at: null, withdraw_off_verified_at: null })
      return json(201, { data: live })
    }
    if (url.pathname === `/api/connections/${LIVE_ID}/test` && method === 'POST') {
      if (opts.liveWithdraw) {
        live = { ...refusedLive(), label: live?.label, api_key_last4: live?.api_key_last4 }
        return json(200, { ok: false, error: 'withdraw_permission_enabled', okx_code: null, acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', permissions: ['read_only', 'trade', 'withdraw'], warnings: [], uid_last4: '9876', verified_at: null, message: null, connection: live })
      }
      const at = new Date().toISOString()
      live = { ...liveConnection({ last_verified_at: at, withdraw_off_verified_at: at }), label: live?.label, api_key_last4: live?.api_key_last4 }
      return json(200, { ok: true, error: null, okx_code: null, acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', permissions: ['read_only', 'trade'], warnings: [], uid_last4: '9876', verified_at: at, message: null, connection: live })
    }
    if (url.pathname === `/api/connections/${ID}` && method === 'PATCH') {
      const body = req.postDataJSON() as Record<string, string>
      stored = connection({ ...stored, label: body.label, api_key_last4: body.api_key.slice(-4), last_verified_at: null })
      return json(200, { data: stored })
    }
    if (url.pathname === `/api/connections/${ID}` && method === 'DELETE') {
      stored = null
      return json(204)
    }
    if (url.pathname === `/api/connections/${ID}/test` && method === 'POST') {
      if (opts.failTest) {
        stored = connection({ ...stored, last_verified_at: null, last_test_error: 'invalid_credentials' })
        return json(200, { ok: false, error: 'invalid_credentials', okx_code: '50113', acct_lv: null, account_mode: null, position_mode: null, permissions: [], warnings: [], uid_last4: null, verified_at: null, message: null, connection: stored })
      }
      stored = connection({ ...stored, last_verified_at: new Date().toISOString(), last_test_error: null })
      return json(200, { ok: true, error: null, okx_code: null, acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', permissions: ['read_only', 'trade'], warnings: [], uid_last4: '6123', verified_at: stored.last_verified_at, message: null, connection: stored })
    }
    if (url.pathname.startsWith('/api/portfolio')) return json(200, { data: null })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.goto('/settings/connections')
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'
    test(`Connections renders ${dir}-${theme}`, async ({ page }) => {
      await fakeApi(page)
      await open(page, lang, theme)
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      await expect(page.getByTestId('key-tail')).toHaveText('•••• ab12')
      await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'الاتصالات' : 'Connections')
      await expect(page.getByTestId('live-empty')).toBeVisible()
      // Dates follow the page direction (design .num), unlike LTR-isolated figures.
      await expect(page.getByTestId('last-verified')).toHaveCSS('direction', dir)
      await page.waitForTimeout(400)
      await page.screenshot({ path: `${SHOTS}/connections-${dir}-${theme}.png`, fullPage: true })
      await page.getByTestId('live-empty').scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${SHOTS}/connections-${dir}-${theme}-bottom.png` })
    })
  }
}

test('keyboard only: Test again, Replace keys, Remove with the typed label', async ({ page }) => {
  await fakeApi(page)
  await open(page, 'en', 'light')
  await expect(page.getByTestId('key-tail')).toHaveText('•••• ab12')

  const testAgain = page.getByRole('button', { name: 'Test again' })
  await testAgain.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('conn-status')).toHaveText(/Verified just now/)

  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Replace keys' })).toBeFocused()
  await page.keyboard.press('Enter')
  const sheet = page.getByRole('dialog', { name: 'Replace the OKX demo keys' })
  await expect(sheet).toBeVisible()
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${SHOTS}/replace-sheet.png` })
  await sheet.getByLabel('API key').focus()
  await page.keyboard.type('aaaa1111-2222-3333-4444-55556666zz99')
  await page.keyboard.press('Tab')
  await page.keyboard.type('NEW-SECRET-1')
  // Secret's reveal button, then the passphrase
  await sheet.getByLabel('Passphrase').focus()
  await page.keyboard.type('New-pass-1')
  await page.keyboard.press('Enter')
  await expect(sheet).toBeHidden()
  await expect(page.getByTestId('key-tail')).toHaveText('•••• zz99')

  await page.getByRole('button', { name: 'Remove…' }).focus()
  await page.keyboard.press('Enter')
  const rm = page.getByRole('dialog', { name: 'Remove the OKX demo connection?' })
  await expect(rm.getByLabel('Type “OKX demo” to confirm')).toBeFocused()
  await expect(rm.getByRole('button', { name: 'Remove connection' })).toBeDisabled()
  await page.keyboard.type('OKX demo')
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/remove-sheet.png` })
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('not-connected')).toContainText('Not connected')
  await expect(page.getByRole('button', { name: 'Connect OKX demo' })).toBeFocused()
  await page.screenshot({ path: `${SHOTS}/not-connected.png`, fullPage: true })
})

test('a failed Test again shows the mapped error (Arabic, dark)', async ({ page }) => {
  await fakeApi(page, { failTest: true })
  await open(page, 'ar', 'dark')
  await page.getByRole('button', { name: 'إعادة الاختبار' }).click()
  await expect(page.getByTestId('test-error')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/test-failed-rtl-dark.png`, fullPage: true })
})

test('a demo key with Withdraw keeps the orange notice', async ({ page }) => {
  await fakeApi(page, { stored: connection({ permissions: ['read_only', 'trade', 'withdraw'] }) })
  await open(page, 'en', 'light')
  await expect(page.getByTestId('withdraw-warning')).toBeVisible()
  await page.waitForTimeout(10_500)
  await expect(page.getByTestId('withdraw-warning')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/withdraw-warning.png`, fullPage: true })
})

/* ATL-241: the Live account section, every state at 1280 and 390 px, light/dark, en/ar. */
for (const width of [1280, 390] as const) {
  for (const lang of ['en', 'ar'] as const) {
    for (const theme of ['light', 'dark'] as const) {
      const dir = lang === 'ar' ? 'rtl' : 'ltr'
      const tag = `${width}-${dir}-${theme}`
      test(`Live account states ${tag}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        // Empty, then connect a key that can withdraw (refused, red), then replace it with a Read + Trade one.
        await fakeApi(page, { liveWithdraw: true })
        await open(page, lang, theme)
        const section = page.getByTestId('live-section')
        await expect(section.getByTestId('live-empty')).toBeVisible()
        await page.waitForTimeout(400)
        await section.screenshot({ path: `${LIVE_SHOTS}/live-empty-${tag}.png` })
        await section.getByRole('button', { name: lang === 'ar' ? 'ربط OKX الحقيقي' : 'Connect OKX live' }).click()
        const sheet = page.getByRole('dialog')
        await sheet.locator('#lck-key').fill('bbbb1111-2222-3333-4444-55556666lv42')
        await sheet.locator('#lck-secret').fill('LIVE-SECRET-1')
        await sheet.locator('#lck-pass').fill('Live-pass-1')
        await page.waitForTimeout(400)
        await page.screenshot({ path: `${LIVE_SHOTS}/live-connect-sheet-${tag}.png` })
        await sheet.locator('form').getByRole('button', { name: lang === 'ar' ? 'حفظ واختبار' : 'Save and test' }).click()
        await expect(sheet.getByTestId('test-error')).toBeVisible()
        await page.screenshot({ path: `${LIVE_SHOTS}/live-connect-refused-sheet-${tag}.png` })
        await page.keyboard.press('Escape')
        await expect(section.getByTestId('live-refused')).toBeVisible()
        await expect(section.getByTestId('live-card')).toHaveAttribute('data-state', 'refused')
        await expect(section.getByTestId('live-key-tail')).toHaveText('•••• lv42')
        await expect(section).not.toContainText('LIVE-SECRET-1')
        await page.waitForTimeout(400)
        await section.screenshot({ path: `${LIVE_SHOTS}/live-refused-${tag}.png` })
      })
      test(`Live account verified ${tag}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await fakeApi(page, { live: liveConnection() })
        await open(page, lang, theme)
        const section = page.getByTestId('live-section')
        await expect(section.getByTestId('live-card')).toHaveAttribute('data-state', 'verified')
        await expect(section.getByTestId('uid-tail')).toHaveText('•••• 9876')
        await expect(section.getByTestId('live-env-link')).toHaveAttribute('href', '/settings/environment')
        await expect(page.getByTestId('trading-demo')).toBeVisible()
        await page.waitForTimeout(400)
        await page.screenshot({ path: `${LIVE_SHOTS}/live-verified-page-${tag}.png`, fullPage: true })
        await section.screenshot({ path: `${LIVE_SHOTS}/live-verified-${tag}.png` })
      })
    }
  }
}
