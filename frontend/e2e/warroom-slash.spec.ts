import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }

/*
 * ATL-163: the composer's slash commands against a fake Laravel. Captures the autocomplete, the
 * inline help for an unknown command, a done note and the /kill flatten sheet in en/ar ×
 * light/dark at 1280 px. Screenshots go to ../../evidence/ATL-163/.
 */

const SHOTS = '../../evidence/ATL-163'
const T = { en: enWarroom, ar: arWarroom }
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const NOW = Date.now()
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()
const MAIN = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: iso(60_000), created_at: iso(30 * 86_400_000) }
const HEALTH = {
  env: 'demo',
  as_of: iso(0),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

async function fakeApi(page: Page) {
  const writes: string[] = []
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (req.method() !== 'GET') writes.push(`${req.method()} ${url.pathname}`)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: [], meta: { next_cursor: null } })
    if (url.pathname === '/api/system/health') return json(200, { data: HEALTH })
    if (url.pathname === '/api/system/pause') return json(200, { ok: true, paused: true })
    if (url.pathname === '/api/portfolio/orders') return json(200, { data: { env: 'demo', orders: [], algo_orders: [] } })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: iso(0), items: [] } })
    return json(404, { message: 'Not found' })
  })
  return writes
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1280, height: 900 })
  const writes = await fakeApi(page)
  await page.goto('/warroom')
  await expect(page.getByTestId('wr-composer-input')).toBeEnabled()
  return writes
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'
    test(`slash autocomplete, help, note and /kill flatten, ${dir}-${theme}`, async ({ page }) => {
      const t = T[lang]
      mkdirSync(SHOTS, { recursive: true })
      const writes = await open(page, lang, theme)
      const input = page.getByTestId('wr-composer-input')
      const composer = page.locator('.composer-wrap')

      await input.pressSequentially('/')
      await expect(page.getByTestId('wr-slash-picker').getByRole('option')).toHaveCount(10)
      await page.screenshot({ path: `${SHOTS}/autocomplete-${dir}-${theme}.png`, animations: 'disabled' })

      await input.fill('/foo')
      await input.press('Escape')
      await input.press('Enter')
      await expect(page.getByTestId('wr-slash-help')).toContainText(t.slash.help.unknown.replace('<c>{{cmd}}</c>', '\u2066/foo\u2069'))
      // The typed command is an LTR-isolated run, so RTL never shows it as "foo/".
      const cmd = page.getByTestId('wr-slash-help').locator('.picker-label bdi[dir="ltr"]')
      await expect(cmd).toHaveCSS('direction', 'ltr')
      await expect(cmd).toHaveCSS('unicode-bidi', 'isolate')
      // Measure the glyphs: "/" must sit left of "f" in both directions (RTL once showed "foo/").
      const xs = await cmd.evaluate((b) => {
        const node = b.firstChild!
        const text = node.textContent ?? ''
        const at = (ch: string) => {
          const r = document.createRange()
          r.setStart(node, text.indexOf(ch))
          r.setEnd(node, text.indexOf(ch) + 1)
          return r.getBoundingClientRect().x
        }
        return { slash: at('/'), f: at('f') }
      })
      expect(xs.slash).toBeLessThan(xs.f)
      await page.screenshot({ path: `${SHOTS}/help-${dir}-${theme}.png`, animations: 'disabled' })
      expect(writes).toEqual([])

      await input.fill('/pause')
      await input.press('Enter')
      await expect(page.getByTestId('wr-slash-note')).toHaveText(t.slash.done.pause)
      await composer.screenshot({ path: `${SHOTS}/note-${dir}-${theme}.png`, animations: 'disabled' })
      expect(writes).toEqual(['POST /api/system/pause'])

      await input.fill('/kill flatten')
      await input.press('Enter')
      const sheet = page.getByRole('dialog')
      await expect(sheet.getByTestId('kill-flatten')).toBeChecked()
      await expect(sheet.getByTestId('kill-go')).toHaveCount(0)
      await page.screenshot({ path: `${SHOTS}/kill-flatten-${dir}-${theme}.png`, animations: 'disabled' })
      expect(writes).toEqual(['POST /api/system/pause'])
    })
  }
}
