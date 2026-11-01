import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-149: the Stop trading button, its sheet and the paused banner, against a fake Laravel
 * serving ATL-148's system API. Decision 251: Stop is one press, keeps every stop, pauses.
 * Screenshots go to ../../evidence/ATL-149/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'ar', theme: 'system' }
const SHOTS = '../../evidence/ATL-149'
const NOW = Date.now()

const RUNNING = {
  env: 'demo',
  as_of: new Date(NOW).toISOString(),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}
const PAUSED = {
  ...RUNNING,
  trading: { paused: true, reason: 'Paused by the owner.', paused_at: new Date(NOW).toISOString(), accepting_new_orders: false },
}
const REPORT = {
  ok: true,
  env: 'demo',
  flatten: false,
  at: new Date(NOW).toISOString(),
  paused: true,
  cancelled: 3,
  cancelled_orders: 2,
  cancelled_algos: 1,
  already_done: 0,
  closed: 0,
  stops_kept: 2,
  failed: [],
  items: [
    { kind: 'order', inst_id: 'SOL-USDT-SWAP', ref: '1001', action: 'cancelled', attempts: 1 },
    { kind: 'order', inst_id: 'ETH-USDT-SWAP', ref: '1002', action: 'cancelled', attempts: 1 },
    { kind: 'algo', inst_id: 'BTC-USDT-SWAP', ref: '7001', action: 'cancelled', attempts: 1, algo_kind: 'tp' },
    { kind: 'algo', inst_id: 'BTC-USDT-SWAP', ref: '7002', action: 'kept', attempts: 0, algo_kind: 'sl' },
    { kind: 'algo', inst_id: 'XAU-USDT-SWAP', ref: '7003', action: 'kept', attempts: 0, algo_kind: 'oco' },
  ],
}
const POSITIONS = [
  { id: 'p1', inst_id: 'BTC-USDT-SWAP', side: 'long', pos_side: 'net', size: '25', leverage: '5', entry: '67250.5', mark: '68120.3', liq_px: '54112.8', margin: '3362.5', settle_ccy: 'USDT', upl_usd: '217.45', upl_pct: '6.46', margin_mode: 'isolated', opened_at: new Date(NOW - 3_600_000).toISOString(), stop: null, take_profits: null, agent: null, proposal_id: null },
  { id: 'p2', inst_id: 'XAU-USDT-SWAP', side: 'short', pos_side: 'net', size: '400', leverage: '3', entry: '2741.8', mark: '2728.4', liq_px: '3618.9', margin: '3655.7', settle_ccy: 'USDT', upl_usd: '53.6', upl_pct: '1.46', margin_mode: 'isolated', opened_at: new Date(NOW - 7_200_000).toISOString(), stop: null, take_profits: null, agent: null, proposal_id: null },
]

async function fakeApi(page: Page) {
  const state = { health: RUNNING as typeof RUNNING | typeof PAUSED, kills: [] as unknown[] }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = {
      'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*',
      'Access-Control-Allow-Credentials': 'true',
    }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/system/health') return json(200, { data: state.health })
    if (url.pathname === '/api/system/kill' && req.method() === 'POST') {
      state.kills.push(req.postDataJSON())
      state.health = PAUSED
      return json(200, REPORT)
    }
    if (url.pathname === '/api/portfolio/orders') {
      return json(200, {
        data: {
          env: 'demo',
          orders: [{ id: 'o1' }, { id: 'o2' }],
          algo_orders: [
            { kind: 'tp', protects: false },
            { kind: 'sl', protects: true },
            { kind: 'oco', protects: true },
          ],
        },
      })
    }
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: POSITIONS } })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
  return state
}

