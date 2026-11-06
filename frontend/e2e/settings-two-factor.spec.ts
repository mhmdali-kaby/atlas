import { createHmac } from 'node:crypto'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-247: Settings → General → Two-factor sign-in against a faked ATL-245 /api/me/two-factor.
 * The fake server checks a real TOTP (RFC 6238, SHA-1, 30 s, 6 digits) for its secret, and the
 * test types the code it computes from the secret the sheet shows, as an owner's phone would.
 */

const SHOTS = '../../evidence/ATL-247'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SECRET = 'SOFIABYEHVUSCMBXROJVVMTK4QKN3BAE'
const PASSWORD = 'correct horse battery'
const CODES = ['k7m2p-x9q4t', 'r3vhw-8n2ca', 'p6tz9-m4kxe', 'b2wq7-h5rnd', 'y8cf3-t6jmv', 'e4nk2-q9xsp', 'h9dr6-w3bzu', 'u5gm8-c2tfy']
// A stand-in QR (the real one comes from bacon/bacon-qr-code); black on white like the API's.
const QR = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><rect width="21" height="21" fill="white"/><path d="M0 0h7v7h-7zM14 0h7v7h-7zM0 14h7v7h-7zM9 9h3v3h-3z" fill="black"/></svg>`

function base32(s: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const ch of s.replace(/[\s=]/g, '').toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0')
  const bytes: number[] = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)))
  const h = createHmac('sha1', base32(secret)).update(counter).digest()
  const o = h[h.length - 1]! & 0xf
  const n = ((h[o]! & 0x7f) << 24) | (h[o + 1]! << 16) | (h[o + 2]! << 8) | h[o + 3]!
  return String(n % 1_000_000).padStart(6, '0')
}

async function fakeApi(page: Page, theme: 'light' | 'dark') {
  const state = { enabled: false, confirmedAt: null as string | null, started: false, confirms: [] as string[] }
  const status = () => ({ enabled: state.enabled, confirmed_at: state.confirmedAt, recovery_codes_left: state.enabled ? 8 : 0 })
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (code: number, body?: unknown) =>
      route.fulfill({ status: code, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    const body = JSON.parse(req.postData() ?? '{}') as Record<string, string>
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/me/preferences') return json(200, { data: { name: OWNER.name, locale: 'en', timezone: 'Asia/Baghdad', theme } })
    if (url.pathname === '/api/me/two-factor' && method === 'GET') return json(200, { data: status() })
    if (url.pathname === '/api/me/two-factor/start') {
      if (body.password !== PASSWORD) return json(422, { message: 'The password is incorrect.', errors: { password: ['The password is incorrect.'] } })
      state.started = true
      return json(200, { data: { qr_svg: QR, secret: SECRET } })
    }
    if (url.pathname === '/api/me/two-factor/confirm') {
      state.confirms.push(body.code ?? '')
      const now = Date.now()
      const ok = state.started && [now - 30_000, now, now + 30_000].some((at) => totp(SECRET, at) === body.code)
      if (!ok) return json(422, { message: 'The code is wrong.', errors: { code: ['That code didn’t match. Type the code your app shows now.'] } })
      state.enabled = true
      state.confirmedAt = new Date(now).toISOString()
      return json(200, { data: { ...status(), recovery_codes: CODES } })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

for (const theme of ['light', 'dark'] as const) {
  test(`turns two-factor on with a code computed from the shown secret (${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.addInitScript((th) => {
      localStorage.setItem('atlas.lang', 'en')
      localStorage.setItem('atlas.theme', th)
    }, theme)
    const api = await fakeApi(page, theme)
    await page.goto('/settings/general')
    const section = page.getByTestId('tf-section')
    await expect(section.getByText('Off. Atlas asks only for your password.')).toBeVisible()
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/(^|\s)dark(\s|$)/)
    await section.screenshot({ path: `${SHOTS}/off-${theme}.png` })

    await section.getByRole('button', { name: 'Turn on…' }).click()
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByText('Step 1 of 3')).toBeVisible()
    await sheet.getByLabel('Your Atlas password').fill(PASSWORD)
    await sheet.getByRole('button', { name: 'Continue' }).click()

    await expect(sheet.getByTestId('tf-qr')).toBeVisible()
    await expect(sheet.getByText('Step 2 of 3')).toBeVisible()
    const shown = (await sheet.getByTestId('tf-secret').innerText()).replace(/\s/g, '')
    expect(shown).toBe(SECRET)
    await page.screenshot({ path: `${SHOTS}/scan-${theme}.png` })
    expect((await new AxeBuilder({ page }).include('[data-sheet]').analyze()).violations).toEqual([])

    // The phone's authenticator: a TOTP from the setup key on screen.
    await sheet.getByLabel('6-digit code').pressSequentially(totp(shown))

    const codes = page.getByRole('alertdialog')
    await expect(codes.getByText('Save your recovery codes')).toBeVisible()
    await expect(codes.getByTestId('tf-codes').getByRole('listitem')).toHaveCount(8)
    expect(api.confirms).toHaveLength(1)
    await page.screenshot({ path: `${SHOTS}/codes-${theme}.png` })

    await page.keyboard.press('Escape')
    await expect(codes).toBeVisible()
    await codes.getByRole('button', { name: 'I saved them' }).click()
    await expect(page.getByRole('alertdialog')).toHaveCount(0)
    await expect(section.getByTestId('tf-badge')).toHaveText('On')
    await expect(section.getByTestId('tf-left')).toHaveText('8 of 8 left. Each one signs you in once, without your phone.')
    await section.screenshot({ path: `${SHOTS}/on-${theme}.png` })
  })
}
