import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-177: Portfolio → Overview's "Compare with" control and Holdings' average cost and 24h
 * change, against a fake Laravel serving ATL-176's contract
 * (docs/handoff/ATL-176-holdings-and-benchmark-api.md). BTC and Gold have history, SPY does not
 * (not_in_universe), so SPY is disabled with its tooltip. Checked in Arabic RTL dark and English
 * light, with axe. Screenshots go to ../../evidence/ATL-177/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-177'
const NOW = Date.UTC(2026, 9, 28, 17, 36)
const HOUR = 3_600_000
const SPAN: Record<string, number> = { '1D': 24 * HOUR, '1W': 7 * 24 * HOUR, '1M': 30 * 24 * HOUR, '3M': 92 * 24 * HOUR }

/** Hourly equity over the range, a gentle deterministic walk from 5,000,000. */
function equity(range: string) {
  const span = SPAN[range] ?? SPAN['3M']!
  const n = Math.min(400, Math.round(span / HOUR))
  const step = span / n
  let cents = 500_000_000
  const points = Array.from({ length: n + 1 }, (_, i) => {
    cents += Math.round(Math.sin(i / 9) * 60_000 + 9_000)
    return { ts: new Date(NOW - span + i * step).toISOString(), equity_usd: (cents / 100).toFixed(2) }
  })
  return { range, from: new Date(NOW - span).toISOString(), to: new Date(NOW).toISOString(), points, markers: [] }
}

/** ATL-176's rebased series: value_usd = base_equity × close / base_close, one point every 4 equity points. */
function benchmark(symbol: string, range: string) {
  const eq = equity(range)
  const base = eq.points[0]!
  const empty = { symbol, env: 'demo', range, from: eq.from, to: eq.to, bar: null, base: null, points: [] }
  if (symbol === 'SPY') return { ...empty, available: false, reason: 'not_in_universe', inst_id: null }
  const baseClose = symbol === 'BTC' ? 60000 : 2400
  const points = eq.points
    .filter((_, i) => i % 4 === 0)
    .map((p, i) => {
      const close = baseClose * (1 + Math.cos(i / 7) * 0.01 - 0.01 + i * 0.0004)
      return { ts: p.ts, close: close.toFixed(2), value_usd: ((Number(base.equity_usd) * close) / baseClose).toFixed(2) }
    })
  return {
    ...empty,
    available: true,
    reason: null,
    inst_id: `${symbol}-USDT-SWAP`,
    bar: '1H',
    base: { ts: base.ts, equity_usd: base.equity_usd, close: baseClose.toFixed(2), candle_ts: base.ts },
    points,
  }
}

const HOLDINGS = {
  taken_at: new Date(NOW).toISOString(),
  total_value_usd: '5236121.59',
  items: [
    { ccy: 'USDT', account: 'trading', quantity: '5099121.59', available: '5099121.59', frozen: '0', value_usd: '5099121.59', pct_of_portfolio: '97.3836', avg_cost_usd: null, change_24h_pct: null, change_24h_reason: 'no_instrument', change_24h_inst_id: null },
    { ccy: 'BTC', account: 'trading', quantity: '1.5', available: '1.5', frozen: '0', value_usd: '99000', pct_of_portfolio: '1.8907', avg_cost_usd: '60000.50', change_24h_pct: '2.4513', change_24h_reason: null, change_24h_inst_id: 'BTC-USDT' },
    { ccy: 'ETH', account: 'funding', quantity: '12', available: '12', frozen: '0', value_usd: '38000', pct_of_portfolio: '0.7257', avg_cost_usd: '3301.25', change_24h_pct: '-1.2', change_24h_reason: null, change_24h_inst_id: 'ETH-USDT' },
    { ccy: 'SOL', account: 'funding', quantity: '2', available: '2', frozen: '0', value_usd: '400', pct_of_portfolio: '0.0076', avg_cost_usd: null, change_24h_pct: null, change_24h_reason: 'no_candles', change_24h_inst_id: null },
  ],
}

