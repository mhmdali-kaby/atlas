import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-175: Portfolio → Performance against a fake Laravel serving ATL-174's
 * GET /api/portfolio/performance. Checks Arabic RTL in dark mode (and English light) with axe, and
 * that the "Average win / loss" figures stay inside their tile at 1024, 1180 and 1280 px in both
 * languages (the v11 design overflowed by 15–45 px below 1280). Screenshots go to ../../evidence/ATL-175/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-175'
const NOW = Date.now()
const INSTS = ['SOL-USDT-SWAP', 'BTC-USDT-SWAP', 'TSLA-USDT-SWAP', 'XAU-USDT-SWAP', 'AAPL-USDC-SWAP', 'ETH-USDT-SWAP']
const PNL = ['3120.5', '1410.2', '640', '-210.75', '-480.1', '-840.25']

const heatmap = Array.from({ length: 40 }, (_, i) => {
  const pnl = ((i * 137) % 1700) - 700
  return { weekday: 1 + (i % 7), hour: (i * 5) % 24, pnl_usd: String(pnl), trades: 1 + (i % 4), wins: i % 3, losses: 1 }
}).filter((c, i, all) => all.findIndex((x) => x.weekday === c.weekday && x.hour === c.hour) === i)

function perf(range: string) {
  return {
    env: 'demo',
    range,
    from: range === 'ALL' ? null : new Date(NOW - 30 * 864e5).toISOString(),
    to: new Date(NOW).toISOString(),
    timezone: 'Asia/Baghdad',
    trades: { total: 37, wins: 20, losses: 17, flats: 0, excluded_non_usd: 0, gross_profit_usd: '12248', gross_loss_usd: '-6767.7', net_pnl_usd: '5480.3' },
    win_rate_pct: { value: '54.05', sample_size: 37, reason: null },
    avg_win_usd: { value: '612.4', sample_size: 20, reason: null },
    avg_loss_usd: { value: '-398.1', sample_size: 17, reason: null },
    profit_factor: { value: '1.8099', sample_size: 37, reason: null },
    expectancy_r: { value: '0.3100', sample_size: 33, reason: null },
    max_drawdown: {
      usd: '2410',
      pct: '2.40',
      peak_usd: '100410',
      trough_usd: '98000',
      peak_at: '2026-10-09T09:00:00.000000Z',
      trough_at: '2026-10-14T09:00:00.000000Z',
      sample_size: 1440,
      reason: null,
    },
    sharpe: { value: '1.4200', sample_size: 62, reason: null },
    sortino: { value: null, sample_size: 7, reason: 'insufficient_data' },
    breakdowns: {
      by_instrument: INSTS.map((inst_id, i) => ({ inst_id, pnl_usd: PNL[i], trades: 6, wins: 3, losses: 3 })),
      by_asset_class: [
        { asset_class: 'crypto', pnl_usd: '3690.45', trades: 18, wins: 11, losses: 7 },
        { asset_class: 'stock', pnl_usd: '159.9', trades: 12, wins: 6, losses: 6 },
        { asset_class: 'commodity', pnl_usd: '-210.75', trades: 7, wins: 3, losses: 4 },
      ],
      by_agent: [
        { agent: 'strategist', pnl_usd: '4900', trades: 30, wins: 17, losses: 13 },
        { agent: 'portfolio_manager', pnl_usd: '120', trades: 3, wins: 2, losses: 1 },
        { agent: null, pnl_usd: '-380.3', trades: 4, wins: 1, losses: 3 },
      ],
      by_weekday: [],
      by_hour: [],
      heatmap,
    },
  }
}

const ranges: string[] = []

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
    if (url.pathname === '/api/portfolio/performance') {
      const range = url.searchParams.get('range') ?? 'ALL'
      ranges.push(range)
      return json(200, { data: perf(range) })
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
  await page.goto('/portfolio/performance')
  await expect(page.getByTestId('pp-tiles')).toBeVisible()
}

