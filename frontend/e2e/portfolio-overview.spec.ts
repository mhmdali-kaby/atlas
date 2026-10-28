import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-22: the Portfolio Overview against a fake Laravel serving the ATL-12 contract with
 * PortfolioDemoSeeder-shaped data (30 days every 15 min from 5,000,000, downsampled to
 * ≤ 500 points, one deposit marker). Screenshots go to ../../evidence/ATL-22/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-22'
const NOW = Date.UTC(2026, 9, 28, 17, 36)
const QUARTER = 15 * 60_000

/** A deterministic random walk like PortfolioDemoSeeder's, as decimal strings. */
function walk(): Array<{ ts: number; equity_usd: string }> {
  let seed = 7
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
  let cents = 500_000_000
  const out: Array<{ ts: number; equity_usd: string }> = []
  for (let i = 2880; i >= 0; i--) {
    cents += Math.round((rand() - 0.47) * 400_000)
    out.push({ ts: NOW - i * QUARTER, equity_usd: (cents / 100).toFixed(2) })
  }
  return out
}
const ALL = walk()
const LAST = ALL[ALL.length - 1]!

const SPAN: Record<string, number> = { '1D': 86_400_000, '1W': 7 * 86_400_000, '1M': 30 * 86_400_000, '3M': 92 * 86_400_000 }
function equity(range: string) {
  const from = SPAN[range] ? NOW - SPAN[range] : null
  let pts = ALL.filter((p) => from === null || p.ts >= from)
  if (pts.length > 500) {
    const every = Math.ceil(pts.length / 499)
    pts = pts.filter((_, i) => i % every === every - 1 || i === pts.length - 1)
  }
  const deposit = { id: 'f3c94440', kind: 'deposit', ccy: 'USDT', amount: '1000', amount_usd: '1000', state: '2', tx_id: null, ts: new Date(NOW - 10 * 86_400_000).toISOString() }
  return {
    range,
    from: from === null ? null : new Date(from).toISOString(),
    to: new Date(NOW).toISOString(),
    points: pts.map((p) => ({ ts: new Date(p.ts).toISOString(), equity_usd: p.equity_usd })),
    markers: from === null || Date.parse(deposit.ts) >= from ? [deposit] : [],
  }
}

function summary(stale: boolean) {
  return {
    env: 'demo',
    total_equity_usd: LAST.equity_usd,
    net_deposits_usd: '1000',
    total_pnl_usd: '135121.59',
    total_pnl_pct: '2.7019',
    pnl_since: new Date(ALL[0]!.ts).toISOString(),
    today_pnl_usd: '7219.64',
    unrealised_pnl_usd: '271.05',
    fees_paid_usd: '13.88991',
    funding_net_usd: '-0.036183',
    equity_as_of: new Date(NOW).toISOString(),
    reconciled_at: new Date(stale ? Date.now() - 7 * 60_000 : NOW).toISOString(),
    stale,
  }
}

async function fakeApi(page: Page, { stale = false } = {}) {
  const ranges: string[] = []
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
    if (url.pathname === '/api/portfolio/summary') return json(200, { data: summary(stale) })
    if (url.pathname === '/api/portfolio/equity') {
      const range = url.searchParams.get('range') ?? '1M'
      ranges.push(range)
      return json(200, { data: equity(range) })
    }
    if (url.pathname === '/api/portfolio/positions') {
      return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: [{ id: 'a', inst_id: 'BTC-USDT-SWAP' }, { id: 'b', inst_id: 'XAU-USDT-SWAP' }] } })
    }
    if (url.pathname === '/api/connections') {
      return json(200, { data: [{ id: 'c1', exchange: 'okx', env: 'demo', uid_last4: '4821', last_verified_at: new Date(NOW).toISOString() }] })
    }
    return json(404, { message: 'Not found' })
  })
  return ranges
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
  await page.goto('/')
  await expect(page).toHaveURL(/\/portfolio$/)
  await expect(page.getByTestId('hero-equity')).toBeVisible()
  await expect(page.locator('[data-testid=equity-chart] .recharts-area-curve')).toBeVisible()
}

/** x of the first and last time-axis labels, in page pixels. */
async function axisEnds(page: Page) {
  const ticks = page.locator('[data-testid=equity-chart] text.pf-x-tick')
  await expect(ticks.first()).toBeVisible()
  const boxes = await ticks.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().x))
  return { first: boxes[0]!, last: boxes[boxes.length - 1]! }
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'
    test(`Overview renders ${dir}-${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      await fakeApi(page)
      await open(page, lang, theme)
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      await expect(page.getByTestId('hero-equity')).toHaveText(`$${Number(LAST.equity_usd).toLocaleString('en-US', { minimumFractionDigits: 2 })}`)
      // Western digits only, in both languages.
      const text = await page.locator('main').innerText()
      expect(text).not.toMatch(/[٠-٩۰-۹]/)

      const { first, last } = await axisEnds(page)
      if (dir === 'ltr') expect(first).toBeLessThan(last)
      else expect(first).toBeGreaterThan(last)

      // Tiles mirror: Net deposits is the first tile at the reading start.
      const net = await page.getByTestId('tile-net-deposits').boundingBox()
      const funding = await page.getByTestId('tile-funding').boundingBox()
      if (dir === 'ltr') expect(net!.x).toBeLessThan(funding!.x)
      else expect(net!.x).toBeGreaterThan(funding!.x)

      await page.screenshot({ path: `${SHOTS}/overview-${dir}-${theme}.png`, fullPage: true })
    })
  }
}

test('switching to 1D refetches the curve with range=1D and shows the hover card', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 860 })
  const ranges = await fakeApi(page)
  await open(page, 'en', 'light')
  await page.getByRole('radio', { name: '1D' }).click()
  await expect(page.getByTestId('equity-chart')).toHaveAttribute('data-range', '1D')
  expect(ranges).toContain('1D')
  await expect(page.getByRole('radio', { name: '1D' })).toHaveAttribute('aria-checked', 'true')
  const chart = page.getByTestId('equity-chart')
  const box = (await chart.boundingBox())!
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5)
  await expect(page.locator('.recharts-tooltip-wrapper .vibrancy-popover')).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/overview-1D-hover.png` })
})

test('stale summary shows the warning pill', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 860 })
  await fakeApi(page, { stale: true })
  await open(page, 'en', 'light')
  await expect(page.getByTestId('reconciled')).toHaveText(/Last synced 7 minutes ago/)
  await page.screenshot({ path: `${SHOTS}/overview-stale.png` })
})

test('narrow window folds the ledger and hides the seal', async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 1000 })
  await fakeApi(page)
  await open(page, 'ar', 'light')
  await page.screenshot({ path: `${SHOTS}/overview-narrow-rtl.png`, fullPage: true })
})
