import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-249: the ⌘K palette with the keyboard only, against a fake Laravel. Open it with ⌘K / Ctrl+K,
 * arrow to Positions, press Enter, land on Positions; in light LTR and dark RTL.
 * Screenshots go to ../../evidence/ATL-249/.
 * ATL-250: typing "xau" lists the instrument, Enter opens the War Room with it in the inspector
 * (screenshots in ../../evidence/ATL-250/).
 */

const SHOTS = '../../evidence/ATL-249'
const NOW = Date.now()
const HEALTH = {
  env: 'demo',
  as_of: new Date(NOW).toISOString(),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

const INSTRUMENTS = [
  { inst_id: 'XAU-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAU', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'live', blocked: false },
  { inst_id: 'XAUT-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAUT', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'preopen', blocked: false },
  { inst_id: 'NG-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'NG', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'suspend', blocked: false },
]
const MAIN = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: null, created_at: new Date(NOW - 86_400_000 * 30).toISOString() }

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
    if (url.pathname === '/api/instruments') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase()
      return json(200, { data: INSTRUMENTS.filter((i) => i.inst_id.toLowerCase().includes(q) || i.base_ccy.toLowerCase().includes(q)) })
    }
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: [], meta: { next_cursor: null } })
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

for (const c of cases) {
  test(`${c.name}: "xau" lists gold under Instruments and Enter opens it in the War Room inspector (ATL-250)`, async ({ page }) => {
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
    await page.keyboard.press(c.keys)
    await page.keyboard.type('xau')
    const row = page.getByTestId('palette-item-inst.XAU-USDT-SWAP')
    await expect(row).toBeVisible()
    await expect(page.locator('[role="option"][aria-selected="true"]')).toHaveAttribute('data-testid', 'palette-item-inst.XAU-USDT-SWAP')
    await page.screenshot({ path: `../../evidence/ATL-250/xau-${c.lang}-${c.theme}.png` })
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/warroom\?inst=XAU-USDT-SWAP$/)
    await expect(page.getByTestId('wr-inspector-picked')).toContainText('XAU-USDT-SWAP')
    await page.screenshot({ path: `../../evidence/ATL-250/inspector-${c.lang}-${c.theme}.png` })
  })
}
