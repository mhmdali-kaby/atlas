import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-249: the ⌘K palette with the keyboard only, against a fake Laravel. Open it with ⌘K / Ctrl+K,
 * arrow to Positions, press Enter, land on Positions; in light LTR and dark RTL.
 * Screenshots go to ../../evidence/ATL-249/.
 */

const SHOTS = '../../evidence/ATL-249'
const NOW = Date.now()
const HEALTH = {
  env: 'demo',
  as_of: new Date(NOW).toISOString(),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

async function fakeApi(page: Page, locale: 'en' | 'ar', theme: 'light' | 'dark') {
  const owner = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale, theme }
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
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: owner })
    if (url.pathname === '/api/me/preferences') return json(200, { data: { name: owner.name, locale, theme, timezone: 'Asia/Baghdad' } })
    if (url.pathname === '/api/system/health') return json(200, { data: HEALTH })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: [] } })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

const cases = [
  { name: 'light LTR', lang: 'en', theme: 'light', keys: 'Meta+k', positions: 'Positions' },
  { name: 'dark RTL', lang: 'ar', theme: 'dark', keys: 'Control+k', positions: 'المراكز' },
] as const

for (const c of cases) {
  test(`${c.name}: open the palette with the keyboard, arrow to Positions, Enter`, async ({ page }) => {
    await fakeApi(page, c.lang, c.theme)
    await page.addInitScript(
      ([lang, theme]) => {
        localStorage.setItem('atlas.lang', lang)
        localStorage.setItem('atlas.theme', theme)
      },
      [c.lang, c.theme] as const,
    )
    await page.goto('/settings/general')
    await expect(page.locator('html')).toHaveAttribute('dir', c.lang === 'ar' ? 'rtl' : 'ltr')
    await expect(page.locator('html')).toHaveClass(new RegExp(c.theme))

    await page.keyboard.press(c.keys)
    const palette = page.getByTestId('command-palette')
    await expect(palette).toBeVisible()
    await expect(page.getByRole('combobox')).toBeFocused()
    await page.screenshot({ path: `${SHOTS}/open-${c.lang}-${c.theme}.png` })

    // Arrow down from the first row until Positions is the selected option (no typing).
    const selected = page.locator('[role="option"][aria-selected="true"]')
    for (let i = 0; i < 20; i += 1) {
      if ((await selected.getAttribute('data-testid')) === 'palette-item-page.portfolio.positions') break
      await page.keyboard.press('ArrowDown')
    }
    await expect(selected).toHaveAttribute('data-testid', 'palette-item-page.portfolio.positions')
    await expect(selected).toContainText(c.positions)
    await page.screenshot({ path: `${SHOTS}/positions-selected-${c.lang}-${c.theme}.png` })

    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/portfolio\/positions$/)
    await expect(palette).toHaveCount(0)
    await expect(page.getByRole('heading', { level: 1, name: c.positions })).toBeVisible()
  })
}

test('typing filters, and Esc closes and gives focus back', async ({ page }) => {
  await fakeApi(page, 'en', 'light')
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'en'))
  await page.goto('/settings/general')
  const capsule = page.getByTestId('palette-open')
  await capsule.focus()
  await page.keyboard.press('Control+k')
  await page.keyboard.type('risk')
  await expect(page.locator('[role="option"][aria-selected="true"]')).toHaveAttribute('data-testid', 'palette-item-page.settings.risk')
  await page.screenshot({ path: `${SHOTS}/typed-risk-en-light.png` })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('command-palette')).toHaveCount(0)
  await expect(capsule).toBeFocused()
})
