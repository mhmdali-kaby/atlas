import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-24: Portfolio → Holdings and Cash flows against a fake Laravel serving the ATL-12
 * contract (PortfolioDemoSeeder's three balances, plus 27 made-up flows for the pager).
 * Screenshots go to ../../evidence/ATL-24/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-24'
const NOW = Date.now()
const DAY = 86_400_000

const later = { avg_cost_usd: null, change_24h_pct: null }
const HOLDINGS = {
  taken_at: new Date(NOW).toISOString(),
  total_value_usd: '5136121.59',
  items: [
    { ccy: 'USDT', account: 'trading', quantity: '5099121.59', available: '5092103.331667', frozen: '7018.258333', value_usd: '5099121.59', pct_of_portfolio: '99.2796', ...later },
    { ccy: 'USDC', account: 'trading', quantity: '25000', available: '25000', frozen: '0', value_usd: '25000', pct_of_portfolio: '0.4867', ...later },
    { ccy: 'USDT', account: 'funding', quantity: '12000', available: '12000', frozen: '0', value_usd: '12000', pct_of_portfolio: '0.2336', ...later },
  ],
}

const FLOWS = Array.from({ length: 27 }, (_, i) => ({
  id: `flow-${i + 1}`,
  kind: i % 4 === 1 ? 'withdrawal' : 'deposit',
  ccy: i % 3 === 2 ? 'USDC' : 'USDT',
  amount: String(250 * (i + 1)),
  amount_usd: String(250 * (i + 1)),
  state: i === 0 ? '0' : i === 5 ? '-1' : '2',
  tx_id: i % 5 === 4 ? null : `0x9f3a${(i * 7919).toString(16).padStart(54, 'a')}c41e`,
  ts: new Date(NOW - (i + 1) * 1.7 * DAY).toISOString(),
}))

async function fakeApi(page: Page, opts: { holdings?: unknown; flows?: unknown[]; fail?: boolean } = {}) {
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
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/portfolio/summary') {
      return json(200, { data: { env: 'demo', total_equity_usd: '5136121.59', reconciled_at: new Date(NOW).toISOString(), stale: false } })
    }
    if (opts.fail && url.pathname.startsWith('/api/portfolio/')) return json(500, { message: 'Server Error' })
    if (url.pathname === '/api/portfolio/holdings') return json(200, { data: opts.holdings ?? HOLDINGS })
    if (url.pathname === '/api/portfolio/cashflows') {
      const all = opts.flows ?? FLOWS
      const per = Number(url.searchParams.get('per_page') ?? 20)
      const p = Number(url.searchParams.get('page') ?? 1)
      const data = all.slice((p - 1) * per, p * per)
      return json(200, {
        data,
        links: {},
        meta: {
          current_page: p,
          last_page: Math.max(1, Math.ceil(all.length / per)),
          per_page: per,
          from: data.length ? (p - 1) * per + 1 : null,
          to: data.length ? (p - 1) * per + data.length : null,
          total: all.length,
        },
      })
    }
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, path: string, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(path)
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'

    test(`Holdings renders ${dir}-${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await fakeApi(page)
      await open(page, '/portfolio/holdings', lang, theme)
      await expect(page.getByTestId('holdings-table')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'الأرصدة' : 'Holdings')
      const rows = page.getByTestId('holding-row')
      await expect(rows).toHaveCount(3)
      await expect(rows.first().locator('[data-col="value"]')).toHaveText('$5,099,121.59')
      await expect(rows.first().locator('[data-col="share"]')).toContainText('99.28%')

      const cur = (await page.locator('th[data-col="currency"]').boundingBox())!
      const chg = (await page.locator('th[data-col="change"]').boundingBox())!
      if (dir === 'ltr') expect(cur.x).toBeLessThan(chg.x)
      else expect(cur.x).toBeGreaterThan(chg.x)
      await expect(rows.first().locator('[data-col="quantity"]')).toHaveCSS('text-align', 'end')
      expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)

      await page.screenshot({ path: `${SHOTS}/holdings-${dir}-${theme}.png`, fullPage: true })
    })

    test(`Cash flows renders ${dir}-${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await fakeApi(page)
      await open(page, '/portfolio/cashflows', lang, theme)
      await expect(page.getByTestId('cashflows-table')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'الإيداع والسحب' : 'Cash flows')
      await expect(page.getByTestId('cashflow-row')).toHaveCount(20)
      // Only the pager's two buttons inside the page content.
      await expect(page.locator('main button')).toHaveCount(2)
      expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)

      const date = (await page.locator('th[data-col="date"]').boundingBox())!
      const tx = (await page.locator('th[data-col="tx"]').boundingBox())!
      if (dir === 'ltr') expect(date.x).toBeLessThan(tx.x)
      else expect(date.x).toBeGreaterThan(tx.x)

      await page.screenshot({ path: `${SHOTS}/cashflows-${dir}-${theme}.png`, fullPage: true })

      await page.locator('main button').nth(1).click()
      await expect(page).toHaveURL(/page=2/)
      await expect(page.getByTestId('cashflow-row')).toHaveCount(7)
      await expect(page.getByTestId('cashflows-range')).toContainText('21–27')
      if (theme === 'light') await page.screenshot({ path: `${SHOTS}/cashflows-${dir}-page2.png`, fullPage: true })
    })
  }
}

test('empty states', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page, { holdings: { taken_at: new Date(NOW).toISOString(), total_value_usd: '0', items: [] }, flows: [] })
  await open(page, '/portfolio/holdings', 'en', 'light')
  await expect(page.getByTestId('state-none')).toContainText('No balances')
  await page.screenshot({ path: `${SHOTS}/holdings-empty.png` })
  await page.goto('/portfolio/cashflows')
  await expect(page.getByTestId('state-none')).toContainText('No deposits or withdrawals')
  await expect(page.locator('main button')).toHaveCount(0)
  await page.screenshot({ path: `${SHOTS}/cashflows-empty.png` })
})

test('error states (rtl, dark)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page, { fail: true })
  await open(page, '/portfolio/holdings', 'ar', 'dark')
  await expect(page.getByTestId('state-error')).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: `${SHOTS}/holdings-error-rtl-dark.png` })
  await page.goto('/portfolio/cashflows')
  await expect(page.getByTestId('state-error')).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: `${SHOTS}/cashflows-error-rtl-dark.png` })
})

test('narrow window keeps the first column pinned (rtl)', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 })
  await fakeApi(page)
  await open(page, '/portfolio/holdings', 'ar', 'light')
  await expect(page.getByTestId('holdings-table')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/holdings-narrow-rtl.png` })
  await page.goto('/portfolio/cashflows')
  await expect(page.getByTestId('cashflows-table')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/cashflows-narrow-rtl.png` })
})
