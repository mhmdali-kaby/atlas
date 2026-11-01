import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-173: Portfolio → History against a fake Laravel serving ATL-172's GET /api/portfolio/history
 * (cursor pages of 200) and GET /api/exports/{type}.csv. 500 closed positions and 500 fills, so the
 * virtualised table is scrolled end to end. Screenshots go to ../../evidence/ATL-173/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-173'
const NOW = Date.now()
const MIN = 60_000
const N = 500
const PAGE = 200
const INSTS = ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'XAU-USDT-SWAP', 'TSLA-USDT-SWAP', 'AAPL-USDC-SWAP', 'SOL-USDT-SWAP']
const CLASS: Record<string, string> = { BTC: 'crypto', ETH: 'crypto', SOL: 'crypto', XAU: 'commodity', TSLA: 'stock', AAPL: 'stock' }
const iso = (ms: number) => new Date(ms).toISOString().replace('Z', '000Z')
const uuid = (k: string, i: number) => `${k}0000000-0000-4000-8000-${String(i).padStart(12, '0')}`

const closed = Array.from({ length: N }, (_, i) => {
  const inst = INSTS[i % INSTS.length]!
  const win = i % 3 !== 0
  const pnl = win ? `${(12.5 + i).toFixed(2)}` : `-${(8.25 + i / 2).toFixed(2)}`
  const closedAt = NOW - (i + 1) * 37 * MIN
  return {
    id: uuid('c', i),
    pos_id: String(1_777_000_000 + i),
    inst_id: inst,
    asset_class: CLASS[inst.split('-')[0]!],
    side: i % 2 ? 'short' : 'long',
    pos_side: 'net',
    margin_mode: 'isolated',
    close_type: i % 17 === 0 ? '1' : '2',
    leverage: '3',
    ccy: inst.includes('USDC') ? 'USDC' : 'USDT',
    size: String(1 + (i % 9)),
    entry: inst.startsWith('BTC') ? '114800.5' : '412.35',
    exit: inst.startsWith('BTC') ? '115120.1' : '409.8',
    realised_pnl: pnl,
    pnl,
    fees: '-0.67',
    funding: '-0.01',
    liq_penalty: '0',
    r: i % 11 === 0 ? null : win ? '1.30' : '-1.00',
    outcome: win ? 'win' : 'loss',
    opened_at: iso(closedAt - (95 + i) * MIN),
    closed_at: iso(closedAt),
    proposal_id: i % 11 === 0 ? null : uuid('b', i),
    agent: i % 11 === 0 ? null : 'strategist',
    setup_tag: null,
    origin: i % 11 === 0 ? 'external' : 'atlas',
  }
})
const fills = Array.from({ length: N }, (_, i) => {
  const inst = INSTS[i % INSTS.length]!
  return {
    id: uuid('f', i),
    ts: iso(NOW - (i + 1) * 11 * MIN),
    inst_id: inst,
    asset_class: CLASS[inst.split('-')[0]!],
    side: i % 2 ? 'sell' : 'buy',
    pos_side: 'net',
    price: inst.startsWith('BTC') ? '114800.5' : '412.35',
    size: String(1 + (i % 5)),
    fee: '-0.0412',
    fee_ccy: 'USDT',
    fill_pnl: i % 2 ? '-3.2' : '4.1',
    exec_type: 'T',
    trade_id: String(1_900_000_000 + i),
    ord_id: String(2_900_000_000 + i),
    order_id: null,
    proposal_id: i % 4 ? uuid('b', i) : null,
    agent: i % 4 ? 'strategist' : null,
    origin: i % 4 ? 'atlas' : 'external',
  }
})

const seen: URL[] = []

async function fakeApi(page: Page) {
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
      return json(200, { data: { env: 'demo', reconciled_at: new Date(NOW).toISOString(), stale: false } })
    }
    if (url.pathname === '/api/portfolio/history') {
      seen.push(url)
      const type = url.searchParams.get('type') ?? 'fills'
      let all: Array<{ side: string; outcome?: string | null; fill_pnl?: string | null }> = type === 'closed' ? closed : fills
      const side = url.searchParams.get('side')
      if (side) all = all.filter((r) => r.side === side || (side === 'buy' && r.side === 'long') || (side === 'sell' && r.side === 'short'))
      const at = Number(url.searchParams.get('cursor') ?? '0')
      const data = all.slice(at, at + PAGE)
      const next = at + PAGE < all.length ? String(at + PAGE) : null
      return json(200, { data, meta: { type, per_page: PAGE, next_cursor: next, prev_cursor: at ? String(at - PAGE) : null } })
    }
    if (url.pathname.startsWith('/api/exports/')) {
      return route.fulfill({ status: 200, contentType: 'text/csv', headers: cors, body: 'id,inst_id\n1,BTC-USDT-SWAP\n' })
    }
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
  await page.goto('/portfolio/history')
}