function summary() {
  const last = equity('1M').points.at(-1)!
  return {
    env: 'demo',
    total_equity_usd: last.equity_usd,
    net_deposits_usd: '0',
    total_pnl_usd: '35121.59',
    total_pnl_pct: '0.7024',
    pnl_since: new Date(NOW - 30 * 24 * HOUR).toISOString(),
    today_pnl_usd: '2219.64',
    unrealised_pnl_usd: '271.05',
    fees_paid_usd: '13.88991',
    funding_net_usd: '-0.036183',
    equity_as_of: new Date(NOW).toISOString(),
    reconciled_at: new Date(NOW).toISOString(),
    stale: false,
  }
}

async function fakeApi(page: Page) {
  const calls: string[] = []
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const cors = { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/portfolio/summary') return json(200, { data: summary() })
    if (url.pathname === '/api/portfolio/equity') return json(200, { data: equity(url.searchParams.get('range') ?? '1M') })
    if (url.pathname === '/api/portfolio/benchmark') {
      calls.push(url.search)
      return json(200, { data: benchmark(url.searchParams.get('symbol')!, url.searchParams.get('range') ?? '1M') })
    }
    if (url.pathname === '/api/portfolio/holdings') return json(200, { data: HOLDINGS })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: [] } })
    if (url.pathname === '/api/connections') {
      return json(200, { data: [{ id: 'c1', exchange: 'okx', env: 'demo', uid_last4: '4821', last_verified_at: new Date(NOW).toISOString() }] })
    }
    if (url.pathname === '/api/settings') {
      return json(200, { data: { env: 'demo', version: 3, settings: { risk_profile: 'balanced', autonomy_level: 2, onboarding_completed: 1 }, presets: {} } })
    }
    return json(404, { message: 'Not found' })
  })
  return calls
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
  await expect(page).toHaveURL(new RegExp(`${path}$`))
}

const CASES = [
  { lang: 'ar', theme: 'dark', dir: 'rtl', btc: 'BTC', gold: 'الذهب', none: 'لا شيء', compare: 'قارن مع', spyTip: 'لا يتوفر سجل لـ SPY في هذا المدى على الحساب التجريبي بعد' },
  { lang: 'en', theme: 'light', dir: 'ltr', btc: 'BTC', gold: 'Gold', none: 'None', compare: 'Compare with', spyTip: 'No SPY history for this range on the demo account yet' },
] as const

