import { expect, test, type Page } from '@playwright/test'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-18'
const ID = 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30'
const SECRET = 'SECRET-9F8E7D6C5B4A'
const PASS = 'Pass-phrase-77'

type Mode = 'ok' | 'wrong' | 'withdraw-demo'

/** A fake Laravel on the API origin: auth, /api/connections and the test endpoint. */
async function fakeApi(page: Page, mode: Mode) {
  const state: { stored: Record<string, unknown> | null; bodies: string[] } = { stored: null, bodies: [] }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) => {
      const text = body === undefined ? '' : JSON.stringify(body)
      state.bodies.push(text)
      return route.fulfill({ status, contentType: 'application/json', headers: cors, body: text })
    }
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections' && method === 'GET') return json(200, { data: state.stored ? [state.stored] : [] })
    if (url.pathname === '/api/connections' && (method === 'POST' || method === 'PATCH')) {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, string>
      state.stored = {
        id: ID, exchange: 'okx', env: 'demo', label: body.label, api_key_last4: (body.api_key ?? '').slice(-4), has_secret: true,
        has_passphrase: true, permissions: null, account_mode: null, position_mode: null, uid_last4: null, last_verified_at: null,
        last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
      }
      return json(201, { data: state.stored })
    }
    if (url.pathname === `/api/connections/${ID}` && method === 'PATCH') return json(200, { data: state.stored })
    if (url.pathname === `/api/connections/${ID}/test`) {
      if (mode === 'wrong') {
        state.stored = { ...state.stored, last_test_error: 'invalid_credentials' }
        return json(200, { ok: false, acct_lv: null, account_mode: null, position_mode: null, permissions: [], warnings: [], uid_last4: null, error: 'invalid_credentials', okx_code: '50113', verified_at: null, message: 'Key or passphrase is wrong', connection: state.stored })
      }
      const permissions = mode === 'withdraw-demo' ? ['read_only', 'trade', 'withdraw'] : ['read_only', 'trade']
      state.stored = { ...state.stored, permissions, account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123', last_verified_at: '2026-10-28T08:00:00Z' }
      return json(200, { ok: true, acct_lv: '2', account_mode: 'futures', position_mode: 'net_mode', permissions, warnings: mode === 'withdraw-demo' ? ['withdraw_permission_enabled'] : [], uid_last4: '6123', error: null, okx_code: null, verified_at: '2026-10-28T08:00:00Z', message: null, connection: state.stored })
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

async function fill(page: Page, labels: { key: string; secret: string; pass: string; test: string }) {
  await page.getByLabel(labels.key, { exact: true }).fill('f3b1c2d4-1111-2222-3333-44445555ab12')
  await page.getByLabel(labels.secret, { exact: true }).fill(SECRET)
  await page.getByLabel(labels.pass, { exact: true }).fill(PASS)
  await page.getByRole('button', { name: labels.test }).click()
}

const EN = { key: 'API key', secret: 'Secret key', pass: 'Passphrase', test: 'Test connection' }
const AR = { key: 'مفتاح API', secret: 'المفتاح السري', pass: 'عبارة المرور', test: 'اختبار الاتصال' }

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'light'], ['ar', 'dark']] as const) {
  test(`welcome and connect render in ${lang} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 860 })
    await prefs(page, lang, theme)
    await fakeApi(page, 'ok')
    await page.goto('/')
    await expect(page).toHaveURL(/\/onboarding$/)
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(theme === 'dark')
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(lang === 'ar' ? 'مرحبًا بك في أطلس' : 'Welcome to Atlas')
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/welcome-${lang}-${theme}.png` })

    await page.getByTestId('ob-next').click()
    await expect(page).toHaveURL(/step=connect/)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(lang === 'ar' ? 'اربط حساب OKX التجريبي' : 'Connect your OKX demo account')
    await expect(page.getByTestId('ob-next')).toBeDisabled()
    await page.waitForTimeout(500) // the step slides in (250ms) before the picture
    await page.screenshot({ path: `${SHOTS}/connect-${lang}-${theme}.png` })

    // In Arabic the step-3 dot sits left of step 2: the stepper follows the reading direction.
    const dots = page.getByRole('navigation').locator('li')
    const [a, b] = [await dots.nth(0).boundingBox(), await dots.nth(1).boundingBox()]
    expect(lang === 'ar' ? a!.x > b!.x : a!.x < b!.x).toBe(true)

    await fill(page, lang === 'ar' ? AR : EN)
    await expect(page.getByTestId('test-success')).toBeVisible()
    await expect(page.getByTestId('uid-tail')).toHaveText('•••• 6123')
    await expect(page.getByTestId('ob-next')).toBeEnabled()
    await page.waitForTimeout(800) // the result settles in (300ms spring) before the picture
    await page.screenshot({ path: `${SHOTS}/connect-success-${lang}-${theme}.png`, fullPage: true })
    await page.getByTestId('test-success').screenshot({ path: `${SHOTS}/result-card-${lang}-${theme}.png` })
    await page.getByTestId('saved-key').screenshot({ path: `${SHOTS}/saved-key-${lang}-${theme}.png` })
  })
}

test('a wrong passphrase shows the mapped error and keeps Continue disabled', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 860 })
  await prefs(page, 'en', 'light')
  await fakeApi(page, 'wrong')
  await page.goto('/onboarding?step=connect')
  await fill(page, EN)
  await expect(page.getByRole('alert')).toContainText('Key or passphrase is wrong')
  await expect(page.getByTestId('ob-next')).toBeDisabled()
  await page.screenshot({ path: `${SHOTS}/connect-wrong-key-en-light.png` })
})

test('a demo key with Withdraw keeps an orange notice after 10 s and after a reload, and Back shows only •••• last4', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await prefs(page, 'en', 'light')
  const api = await fakeApi(page, 'withdraw-demo')
  await page.goto('/onboarding?step=connect')
  await fill(page, EN)
  const notice = page.getByTestId('withdraw-warning')
  await expect(notice).toContainText('This key can withdraw funds')
  await expect(page.getByTestId('ob-next')).toBeEnabled()
  await page.waitForTimeout(10_500) // longer than a toast's 4.2 s
  await expect(notice).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/connect-withdraw-warning-after-10s.png`, fullPage: true })

  await page.reload()
  await expect(page.getByTestId('withdraw-warning')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/connect-withdraw-warning-after-reload.png`, fullPage: true })

  await page.getByRole('button', { name: 'Back' }).click()
  await page.getByTestId('ob-next').click()
  await expect(page.getByTestId('saved-key')).toContainText('•••• ab12')
  await expect(page.getByLabel('API key', { exact: true })).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/connect-saved-after-back.png` })

  for (const body of api.bodies) {
    expect(body).not.toContain(SECRET)
    expect(body).not.toContain(PASS)
  }
})
