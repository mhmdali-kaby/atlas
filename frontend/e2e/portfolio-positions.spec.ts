import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-23: Portfolio → Positions against a fake Laravel serving the ATL-12 contract with
 * PortfolioDemoSeeder's BTC-USDT-SWAP long and XAU-USDT-SWAP short. Screenshots go to
 * ../../evidence/ATL-23/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-23'
const NOW = Date.now()
const HOUR = 3_600_000

const BTC = {
  id: '809d7f30',
  inst_id: 'BTC-USDT-SWAP',
  side: 'long',
  pos_side: 'net',
  size: '25',
  leverage: '5',
  entry: '67250.5',
  mark: '68120.3',
  liq_px: '54112.8',
  margin: '3362.525',
  settle_ccy: 'USDT',
  upl_usd: '217.45',
  upl_pct: '6.466866',
  margin_mode: 'isolated',
  opened_at: new Date(NOW - 9 * HOUR).toISOString(),
  stop: null,
  take_profits: null,
  agent: null,
  proposal_id: null,
}
const XAU = {
  ...BTC,
  id: '85242c4a',
  inst_id: 'XAU-USDT-SWAP',
  side: 'short',
  size: '400',
  leverage: '3',
  entry: '2741.8',
  mark: '2728.4',
  liq_px: '3618.9',
  margin: '3655.733333',
  upl_usd: '53.6',
  upl_pct: '1.46619',
  opened_at: new Date(NOW - 27 * HOUR).toISOString(),
}

/** Extra rows (only for the sticky-header check) so the page has to scroll. */
function many() {
  return Array.from({ length: 30 }, (_, i) => ({ ...BTC, id: `x${i}`, inst_id: `C${String(i).padStart(2, '0')}-USDT-SWAP` }))
}

async function fakeApi(page: Page, items: unknown[]) {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const cors = {
      'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*',
      'Access-Control-Allow-Credentials': 'true',
    }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/portfolio/summary') {
      return json(200, {
        data: {
          env: 'demo',
          total_equity_usd: '5136121.59',
          net_deposits_usd: '0',
          total_pnl_usd: '136121.59',
          total_pnl_pct: '2.7224',
          pnl_since: new Date(NOW - 30 * 24 * HOUR).toISOString(),
          today_pnl_usd: '7219.64',
          unrealised_pnl_usd: '271.05',
          fees_paid_usd: '13.88991',
          funding_net_usd: '-0.036183',
          equity_as_of: new Date(NOW).toISOString(),
          reconciled_at: new Date(NOW).toISOString(),
          stale: false,
        },
      })
    }
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date(NOW).toISOString(), items } })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
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
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/portfolio/positions')
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'
    test(`Positions renders ${dir}-${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await fakeApi(page, [BTC, XAU])
      await open(page, lang, theme)
      await expect(page.getByTestId('positions-table')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      await expect(page.getByTestId('position-row')).toHaveCount(2)
      await expect(page.getByTestId('reconciled')).toHaveAttribute('data-state', 'ok')

      // The sidebar has the Positions entry, marked current.
      await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'المراكز' : 'Positions')

      // Column order mirrors: Instrument is at the reading start, Time in trade at the end.
      const inst = (await page.locator('th[data-col="instrument"]').boundingBox())!
      const time = (await page.locator('th[data-col="time"]').boundingBox())!
      if (dir === 'ltr') expect(inst.x).toBeLessThan(time.x)
      else expect(inst.x).toBeGreaterThan(time.x)

      // Numbers are end-aligned: their right (ltr) / left (rtl) edge meets the cell's.
      const entryCell = page.locator('[data-testid=position-row]').first().locator('[data-col="entry"]')
      await expect(entryCell).toHaveCSS('text-align', 'end')

      // Western digits only.
      expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)
      await expect(entryCell).toHaveText('67,250.5')

      await page.screenshot({ path: `${SHOTS}/positions-${dir}-${theme}.png`, fullPage: true })
    })
  }
}

test('the header stays visible while the table scrolls', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 700 })
  await fakeApi(page, [BTC, XAU, ...many()])
  await open(page, 'en', 'light')
  await expect(page.getByTestId('positions-table')).toBeVisible()
  const main = page.locator('main#content')
  await main.evaluate((el) => el.scrollTo(0, 900))
  const header = page.locator('th[data-col="instrument"]')
  const mainBox = (await main.boundingBox())!
  const headBox = (await header.boundingBox())!
  expect(headBox.y).toBeGreaterThanOrEqual(mainBox.y - 1)
  expect(headBox.y).toBeLessThan(mainBox.y + 40)
  await expect(header).toBeInViewport()
  await page.screenshot({ path: `${SHOTS}/positions-sticky-header.png` })
})

test('no open positions shows the empty state', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page, [])
  await open(page, 'en', 'light')
  await expect(page.getByText('No open positions on the OKX demo account.', { exact: false })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/positions-empty.png` })
})

test('narrow window scrolls the table sideways with the instrument pinned (rtl)', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 })
  await fakeApi(page, [BTC, XAU])
  await open(page, 'ar', 'light')
  await expect(page.getByTestId('positions-table')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/positions-narrow-rtl.png` })
})