const domRows = (page: Page) => page.locator('[data-testid="history-row"]').count()

for (const [lang, theme] of [
  ['ar', 'dark'],
  ['en', 'light'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  test(`500 closed positions scroll virtualised, ${dir}-${theme}, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByTestId('history-table')).toBeVisible()

    await page.getByRole('radio', { name: lang === 'ar' ? 'المراكز المغلقة' : 'Closed positions' }).click()
    await expect(page.getByTestId('history-page')).toHaveAttribute('data-type', 'closed')
    await expect(page.locator('[data-testid="history-row"][data-id="' + closed[0]!.id + '"]')).toBeVisible()
    await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'سجل التداول' : 'History')

    // Columns mirror in RTL: Closed at the reading start, How it closed at the end.
    const first = (await page.locator('th[data-col="closed"]').boundingBox())!
    const last = (await page.locator('th[data-col="close"]').boundingBox())!
    if (dir === 'ltr') expect(first.x).toBeLessThan(last.x)
    else expect(first.x).toBeGreaterThan(last.x)
    await expect(page.locator('[data-testid="history-row"]').first().locator('[data-col="pnl"]')).toHaveCSS('text-align', 'end')
    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)

    const axe = await new AxeBuilder({ page }).include('[data-testid="history-page"]').analyze()
    expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
    await page.screenshot({ path: `${SHOTS}/history-closed-${dir}-${theme}.png` })

    // Scroll end to end: pages load as the end nears, and the DOM never holds more than a window.
    const box = page.getByTestId('history-scroll')
    let maxRows = await domRows(page)
    for (let step = 0; step < 80; step++) {
      await box.evaluate((el) => el.scrollBy(0, 900))
      await page.waitForTimeout(40)
      maxRows = Math.max(maxRows, await domRows(page))
      if (await page.locator(`[data-testid="history-row"][data-id="${closed[N - 1]!.id}"]`).isVisible()) break
    }
    await expect(page.locator(`[data-testid="history-row"][data-id="${closed[N - 1]!.id}"]`)).toBeVisible()
    await expect(page.getByTestId('history-table')).toHaveAttribute('aria-rowcount', String(N + 1))
    expect(maxRows).toBeLessThan(100)
    expect(maxRows).toBeGreaterThan(10)
    // The header stays at the top of the box while the rows scroll under it.
    const head = (await page.locator('th[data-col="closed"]').boundingBox())!
    const boxRect = (await box.boundingBox())!
    expect(Math.abs(head.y - boxRect.y)).toBeLessThan(2)
    await page.screenshot({ path: `${SHOTS}/history-closed-end-${dir}-${theme}.png` })
  })
}

test('fills: 500 rows virtualised in Arabic dark; side Sell narrows the request', async ({ page }) => {
  seen.length = 0
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page)
  await open(page, 'ar', 'dark')
  await expect(page.getByTestId('history-table')).toBeVisible()
  const box = page.getByTestId('history-scroll')
  for (let step = 0; step < 80; step++) {
    await box.evaluate((el) => el.scrollBy(0, 1200))
    await page.waitForTimeout(40)
    expect(await domRows(page)).toBeLessThan(100)
    if (await page.locator(`[data-testid="history-row"][data-id="${fills[N - 1]!.id}"]`).isVisible()) break
  }
  await expect(page.locator(`[data-testid="history-row"][data-id="${fills[N - 1]!.id}"]`)).toBeVisible()

  await page.getByRole('radiogroup', { name: 'الاتجاه' }).getByRole('radio', { name: 'بيع' }).click()
  await expect.poll(() => seen.at(-1)?.searchParams.get('side')).toBe('sell')
  const axe = await new AxeBuilder({ page }).include('[data-testid="history-page"]').analyze()
  expect(axe.violations.map((v) => v.id)).toEqual([])
  await page.screenshot({ path: `${SHOTS}/history-fills-rtl-dark.png` })
})
