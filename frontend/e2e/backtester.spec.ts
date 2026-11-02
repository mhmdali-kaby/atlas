import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-209: the Backtester form against a fake Laravel serving ATL-208's /api/backtests and ATL-119's
 * /api/instruments. The form, a 422 shown beside its field, and the running state, in English light
 * and Arabic dark, with no axe violations. Screenshots go to ../../evidence/ATL-209/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-209'
const RUN_ID = '9d7c1f7e-6a8b-4c55-9a4e-2f1d8c3b7a10'
const INSTRUMENTS = ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XAU-USDT-SWAP', 'XAG-USDT-SWAP', 'TSLA-USDT-SWAP']
const nowIso = () => new Date().toISOString().replace('Z', '000Z')

const runRow = (id: string, name: string, insts: string[], status: string, ago: number) => ({
  id,
  env: 'demo',
  status,
  rule_name: name,
  side: 'long',
  inst_ids: insts,
  bar: '1H',
  period: { start: '2026-05-01T00:00:00Z', end: '2026-10-01T00:00:00Z' },
  n_trades: status === 'done' ? 18 : null,
  overfit_warning: status === 'done' ? false : null,
  data_gap: status === 'done' ? false : null,
  error: status === 'failed' ? 'too_many_bars' : null,
  error_reason: status === 'failed' ? 'the period is 73440 1m bars per instrument; backtest.max_bars is 50000' : null,
  created_at: new Date(Date.now() - ago).toISOString(),
  started_at: new Date(Date.now() - ago + 500).toISOString(),
  finished_at: status === 'done' || status === 'failed' ? new Date(Date.now() - ago + 6000).toISOString() : null,
})

const RUNS = [
  runRow('b14', 'sma_10_30_cross', ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'], 'done', 3_600_000),
  runRow('b13', 'rsi_14_reversal', ['ETH-USDT-SWAP'], 'failed', 26 * 3_600_000),
  runRow('b12', 'ema_20_50_trend', ['SOL-USDT-SWAP', 'BTC-USDT-SWAP', 'ETH-USDT-SWAP'], 'done', 50 * 3_600_000),
]

interface Fake {
  /** The POST answer: a 422 with these errors, or the 202. */
  refuse?: Record<string, string[]>
  posted?: unknown[]
}

async function fakeApi(page: Page, fake: Fake = {}) {
  let started: unknown = null
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
    if (url.pathname === '/api/backtests' && req.method() === 'POST') {
      const body = req.postDataJSON() as Record<string, unknown>
      fake.posted?.push(body)
      if (fake.refuse) return json(422, { message: 'The rule has a problem.', errors: fake.refuse })
      started = body
      return json(202, { ok: true, id: RUN_ID, status: 'queued', created_at: nowIso() })
    }
    if (url.pathname === '/api/backtests') {
      const mine = started ? [{ ...runRow(RUN_ID, 'sma_10_30_cross', ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'], 'running', 1000) }] : []
      return json(200, { env: 'demo', data: [...mine, ...RUNS] })
    }
    if (url.pathname === `/api/backtests/${RUN_ID}`) {
      return json(200, { ...runRow(RUN_ID, 'sma_10_30_cross', ['BTC-USDT-SWAP', 'XAU-USDT-SWAP'], 'running', 4000), request: started, result: null })
    }
    if (url.pathname === '/api/instruments') {
      const q = (url.searchParams.get('q') ?? '').toUpperCase()
      return json(200, { data: INSTRUMENTS.filter((i) => i.startsWith(q)).map((inst_id) => ({ inst_id, asset_class: 'crypto', blocked: false })) })
    }
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', path = '/backtester') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(path)
}

async function noAxe(page: Page) {
  const axe = await new AxeBuilder({ page }).include('main').include('[data-testid="bt-runs"]').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
}

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  const ar = lang === 'ar'

  test(`backtester form, ${dir}-${theme}: build, refuse, run, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const fake: Fake = { posted: [] }
    await fakeApi(page, fake)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByTestId('bt-form')).toBeVisible()
    await expect(page.getByTestId('area-switch').getByRole('link', { name: ar ? 'الاختبار التاريخي' : 'Backtester' })).toHaveAttribute('aria-current', 'true')
    await expect(page.getByTestId('bt-run-row')).toHaveCount(3)

    // add XAU from the search
    await page.getByRole('combobox', { name: ar ? 'أضف أداة' : 'Add instrument' }).fill('XA')
    await page.getByRole('option', { name: 'XAU-USDT-SWAP' }).click()
    await expect(page.getByTestId('bt-inst-chip')).toHaveCount(2)
    await expect(page.getByTestId('bt-sentence')).toContainText('XAU')
    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/form-${dir}-${theme}.png`, fullPage: true })

    // the RTL mirror: the first column sits at the right in Arabic
    const cols = page.locator('.bt-col')
    const a = (await cols.nth(0).boundingBox())!
    const b = (await cols.nth(1).boundingBox())!
    if (dir === 'ltr') expect(a.x).toBeLessThan(b.x)
    else expect(a.x).toBeGreaterThan(b.x)

    // the server refuses a condition: its reason sits beside the row, focused
    fake.refuse = { 'rule_spec.entry.0.right': ['rule_spec: condition uses unknown indicator ‘sma99’'] }
    await page.getByTestId('bt-run').click()
    await expect(page.getByTestId('bt-banner')).toBeVisible()
    await expect(page.getByTestId('bt-entry-0').getByTestId('bt-err-entry-0')).toContainText('sma99')
    await expect(page.locator(':focus')).toHaveAttribute('aria-invalid', 'true')
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/form-422-${dir}-${theme}.png`, fullPage: true })

    // accepted: the page moves to the run and shows it running
    fake.refuse = undefined
    await page.getByTestId('bt-run').click()
    await expect(page).toHaveURL(new RegExp(`/backtester/run\\?id=${RUN_ID}`))
    await expect(page.getByTestId('bt-running')).toBeVisible()
    await expect(page.getByTestId('bt-status')).toHaveAttribute('data-status', 'running')
    expect(fake.posted).toHaveLength(2)
    expect((fake.posted![1] as { inst_ids: string[] }).inst_ids).toEqual(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/running-${dir}-${theme}.png` })
  })
}

test('the form fits a phone width without sideways scroll (Arabic dark)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page)
  await open(page, 'ar', 'dark')
  await expect(page.getByTestId('bt-form')).toBeVisible()
  const overflow = await page.locator('main').evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: `${SHOTS}/form-phone-rtl-dark.png`, fullPage: true })
})
