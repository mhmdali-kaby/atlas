import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-151: Portfolio → Positions "Close" sheet against a fake Laravel serving ATL-148's
 * POST /api/portfolio/positions/{id}/close contract, with a verified OKX demo connection so
 * the action is shown. Screenshots go to ../../evidence/ATL-151/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-151'
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

const CONNECTION = {
  id: 'c1',
  exchange: 'okx',
  env: 'demo',
  label: 'OKX demo',
  api_key_last4: '9fz1',
  has_secret: true,
  has_passphrase: true,
  permissions: ['read_only', 'trade'],
  account_mode: 'futures',
  position_mode: 'net_mode',
  uid_last4: '6123',
  last_verified_at: new Date(NOW - 12 * 60_000).toISOString(),
  last_test_error: null,
  created_at: '2026-10-28T07:00:00.000000Z',
  updated_at: '2026-10-28T08:00:00.000000Z',
}

async function fakeApi(page: Page) {
  let closed = false
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
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
          unrealised_pnl_usd: closed ? '0' : '217.45',
          fees_paid_usd: '13.88991',
          funding_net_usd: '-0.036183',
          equity_as_of: new Date(NOW).toISOString(),
          reconciled_at: new Date(NOW).toISOString(),
          stale: false,
        },
      })
    }
    if (url.pathname === '/api/portfolio/positions') {
      return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: closed ? [] : [BTC] } })
    }
    if (url.pathname === `/api/portfolio/positions/${BTC.id}/close` && method === 'POST') {
      closed = true
      return json(200, { ok: true, env: 'demo', inst_id: BTC.inst_id, status: 'closed', closed: 1, cancelled_algos: 1, failed: [] })
    }
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

test('open and confirm the close from the keyboard (Arabic, RTL)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await fakeApi(page)
  await open(page, 'ar', 'light')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.getByTestId('positions-table')).toBeVisible()

  const closeButton = page.getByRole('button', { name: 'إغلاق مركز الشراء على BTC-USDT-SWAP' })
  await closeButton.focus()
  await expect(closeButton).toBeFocused()
  await page.keyboard.press('Enter')

  const sheet = page.getByRole('dialog', { name: 'إغلاق مركز الشراء على BTC-USDT-SWAP؟' })
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('25')
  await expect(sheet).toContainText('217.45')
  await page.waitForTimeout(400)
  await page.screenshot({ path: `${SHOTS}/close-sheet-ar.png` })

  // The first tabbable control is "Keep position"; one more Tab reaches "Close position".
  await expect(sheet.getByRole('button', { name: 'إبقاء المركز' })).toBeFocused()
  await page.keyboard.press('Tab')
  const confirm = sheet.getByRole('button', { name: 'إغلاق المركز' })
  await expect(confirm).toBeFocused()
  await page.keyboard.press('Enter')

  await expect(page.getByRole('heading', { name: 'لا توجد مراكز مفتوحة' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/close-sheet-ar-done.png` })
})