for (const theme of ['light', 'dark'] as const) {
  test(`Arabic ${theme}: open Stop with the keyboard, press Stop trading, see the stops kept`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const api = await fakeApi(page)
    await page.addInitScript(
      ([th]) => {
        localStorage.setItem('atlas.lang', 'ar')
        localStorage.setItem('atlas.theme', th)
      },
      [theme] as const,
    )
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/portfolio/positions')
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')

    const stop = page.getByRole('button', { name: 'إيقاف التداول: إلغاء الأوامر وإيقاف أطلس مؤقتًا' })
    await expect(stop).toBeVisible()
    await expect(stop).toHaveText('إيقاف التداول')

    // Reach it with Tab only.
    let reached = false
    for (let i = 0; i < 40 && !reached; i += 1) {
      await page.keyboard.press('Tab')
      reached = await stop.evaluate((el) => el === document.activeElement)
    }
    expect(reached).toBe(true)
    await page.keyboard.press('Enter')

    // The dialog's name follows its title, which becomes the report's after Stop.
    const sheet = page.getByRole('dialog')
    await expect(page.getByRole('dialog', { name: 'إيقاف التداول كله؟' })).toBeVisible()
    await expect(sheet).toContainText('إبقاء أوامر الوقف (2) على OKX')
    // Focus starts on Cancel, so a stray Enter never stops trading.
    await expect(sheet.getByRole('button', { name: 'إلغاء', exact: true })).toBeFocused()
    await page.waitForTimeout(500)
    await page.screenshot({ path: `${SHOTS}/sheet-rtl-${theme}.png` })

    // One press, no typing: Tab to Stop trading and press it.
    await page.keyboard.press('Tab')
    await expect(sheet.getByRole('button', { name: 'إيقاف التداول', exact: true })).toBeFocused()
    await page.keyboard.press('Enter')

    const report = sheet.getByTestId('kill-report')
    await expect(report).toBeVisible()
    await expect(sheet).toContainText('التداول موقوف مؤقتًا')
    await expect(report.getByTestId('kill-cancelled')).toContainText('3 أوامر')
    await expect(report.getByTestId('kill-kept')).toContainText('أمرا وقف')
    await expect(report.getByTestId('kill-kept')).toContainText('يحتفظ كل مركز مفتوح بأمر الوقف الخاص به على OKX.')
    expect(api.kills).toEqual([{ confirm: 'KILL', flatten: false }])

    // Behind the sheet, the toolbar now reads Stopped and the banner shows the pause.
    await expect(page.getByTestId('trading-banner')).toContainText('أوقفتَ التداول')
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/report-rtl-${theme}.png` })

    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()
    await expect(page.getByTestId('kill-switch')).toBeFocused()
    await expect(page.getByTestId('kill-switch')).toHaveText('متوقف')
    await page.waitForTimeout(400)
    await page.screenshot({ path: `${SHOTS}/banner-rtl-${theme}.png` })
  })
}

test('English, close-all on: a short press sends nothing, a 1.5 s hold sends flatten true', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  const api = await fakeApi(page)
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'en'))
  await page.goto('/portfolio/positions')
  await page.getByTestId('kill-switch').click()
  const sheet = page.getByRole('dialog')
  await expect(page.getByRole('dialog', { name: 'Stop all trading?' })).toBeVisible()
  await sheet.getByRole('switch').click()
  await expect(sheet).toContainText('Also close all 2 positions now')
  const hold = sheet.getByTestId('kill-hold')
  await hold.hover()
  await page.mouse.down()
  await page.waitForTimeout(600)
  await page.mouse.up()
  await expect(sheet).toContainText('Released too early')
  await page.waitForTimeout(1200)
  expect(api.kills).toHaveLength(0)
  await page.screenshot({ path: `${SHOTS}/hold-early-ltr.png` })

  await page.mouse.down()
  await expect(sheet.getByTestId('kill-report')).toBeVisible({ timeout: 4000 })
  await page.mouse.up()
  expect(api.kills).toEqual([{ confirm: 'KILL', flatten: true }])
})

test('the 390 px toolbar keeps Stop as an icon circle', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page)
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'ar'))
  await page.goto('/portfolio/positions')
  const stop = page.getByTestId('kill-switch')
  await expect(stop).toBeVisible()
  const box = (await stop.boundingBox())!
  expect(Math.round(box.width)).toBe(28)
  await page.screenshot({ path: `${SHOTS}/toolbar-390-rtl.png` })
})
