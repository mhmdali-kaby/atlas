import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-150: Portfolio → Orders against a fake Laravel serving the ATL-148 GET /api/portfolio/orders
 * and POST /api/orders/{id}/cancel shapes. Screenshots go to ../../evidence/ATL-150/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-150'
const NOW = Date.now()
const MIN = 60_000

const SOL = {
  id: '9d6c1f2e-0000-4000-8000-000000000001',
  env: 'demo',
  inst_id: 'SOL-USDT-SWAP',
  side: 'buy',
  pos_side: 'net',
  ord_type: 'post_only',
  td_mode: 'isolated',
  px: '249.4',
  sz: '12.02',
  filled_sz: '3',
  avg_px: null,
  lever: 3,
  state: 'partially_filled',
  purpose: 'entry',
  origin: 'atlas',
  reduce_only: false,
  cl_ord_id: 'e3f0',
  ord_id: '123456',
  proposal_id: '7a1b2c3d-1111-4000-8000-000000000412',
  last_error_code: null,
  last_error_msg: null,
  can_cancel: true,
  created_at: new Date(NOW - 69 * MIN).toISOString(),
  updated_at: new Date(NOW - 60 * MIN).toISOString(),
}
const ETH = {
  ...SOL,
  id: '9d6c1f2e-0000-4000-8000-000000000002',
  inst_id: 'ETH-USDT-SWAP',
  side: 'sell',
  ord_type: 'limit',
  px: '3980',
  sz: '0.5',
  filled_sz: '0',
  state: 'live',
  origin: 'external',
  proposal_id: null,
  created_at: new Date(NOW - 190 * MIN).toISOString(),
}
const algo = (over: Record<string, unknown>) => ({
  id: 'a1',
  env: 'demo',
  order_id: null,
  inst_id: 'BTC-USDT-SWAP',
  kind: 'sl',
  protects: true,
  side: 'sell',
  pos_side: 'net',
  trigger_px: null,
  sl_trigger_px: null,
  tp_trigger_px: null,
  ord_px: null,
  sz: '250',
  state: 'live',
  origin: 'atlas',
  reduce_only: true,
  algo_id: '7001',
  algo_cl_ord_id: 's3f',
  last_error_code: null,
  last_error_msg: null,
  created_at: new Date(NOW - 300 * MIN).toISOString(),
  updated_at: new Date(NOW - 300 * MIN).toISOString(),
  ...over,
})
const ALGOS = [
  algo({ id: 'a1', kind: 'oco', sl_trigger_px: '113200', tp_trigger_px: '121300' }),
  algo({ id: 'a2', inst_id: 'XAU-USDT-SWAP', kind: 'sl', trigger_px: '4117.6', sz: '4000', side: 'buy' }),
  algo({ id: 'a3', inst_id: 'XAU-USDT-SWAP', kind: 'tp', protects: false, trigger_px: '4075.8', sz: '2000', side: 'buy' }),
  algo({ id: 'a4', inst_id: 'TSLA-USDT-SWAP', kind: 'sl', trigger_px: '431.2', sz: null, origin: 'external' }),
]

async function fakeApi(page: Page, opts: { cancelFails?: boolean } = {}) {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
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
          pnl_since: new Date(NOW - 30 * 24 * 60 * MIN).toISOString(),
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
    if (url.pathname === '/api/portfolio/orders') return json(200, { data: { env: 'demo', orders: [SOL, ETH], algo_orders: ALGOS } })
    if (/^\/api\/orders\/[^/]+\/cancel$/.test(url.pathname)) {
      return opts.cancelFails
        ? json(502, { ok: false, status: 'failed', failed: [{ code: '51400', message: 'Order cancellation failed as the order has been filled, canceled or does not exist.' }] })
        : json(200, { ok: true, status: 'cancelled' })
    }
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/system/health') return json(404, { message: 'Not found' })
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
  await page.goto('/portfolio/orders')
}

for (const [lang, theme] of [
  ['en', 'light'],
  ['en', 'dark'],
  ['ar', 'light'],
  ['ar', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  test(`Orders renders ${dir}-${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.getByTestId('open-orders-table')).toBeVisible()
    await expect(page.getByTestId('algo-orders-table')).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByTestId('open-order-row')).toHaveCount(2)
    await expect(page.getByTestId('algo-order-row')).toHaveCount(4)
    await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'الأوامر' : 'Orders')

    // Columns mirror in RTL: Instrument at the reading start, Age at the end.
    const inst = (await page.getByTestId('open-orders-table').locator('th[data-col="instrument"]').boundingBox())!
    const age = (await page.locator('th[data-col="age"]').boundingBox())!
    if (dir === 'ltr') expect(inst.x).toBeLessThan(age.x)
    else expect(inst.x).toBeGreaterThan(age.x)

    const price = page.getByTestId('open-order-row').first().locator('[data-col="price"]')
    await expect(price).toHaveCSS('text-align', 'end')
    await expect(price).toHaveText('249.40')
    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)

    await page.screenshot({ path: `${SHOTS}/orders-${dir}-${theme}.png`, fullPage: true })

    await page.getByTestId('cancel-order').first().click()
    const sheet = page.getByRole('dialog')
    await expect(sheet).toBeVisible()
    await expect.poll(() => sheet.evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
    await page.screenshot({ path: `${SHOTS}/orders-cancel-sheet-${dir}-${theme}.png` })
  })
}

test('a failed cancel keeps OKX’s message in the sheet (rtl, dark)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page, { cancelFails: true })
  await open(page, 'ar', 'dark')
  await page.getByTestId('cancel-order').first().click()
  await page.getByTestId('cancel-confirm').click()
  await expect(page.getByTestId('cancel-error')).toContainText('Order cancellation failed')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect.poll(() => page.getByRole('dialog').evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
  await page.screenshot({ path: `${SHOTS}/orders-cancel-fail-rtl-dark.png` })
})