for (const c of CASES) {
  test(`Overview benchmark renders ${c.dir}-${c.theme} with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const calls = await fakeApi(page)
    await open(page, '/portfolio', c.lang, c.theme)
    await expect(page.locator('html')).toHaveAttribute('dir', c.dir)
    await expect(page.locator('[data-testid=equity-chart] .recharts-area-curve')).toBeVisible()

    const group = page.getByRole('radiogroup', { name: c.compare })
    await expect(group.getByRole('radio', { name: c.none })).toHaveAttribute('aria-checked', 'true')
    const spy = group.getByRole('radio', { name: 'SPY' })
    await expect(spy).toBeDisabled()
    await expect(spy).toHaveAccessibleDescription(c.spyTip)
    await expect(page.locator('[data-segment-tip]')).toHaveAttribute('title', c.spyTip)

    await group.getByRole('radio', { name: c.btc }).click()
    const chart = page.getByTestId('equity-chart')
    await expect(chart).toHaveAttribute('data-benchmark', 'BTC')
    await expect(chart.locator('.pf-bench-line .recharts-line-curve')).toBeVisible()
    await expect(page.getByTestId('legend-bench')).toContainText('BTC')
    expect(calls.some((q) => q.includes('symbol=BTC') && q.includes('range=1M'))).toBe(true)
    // The control row and the legend sit on one line at desktop width, controls at the reading start.
    const controls = (await page.getByTestId('benchmark-control').boundingBox())!
    const legend = (await page.getByTestId('legend-bench').boundingBox())!
    if (c.dir === 'ltr') expect(controls.x).toBeLessThan(legend.x)
    else expect(controls.x).toBeGreaterThan(legend.x)
    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)

    // Hover: the frosted card carries the benchmark's change too.
    const box = (await chart.boundingBox())!
    await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5)
    await expect(page.locator('.recharts-tooltip-wrapper .vibrancy-popover')).toContainText('BTC')
    await page.screenshot({ path: `${SHOTS}/overview-bench-${c.dir}-${c.theme}.png` })
    await page.mouse.move(0, 0)

    const axe = await new AxeBuilder({ page }).include('main').analyze()
    expect(axe.violations).toEqual([])

    await group.getByRole('radio', { name: c.gold }).click()
    await expect(chart).toHaveAttribute('data-benchmark', 'XAU')
    await group.getByRole('radio', { name: c.none }).click()
    await expect(chart).not.toHaveAttribute('data-benchmark')
    await expect(chart.locator('.pf-bench-line')).toHaveCount(0)
  })

  test(`Overview benchmark legend wraps under the controls at phone width, ${c.dir}-${c.theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await fakeApi(page)
    await open(page, '/portfolio', c.lang, c.theme)
    await expect(page.locator('[data-testid=equity-chart] .recharts-area-curve')).toBeVisible()
    await page.getByRole('radiogroup', { name: c.compare }).getByRole('radio', { name: c.btc }).click()
    const legend = page.getByTestId('legend-bench')
    await expect(legend).toBeVisible()
    const width = page.viewportSize()!.width
    for (const el of [legend, page.getByTestId('legend-equity'), page.getByTestId('chart-legend')]) {
      const box = (await el.boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(width)
    }
    // The legend sits on its own line below the controls, starting at the reading start.
    const controls = (await page.getByTestId('benchmark-control').boundingBox())!
    const legendBox = (await page.getByTestId('chart-legend').boundingBox())!
    expect(legendBox.y).toBeGreaterThanOrEqual(controls.y + controls.height)
    const first = (await page.getByTestId('legend-equity').boundingBox())!
    const pageBox = (await page.getByTestId('chart-head').boundingBox())!
    if (c.dir === 'ltr') expect(first.x - pageBox.x).toBeLessThan(2)
    else expect(pageBox.x + pageBox.width - (first.x + first.width)).toBeLessThan(2)
    // No segment of either control is squeezed below its content width.
    const squeezed = await page.locator('[role=radiogroup] [role=radio]').evaluateAll((els) =>
      els.filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent),
    )
    expect(squeezed).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: `${SHOTS}/overview-bench-390-${c.dir}-${c.theme}.png`, fullPage: true })
  })

  test(`Holdings cost columns render ${c.dir}-${c.theme} with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page)
    await open(page, '/portfolio/holdings', c.lang, c.theme)
    await expect(page.getByTestId('holdings-table')).toBeVisible()
    const row = (ccy: string) => page.locator(`[data-testid=holding-row][data-ccy=${ccy}]`)
    await expect(row('BTC').locator('td[data-col=avgCost]')).toHaveText('$60,000.50')
    await expect(row('BTC').locator('[data-sign=profit]')).toContainText('+2.45%')
    await expect(row('ETH').locator('[data-sign=loss]')).toContainText('−1.20%')
    await expect(row('SOL').getByTestId('no-change')).toContainText('—')
    await expect(row('USDT').getByTestId('no-avg-cost')).toContainText('—')
    // Numbers stay right-aligned at the trailing edge in both directions.
    const cell = row('BTC').locator('td[data-col=change]')
    await expect(cell).toHaveCSS('text-align', /right|end/)
    await page.screenshot({ path: `${SHOTS}/holdings-${c.dir}-${c.theme}.png`, fullPage: true })

    const axe = await new AxeBuilder({ page }).include('main').analyze()
    expect(axe.violations).toEqual([])
  })
}
