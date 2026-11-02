import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { overfitResult } from '../src/features/backtester/fixtures'

/*
 * ATL-210: a done run's results (metrics, overfit warning, equity curve, walk-forward splits, trades)
 * against a fake Laravel serving ATL-208's GET /api/backtests/{id}, in English light and Arabic dark
 * (the equity chart's time axis mirrored), with no axe violations. Screenshots go to
 * ../../evidence/ATL-210/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-210'
const RUN_ID = '9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10'

const REQUEST = {
  rule_spec: {
    name: 'sma_10_30_cross',
    side: 'long',
    indicators: { sma10: { kind: 'sma', window: 10 }, sma30: { kind: 'sma', window: 30 } },
    entry: [{ left: 'sma10', op: 'crosses_above', right: 'sma30' }],
    exit: [{ left: 'sma10', op: 'crosses_below', right: 'sma30' }],
    stop_pct: 3,
    take_profit_pct: null,
  },
  inst_ids: ['BTC-USDT-SWAP'],
  bar: '1H',
  period: { start: '2026-04-30T21:00:00Z', end: '2026-09-30T21:00:00Z' },
  splits: 3,
  slippage_bps: 2,
}

const ROW = {
  id: RUN_ID,
  env: 'demo',
  status: 'done',
  rule_name: 'sma_10_30_cross',
  side: 'long',
  inst_ids: ['BTC-USDT-SWAP'],
  bar: '1H',
  period: REQUEST.period,
  n_trades: 18,
  overfit_warning: true,
  data_gap: false,
  error: null,
  error_reason: null,
  created_at: '2026-11-02T08:15:00.000000Z',
  started_at: '2026-11-02T08:15:01.000000Z',
  finished_at: '2026-11-02T08:15:07.000000Z',
}

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
    if (url.pathname === '/api/backtests') return json(200, { env: 'demo', data: [ROW] })
    if (url.pathname === `/api/backtests/${RUN_ID}`) return json(200, { ...ROW, request: REQUEST, result: overfitResult() })
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
  await page.goto(`/backtester/run?id=${RUN_ID}`)
}

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  const ar = lang === 'ar'

  test(`backtester results, ${dir}-${theme}: metrics, overfit warning, mirrored equity chart, no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)

    await expect(page.getByTestId('bt-results')).toBeVisible()
    await expect(page.getByTestId('bt-overfit')).toContainText(ar ? 'على الأرجح مُفرط في المواءمة' : 'Probably overfit')
    await expect(page.getByTestId('bt-tiles').getByTestId(/^bt-m-[a-z]+$/)).toHaveCount(10)
    await expect(page.getByTestId('bt-folds').locator('tbody tr')).toHaveCount(3)

    // the chart drew its line, and runs right to left in Arabic: the first point sits at the right
    const chart = page.getByTestId('bt-equity-chart')
    await expect(chart).toHaveAttribute('data-reversed', ar ? 'true' : 'false')
    const line = chart.locator('.recharts-area-curve').first()
    await expect(line).toBeVisible()
    const xs = await chart.locator('text.bt-x-tick').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().x))
    expect(xs).toHaveLength(5)
    // ticks are oldest first: in Arabic the oldest date sits at the right
    if (ar) expect(xs[0]!).toBeGreaterThan(xs[4]!)
    else expect(xs[0]!).toBeLessThan(xs[4]!)
    // the value axis sits on the leading side
    const box = (await chart.boundingBox())!
    const yx = await chart.locator('text.bt-y-tick').first().evaluate((e) => e.getBoundingClientRect().x)
    if (ar) expect(yx).toBeGreaterThan(box.x + box.width / 2)
    else expect(yx).toBeLessThan(box.x + box.width / 2)
    await chart.screenshot({ path: `${SHOTS}/chart-${dir}-${theme}.png` })

    // the frosted tooltip on hover
    await chart.hover({ position: { x: box.width / 2, y: box.height / 2 } })
    await expect(page.getByTestId('bt-tip')).toBeVisible()

    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)
    await page.mouse.move(0, 0)
    const axe = await new AxeBuilder({ page }).include('main').analyze()
    expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
    await page.screenshot({ path: `${SHOTS}/results-${dir}-${theme}.png` })
    await page.getByTestId('bt-folds').screenshot({ path: `${SHOTS}/folds-${dir}-${theme}.png` })
    await page.getByTestId('bt-trades').screenshot({ path: `${SHOTS}/trades-${dir}-${theme}.png` })
  })
}
