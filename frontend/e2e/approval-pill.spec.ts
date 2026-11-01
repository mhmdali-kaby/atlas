import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-180: the approval pill in the toolbar and the "trade waiting for your approval" toast, against
 * a fake Laravel serving ATL-162's GET /api/proposals?status=awaiting_owner. No Reverb here, so a new
 * proposal reaches the page through the list refetch (a visibility change stands in for the 15 s
 * poll). Screenshots go to ../../evidence/ATL-180/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'ar', theme: 'system' }
const SHOTS = '../../evidence/ATL-180'

const HEALTH = {
  env: 'demo',
  as_of: new Date().toISOString(),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

function waiting(id: string, msLeft: number, inst = 'SOL-USDT-SWAP', side: 'buy' | 'sell' = 'buy') {
  return {
    id,
    env: 'demo',
    version: 1,
    parent_id: null,
    author: 'strategist',
    inst_id: inst,
    side,
    status: 'awaiting_owner',
    approval_expires_at: new Date(Date.now() + msLeft).toISOString(),
  }
}

async function fakeApi(page: Page, initial: ReturnType<typeof waiting>[]) {
  const state = { list: initial, listCalls: 0 }
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
    if (url.pathname === '/api/system/health') return json(200, { data: HEALTH })
    if (url.pathname === '/api/proposals' && url.searchParams.get('status') === 'awaiting_owner') {
      state.listCalls += 1
      return json(200, { data: state.list, links: {}, meta: {} })
    }
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date().toISOString(), items: [] } })
    return json(404, { message: 'Not found' })
  })
  return state
}

async function axe(page: Page, ...include: string[]) {
  let builder = new AxeBuilder({ page })
  for (const sel of include) builder = builder.include(sel)
  const results = await builder.analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

async function refetch(page: Page) {
  // TanStack Query refetches stale queries when the page becomes visible again.
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')))
}

for (const theme of ['light', 'dark'] as const) {
  test(`Arabic ${theme}: the pill counts down beside Stop and a new proposal toasts, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const api = await fakeApi(page, [waiting('p1', 354_500)])
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
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)

    const pill = page.getByTestId('approval-pill')
    await expect(pill).toBeVisible()
    await expect(page.getByTestId('approval-pill-label')).toHaveText('صفقة تنتظر موافقتك')
    await expect(page.getByTestId('approval-pill-time')).toHaveText(/^5:5\d$/)
    await expect(pill).toHaveAccessibleName(/^صفقة تنتظر موافقتك، والوقت المتبقي 5:5\d\. افتحها في غرفة العمليات\.$/)
    // In RTL the pill sits on the start side of Stop: to its right.
    const pillBox = (await pill.boundingBox())!
    const stopBox = (await page.getByTestId('kill-switch').boundingBox())!
    expect(pillBox.x).toBeGreaterThan(stopBox.x)
    expect(await axe(page, '[data-testid="approval-pill"]')).toEqual([])

    // A second proposal arrives: a toast on this Portfolio page.
    api.list = [...api.list, waiting('p2', 600_000, 'XAU-USDT-SWAP', 'sell')]
    await refetch(page)
    const toast = page.getByRole('status').filter({ hasText: 'صفقة تنتظر موافقتك' })
    await expect(toast).toBeVisible()
    await expect(toast).toContainText(/XAU-USDT-SWAP بيع\. وافق عليها قبل \d\d:\d\d في غرفة العمليات\./)
    await expect(page.getByTestId('approval-pill-label')).toHaveText('صفقتان تنتظران موافقتك')
    expect(await axe(page, '[data-testid="approval-pill"]', 'ol:has(li)')).toEqual([])
    await page.screenshot({ path: `${SHOTS}/pill-toast-rtl-${theme}.png` })
  })

  test(`Arabic ${theme}: under a minute the pill turns orange and says so, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page, [waiting('p1', 45_000)])
    await page.addInitScript(
      ([th]) => {
        localStorage.setItem('atlas.lang', 'ar')
        localStorage.setItem('atlas.theme', th)
      },
      [theme] as const,
    )
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/portfolio/positions')
    const pill = page.getByTestId('approval-pill')
    await expect(pill).toHaveClass(/is-warn/)
    await expect(page.getByTestId('approval-pill-label')).toHaveText('الدقيقة الأخيرة: صفقة تنتظر موافقتك')
    await expect(page.getByTestId('approval-pill-time')).toHaveText(/^0:[0-4]\d$/)
    expect(await axe(page, '[data-testid="approval-pill"]')).toEqual([])
    await page.screenshot({ path: `${SHOTS}/pill-warn-rtl-${theme}.png`, clip: { x: 0, y: 0, width: 1440, height: 120 } })
  })
}

test('English: clicking the pill opens the War Room on the nearest proposal', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page, [waiting('p-later', 900_000), waiting('p-first', 300_000)])
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'en'))
  await page.goto('/portfolio/positions')
  const pill = page.getByTestId('approval-pill')
  await expect(page.getByTestId('approval-pill-label')).toHaveText('2 to approve')
  await page.screenshot({ path: `${SHOTS}/pill-ltr-light.png`, clip: { x: 0, y: 0, width: 1280, height: 120 } })
  await pill.click()
  await expect(page).toHaveURL(/\/warroom\?proposal=p-first$/)
})

test('nothing waiting: no pill', async ({ page }) => {
  const api = await fakeApi(page, [])
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'en'))
  await page.goto('/portfolio/positions')
  await expect(page.getByTestId('kill-switch')).toBeVisible()
  await expect.poll(() => api.listCalls).toBeGreaterThan(0)
  await expect(page.getByTestId('approval-pill')).toHaveCount(0)
})

test('the 390 px toolbar keeps the pill as icon and time', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page, [waiting('p1', 300_000)])
  await page.addInitScript(() => localStorage.setItem('atlas.lang', 'ar'))
  await page.goto('/portfolio/positions')
  await expect(page.getByTestId('approval-pill')).toBeVisible()
  await expect(page.getByTestId('approval-pill-label')).toBeHidden()
  await expect(page.getByTestId('approval-pill-time')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/toolbar-390-rtl.png` })
})
