import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-227: Settings → General against a faked Laravel (ATL-226 GET/PATCH /api/me/preferences). */

const SHOTS = '../../evidence/ATL-227'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }

type Prefs = { name: string; locale: 'en' | 'ar'; timezone: string; theme: 'system' | 'light' | 'dark' }

async function fakeApi(page: Page, prefs: Prefs) {
  const state = { prefs: { ...prefs }, patches: [] as { body: Record<string, string>; lang: string | undefined }[], langs: new Set<string>() }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    const lang = req.headers()['accept-language']
    if (lang) state.langs.add(lang)
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: { ...OWNER, name: state.prefs.name } })
    if (url.pathname === '/api/me/preferences' && method === 'GET') return json(200, { data: state.prefs })
    if (url.pathname === '/api/me/preferences' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, string>
      state.patches.push({ body, lang })
      state.prefs = { ...state.prefs, ...body } as Prefs
      return json(200, { data: state.prefs })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, prefs: Partial<Prefs> = {}, width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  // The browser starts in English/light; the saved preferences must win at boot.
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'en')
    localStorage.setItem('atlas.theme', 'light')
  })
  const api = await fakeApi(page, { name: 'Mohammed Ali', locale: 'en', timezone: 'Asia/Baghdad', theme: 'light', ...prefs })
  await page.goto('/settings/general')
  await expect(page.getByTestId('gn-page')).toBeVisible()
  return api
}

for (const width of [1280, 390]) {
  for (const locale of ['en', 'ar'] as const) {
    for (const theme of ['light', 'dark'] as const) {
      test(`shows the saved values at ${width}px, ${locale}, ${theme}`, async ({ page }) => {
        await open(page, { locale, theme, timezone: 'Europe/London' }, width)
        const html = page.locator('html')
        await expect(html).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
        await expect(html).toHaveAttribute('lang', locale)
        if (theme === 'dark') await expect(html).toHaveClass(/(^|\s)dark(\s|$)/)
        else await expect(html).not.toHaveClass(/(^|\s)dark(\s|$)/)
        await expect(page.locator('#gn-name')).toHaveValue('Mohammed Ali')
        await expect(page.getByTestId('gn-lang').getByRole('radio', { name: locale === 'ar' ? 'العربية' : 'English' })).toBeChecked()
        await expect(page.getByTestId('zone-button')).toContainText(locale === 'ar' ? 'لندن' : 'London')
        await expect(page.locator(`[data-theme-pick="${theme}"]`)).toHaveAttribute('aria-checked', 'true')
        await expect(page.getByTestId('dayline')).toBeVisible()
        // nothing spills sideways at phone width
        const overflow = await page.evaluate(() => {
          const c = document.getElementById('content')
          return c ? c.scrollWidth - c.clientWidth : 0
        })
        expect(overflow).toBeLessThanOrEqual(1)
        // the page scrolls inside <main>: a tall window shows all of it in one picture
        await page.setViewportSize({ width, height: width < 600 ? 2600 : 1700 })
        await page.screenshot({ path: `${SHOTS}/general-${width}-${locale}-${theme}.png` })
      })
    }
  }
}

test('choosing Arabic switches the whole app at once, survives a reload, and the toolbar agrees', async ({ page }) => {
  const api = await open(page)
  await page.getByTestId('gn-lang').getByRole('radio', { name: 'العربية' }).click()
  const html = page.locator('html')
  await expect(html).toHaveAttribute('dir', 'rtl')
  await expect(html).toHaveAttribute('lang', 'ar')
  await expect(page.getByRole('heading', { level: 2, name: 'عام' })).toBeVisible()
  await expect(page.getByTestId('language-switch')).toContainText('English')
  await expect.poll(() => api.patches).toEqual([{ body: { locale: 'ar' }, lang: 'en' }])

  await page.reload()
  await expect(page.getByTestId('gn-page')).toBeVisible()
  await expect(html).toHaveAttribute('dir', 'rtl')
  await expect(page.getByTestId('gn-lang').getByRole('radio', { name: 'العربية' })).toBeChecked()
  // the toolbar's language control shows the same choice: it offers the other language
  await expect(page.getByTestId('language-switch')).toContainText('English')
  expect(api.langs.has('ar')).toBe(true)
  await page.screenshot({ path: `${SHOTS}/general-after-reload-ar.png`, fullPage: true })
})

test('the toolbar language button writes the same preference the page shows', async ({ page }) => {
  const api = await open(page)
  await page.getByTestId('language-switch').click()
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.getByTestId('gn-lang').getByRole('radio', { name: 'العربية' })).toBeChecked()
  await expect.poll(() => api.patches.map((p) => p.body)).toEqual([{ locale: 'ar' }])
})

test('the page has no axe violations in English and Arabic', async ({ page }) => {
  // The shell's page fade-in would let axe sample half-faded text; reduced motion ends it at once.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await open(page, { timezone: 'Europe/London' })
  const run = async () =>
    (await new AxeBuilder({ page }).include('[data-testid="gn-page"]').analyze()).violations.flatMap((v) =>
      v.nodes.map((n) => `${v.id} ${n.target.join(' ')}`),
    )
  expect(await run()).toEqual([])
  await page.getByTestId('gn-lang').getByRole('radio', { name: 'العربية' }).click()
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  expect(await run()).toEqual([])
})