for (const [lang, theme] of [
  ['ar', 'dark'],
  ['en', 'light'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  test(`renders ${dir}-${theme} with tiles, bars and heatmap, and no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.locator('nav a[aria-current="page"]')).toHaveText(lang === 'ar' ? 'الأداء' : 'Performance')

    await expect(page.locator('[data-testid^="pp-tile-"][data-testid$="-sub"]')).toHaveCount(7)
    await expect(page.getByTestId('pp-tile-sortino')).toContainText(lang === 'ar' ? 'لا بيانات كافية' : 'Not enough data')
    await expect(page.getByTestId('pp-bars-inst').locator('svg path[fill="var(--profit)"]')).toHaveCount(3)
    await expect(page.getByTestId('pp-bars-inst').locator('svg path[fill="var(--loss)"]')).toHaveCount(3)
    await expect(page.getByRole('gridcell')).toHaveCount(7 * 24)

    // The hour axis stays left to right in Arabic: 00 sits left of 23 in every row.
    const row = page.getByRole('row').first()
    const h0 = (await row.getByRole('gridcell').first().boundingBox())!
    const h23 = (await row.getByRole('gridcell').last().boundingBox())!
    expect(h0.x).toBeLessThan(h23.x)
    // The weekday names mirror: at the reading start.
    const day = (await row.getByRole('rowheader').boundingBox())!
    if (lang === 'ar') expect(day.x).toBeGreaterThan(h23.x)
    else expect(day.x).toBeLessThan(h0.x)

    // Keyboard: one tab stop in the grid, arrows rove and show the tooltip.
    await page.locator('[role="gridcell"][tabindex="0"]').focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.getByTestId('pp-heat-tip')).toBeVisible()

    await page.getByRole('radio', { name: lang === 'ar' ? '3 أشهر' : '3M' }).click()
    await expect(page.getByTestId('performance-page')).toHaveAttribute('data-range', '3M')
    expect(ranges).toContain('3M')

    const axe = await new AxeBuilder({ page }).include('[data-testid="performance-page"]').analyze()
    expect(axe.violations).toEqual([])
    await page.screenshot({ path: `${SHOTS}/performance-${dir}-${theme}.png` })
    await page.locator('.pp-charts').screenshot({ path: `${SHOTS}/charts-${dir}-${theme}.png` })
    await page.getByTestId('pp-heat').screenshot({ path: `${SHOTS}/heatmap-${dir}-${theme}.png` })
  })
}

for (const lang of ['en', 'ar'] as const) {
  for (const width of [1024, 1180, 1280]) {
    test(`the Average win / loss figures stay inside their tile at ${width}px in ${lang}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      await fakeApi(page)
      await open(page, lang, 'light')
      const tile = (await page.getByTestId('pp-tile-avg').boundingBox())!
      const pf = (await page.getByTestId('pp-tile-pf').boundingBox())!
      await expect(page.getByTestId('pp-avg-pair')).toContainText('$612.40')
      await expect(page.getByTestId('pp-avg-pair')).toContainText('$398.10')
      // Every figure (and the slash) inside the tile's padding box, never across into Profit factor.
      const boxes = await page
        .getByTestId('pp-avg-pair')
        .locator('[data-sign], .pp-slash')
        .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ l: r.left, r: r.right, t: r.top, b: r.bottom })))
      expect(boxes.length).toBe(3)
      for (const b of boxes) {
        expect(b.l).toBeGreaterThanOrEqual(tile.x - 0.5)
        expect(b.r).toBeLessThanOrEqual(tile.x + tile.width + 0.5)
        const sameRow = Math.abs(pf.y - tile.y) < 1
        if (sameRow) {
          const overlapX = Math.min(b.r, pf.x + pf.width) - Math.max(b.l, pf.x)
          const overlapY = Math.min(b.b, pf.y + pf.height) - Math.max(b.t, pf.y)
          expect(overlapX <= 0 || overlapY <= 0).toBe(true)
        }
      }
      // And nothing scrolls sideways.
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      expect(sideways).toBeLessThanOrEqual(0)
      await page.getByTestId('pp-tiles').screenshot({ path: `${SHOTS}/tiles-${lang}-${width}.png` })
    })
  }
}
