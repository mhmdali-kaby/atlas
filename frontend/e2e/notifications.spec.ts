import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-217: the toolbar bell and its notifications list, against a fake Laravel serving ATL-214's
 * GET /api/notifications, POST /api/notifications/{id}/read and POST /api/notifications/read-all
 * (docs/handoff/ATL-214-notifications.md). Screenshots go to ../../evidence/ATL-217/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-217'
const HEALTH = {
  env: 'demo',
  as_of: new Date().toISOString(),
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const MIN = 60_000

function fixtures() {
  const base = { env: 'demo', channels: { in_app: true, browser: true, telegram: false } }
  return [
    {
      ...base,
      id: 'n4',
      event: 'order_filled',
      severity: 'info',
      title_key: 'notifications.events.order_filled.title',
      params: { order_id: 'o1', proposal_id: 'p1', inst_id: 'AAPL-USDC-SWAP', side: 'buy', purpose: 'entry', filled_sz: '300', avg_px: '262.40' },
      link: '/portfolio/orders?inst_id=AAPL-USDC-SWAP',
      read_at: null,
      created_at: ago(5 * MIN),
    },
    {
      ...base,
      id: 'n3',
      event: 'news_alert',
      severity: 'warning',
      title_key: 'notifications.events.news_alert.title',
      params: { message_id: 'm1', headline: 'Gold slips as Treasury yields climb before the Fed decision', source_name: 'Reuters', importance: '4', inst_ids: ['XAU-USDT-SWAP'] },
      link: '/warroom?message=m1',
      read_at: null,
      created_at: ago(20 * MIN),
    },
    {
      ...base,
      id: 'n2',
      event: 'circuit_breaker',
      severity: 'critical',
      title_key: 'notifications.events.circuit_breaker.title',
      params: { name: 'daily_loss', reason: 'Daily loss 4.1%', since: ago(40 * MIN), self_clearing: true },
      link: '/warroom',
      read_at: null,
      created_at: ago(40 * MIN),
    },
    {
      ...base,
      id: 'n1',
      event: 'stop_hit',
      severity: 'warning',
      title_key: 'notifications.events.stop_hit.title',
      params: { algo_order_id: 'a1', inst_id: 'NVDA-USDT-SWAP', side: 'sell', trigger_px: '181.20', actual_px: '181.05', sz: '120' },
      link: '/portfolio/positions?inst_id=NVDA-USDT-SWAP',
      read_at: ago(26 * 60 * MIN),
      created_at: ago(27 * 60 * MIN),
    },
  ]
}

async function fakeApi(page: Page) {
  const state = { rows: fixtures(), posts: [] as string[] }
  const unread = () => state.rows.filter((r) => !r.read_at).length
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
    if (url.pathname === '/api/proposals') return json(200, { data: [], links: {}, meta: {} })
    if (url.pathname === '/api/notifications' && req.method() === 'GET') {
      return json(200, { data: state.rows, meta: { env: 'demo', per_page: 50, unread_count: unread(), next_cursor: null, prev_cursor: null } })
    }
    if (url.pathname === '/api/notifications/read-all' && req.method() === 'POST') {
      state.posts.push(url.pathname)
      const marked = unread()
      state.rows = state.rows.map((r) => ({ ...r, read_at: r.read_at ?? new Date().toISOString() }))
      return json(200, { data: { env: 'demo', marked, unread_count: 0 } })
    }
    const read = /^\/api\/notifications\/([^/]+)\/read$/.exec(url.pathname)
    if (read && req.method() === 'POST') {
      state.posts.push(url.pathname)
      const row = state.rows.find((r) => r.id === read[1])
      if (!row) return json(404, { message: 'Not found' })
      row.read_at ??= new Date().toISOString()
      return json(200, { data: row })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

/** A locator's text without the bidi isolates the figures and IDs are wrapped in. */
const plain = (page: Page, testId: string, n: number) =>
  page
    .getByTestId(testId)
    .nth(n)
    .evaluate((el) => (el.textContent ?? '').replace(/[\u2066-\u2069]/g, ''))

async function axe(page: Page, ...include: string[]) {
  let builder = new AxeBuilder({ page })
  for (const sel of include) builder = builder.include(sel)
  const results = await builder.analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

async function boot(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ reducedMotion: 'reduce' })
}

test('English light: the list shows the notifications; opening one goes to its link and marks it read; Mark all read clears the count', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const api = await fakeApi(page)
  await boot(page, 'en', 'light')
  await page.goto('/warroom')

  const bell = page.getByTestId('nt-bell')
  await expect(bell).toHaveAccessibleName('Notifications, 3 unread, 1 urgent')
  await expect(page.getByTestId('nt-badge')).toHaveText('3')

  await bell.click()
  const panel = page.getByTestId('nt-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('heading', { name: 'Notifications' })).toBeFocused()
  const rows = panel.getByTestId('nt-row')
  await expect(rows).toHaveCount(4)
  // Newest first, grouped by Baghdad day.
  await expect(rows.nth(0)).toContainText('Order filled')
  expect(await plain(page, 'nt-row', 0)).toContain('Bought 300 contracts of AAPL-USDC-SWAP at 262.40.')
  expect(await plain(page, 'nt-row', 1)).toContain('News on XAU-USDT-SWAP')
  expect(await plain(page, 'nt-row', 1)).toContain('Reuters, importance 4')
  await expect(rows.nth(2)).toContainText('Trading paused by a circuit breaker')
  expect(await plain(page, 'nt-row', 3)).toContain('Stop-loss hit on NVDA-USDT-SWAP')
  await expect(panel.getByRole('heading', { name: 'Yesterday' })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/list-en-light.png` })

  // Opening a row: navigate to its link, mark it read, close the list.
  await rows.nth(0).getByRole('link').click()
  await expect(page).toHaveURL(/\/portfolio\/orders\?inst_id=AAPL-USDC-SWAP$/)
  await expect(panel).toBeHidden()
  await expect.poll(() => api.posts).toContain('/api/notifications/n4/read')
  await expect(bell).toHaveAccessibleName('Notifications, 2 unread, 1 urgent')
  await expect(page.getByTestId('nt-badge')).toHaveText('2')

  await bell.click()
  await expect(panel.locator('[data-id="n4"]')).not.toHaveAttribute('data-unread')
  await panel.getByTestId('nt-markall').click()
  await expect(page.getByTestId('nt-badge')).toHaveCount(0)
  await expect(bell).toHaveAccessibleName('Notifications, none unread')
  await expect(panel.getByTestId('nt-markall')).toBeDisabled()
  await expect.poll(() => api.posts).toContain('/api/notifications/read-all')

  // Escape closes the list and returns focus to the bell.
  await page.keyboard.press('Escape')
  await expect(panel).toBeHidden()
  await expect(bell).toBeFocused()
})

test('Arabic dark at 390 px: the list fits with no horizontal scroll, and axe reports no violations', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page)
  await boot(page, 'ar', 'dark')
  await page.goto('/portfolio')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)

  const bell = page.getByTestId('nt-bell')
  await expect(bell).toBeVisible()
  await bell.click()
  const panel = page.getByTestId('nt-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('nt-row')).toHaveCount(4)
  await expect(panel.getByTestId('nt-row').first()).toContainText('نُفّذ أمر')

  const box = (await panel.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  const overflow = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    panel: (() => {
      const el = document.querySelector('.nt-scroll')!
      return el.scrollWidth - el.clientWidth
    })(),
  }))
  expect(overflow).toEqual({ doc: 0, panel: 0 })

  expect(await axe(page, '[data-testid="nt-panel"]', '[data-testid="nt-bell"]')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/list-ar-dark-390.png` })
})
