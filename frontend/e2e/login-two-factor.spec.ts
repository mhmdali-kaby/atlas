import AxeBuilder from '@axe-core/playwright'
import { createHmac } from 'node:crypto'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-248: signing in with the password, then a TOTP code, for an owner who has two-factor
 * sign-in on. Laravel is faked with page.route (the ATL-246 API); the owner's authenticator
 * secret is seeded here, and both the test and the fake compute RFC 6238 codes from it, so the
 * code typed is a real time-based code, not a fixed string.
 */

const SHOTS = '../../evidence/ATL-248'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale: 'en', theme: 'light' }
const PASSWORD = 'correct horse battery staple'
/** The seeded owner's authenticator secret (base32), as TwoFactor::startEnrolment stores it. */
const SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'

function base32Decode(input: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const ch of input.replace(/=+$/, '')) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0')
  const bytes: number[] = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

/** RFC 6238 TOTP: SHA-1, 30-second steps, 6 digits (google2fa's defaults). */
function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)))
  const mac = createHmac('sha1', base32Decode(secret)).update(counter).digest()
  const offset = mac[mac.length - 1] & 0x0f
  const value = (mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000
  return String(value).padStart(6, '0')
}

/** Accepts the current step and one either side, like the API's window of 1. */
function verify(code: string): boolean {
  return [-1, 0, 1].some((w) => totp(SECRET, Date.now() + w * 30_000) === code)
}

async function fakeApi(page: Page, locale: 'en' | 'ar') {
  const state = { pending: false, signedIn: false, challenges: [] as Record<string, string>[] }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    const owner = { ...OWNER, locale }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true, two_factor_pending: state.pending })
    if (url.pathname === '/api/auth/me') return state.signedIn ? json(200, { data: owner }) : json(401, { message: 'Unauthenticated.' })
    if (url.pathname === '/api/me/preferences') return json(200, { data: { name: owner.name, locale, timezone: 'Asia/Baghdad', theme: 'light' } })
    if (url.pathname === '/api/auth/login' && method === 'POST') {
      const body = JSON.parse(req.postData() ?? '{}') as { email: string; password: string }
      if (body.email !== OWNER.email || body.password !== PASSWORD) {
        const msg = 'These credentials do not match our records.'
        return json(422, { message: msg, errors: { email: [msg] } })
      }
      state.pending = true
      return json(200, { two_factor: true })
    }
    if (url.pathname === '/api/auth/two-factor-challenge' && method === 'POST') {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, string>
      state.challenges.push(body)
      if (!state.pending) return json(422, { message: 'Your sign-in has expired.', error: 'challenge_expired', errors: { code: ['Your sign-in has expired.'] } })
      if (!body.code || !verify(body.code)) return json(422, { message: 'The code is incorrect.', error: 'code_wrong', errors: { code: ['The code is incorrect.'] } })
      state.pending = false
      state.signedIn = true
      return json(200, { data: owner })
    }
    // Anything the landing page asks for once signed in: empty is enough for this flow.
    return json(404, { message: 'Not found' })
  })
  return state
}

for (const locale of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    test(`signs in with password then a TOTP code, ${locale}, ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 900 })
      await page.addInitScript(
        ([lang, th]) => {
          localStorage.setItem('atlas.lang', lang)
          localStorage.setItem('atlas.theme', th)
        },
        [locale, theme],
      )
      const api = await fakeApi(page, locale)
      const ar = locale === 'ar'
      await page.goto('/login?next=%2Fsettings%2Fgeneral')
      const html = page.locator('html')
      await expect(html).toHaveAttribute('dir', ar ? 'rtl' : 'ltr')
      if (theme === 'dark') await expect(html).toHaveClass(/(^|\s)dark(\s|$)/)

      await page.getByLabel(ar ? 'البريد الإلكتروني' : 'Email').fill(OWNER.email)
      await page.getByLabel(ar ? 'كلمة المرور' : 'Password', { exact: true }).fill(PASSWORD)
      await page.getByRole('button', { name: ar ? 'تسجيل الدخول' : 'Sign in' }).click()

      const field = page.getByLabel(ar ? 'رمز من 6 أرقام' : '6-digit code')
      await expect(field).toBeFocused()
      await expect(field).toHaveAttribute('dir', 'ltr')
      await expect(page.getByText(ar ? 'أدخل الرمز المكوّن من 6 أرقام من تطبيق المصادقة.' : 'Enter the 6-digit code from your authenticator app.')).toBeVisible()
      // The cells read left to right in Arabic too.
      const cells = page.getByTestId('login-code-cells').locator('.otp-cells')
      await expect(cells).toHaveCSS('direction', 'ltr')
      // Let the step's slide-in settle before axe samples colours.
      await expect(page.getByTestId('two-factor-step')).toHaveCSS('opacity', '1')
      await page.waitForTimeout(300)
      const axe = await new AxeBuilder({ page }).include('main').analyze()
      expect(axe.violations).toEqual([])
      await page.screenshot({ path: `${SHOTS}/code-step-${locale}-${theme}.png` })

      // A wrong code first: the message shows and the step stays.
      await page.keyboard.type(totp(SECRET) === '000000' ? '111111' : '000000')
      await expect(page.getByRole('alert')).toContainText(ar ? 'هذا الرمز لم ينجح' : 'That code didn’t work')
      await page.screenshot({ path: `${SHOTS}/code-wrong-${locale}-${theme}.png` })

      // Then the real code from the seeded secret, typed digit by digit: six digits auto-submit.
      await expect(field).toBeFocused()
      await page.keyboard.type(totp(SECRET), { delay: 40 })
      await page.waitForURL('**/settings/general')
      expect(api.signedIn).toBe(true)
      expect(api.challenges).toHaveLength(2)
      expect(api.challenges[1]).toEqual({ code: expect.stringMatching(/^\d{6}$/) })
      await page.screenshot({ path: `${SHOTS}/landed-${locale}-${theme}.png` })
    })
  }
}
