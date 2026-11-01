import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-131: Settings → Markets against a faked Laravel (ATL-124 settings, ATL-119 instruments, ATL-155 eligibility). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-131'

const BALANCED = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }
const UNIVERSE = [
  { inst_id: 'XAU-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAU', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: true },
  { inst_id: 'XAG-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAG', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
  { inst_id: 'SOL-USDT-SWAP', inst_type: 'SWAP', asset_class: 'crypto', base_ccy: 'SOL', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
  { inst_id: 'SOL-USDT', inst_type: 'SPOT', asset_class: 'crypto', base_ccy: 'SOL', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false },
]
const FAILS: Record<string, { fails: string[]; market: string; min: string }> = {
  'XAU-USDT-SWAP': { fails: ['blocked'], market: 'commodity_perp', min: '5000000' },
  'SOL-USDT-SWAP': { fails: ['min_24h_volume'], market: 'crypto_perp', min: '20000000' },
  'XAG-USDT-SWAP': { fails: [], market: 'commodity_perp', min: '5000000' },
}

async function fakeApi(page: Page) {
  const settings: Record<string, unknown> = {
    risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1,
    enabled_markets: ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'],
    whitelist: ['XAG-USDT-SWAP', 'SOL-USDT-SWAP', 'XAU-USDT-SWAP'], blocklist: ['XAU-USDT-SWAP', 'MEME-USDT-SWAP'],
    avoid_stock_perps_outside_us_hours: true, max_instruments_per_day: 5, max_spread_bps: '8',
    min_24h_volume_usd_crypto_perp: '20000000', min_24h_volume_usd_stock_perp: '5000000', min_24h_volume_usd_commodity_perp: '5000000', min_24h_volume_usd_spot: '10000000',
  }
  const history = [
    { id: 'h6', version: 6, diff: { blocklist: { from: [], to: ['XAU-USDT-SWAP', 'MEME-USDT-SWAP'] }, max_spread_bps: { from: '10', to: '8' } }, changed_by: null, created_at: '2026-10-28T08:05:00Z' },
    { id: 'h5', version: 5, diff: { enabled_markets: { from: ['crypto_perp'], to: ['spot', 'crypto_perp', 'stock_perp', 'commodity_perp'] } }, changed_by: null, created_at: '2026-10-28T07:40:00Z' },
  ]
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/settings/history') return json(200, { data: history })
    if (url.pathname === '/api/instruments') {
      const q = (url.searchParams.get('q') ?? '').toUpperCase()
      return json(200, { data: UNIVERSE.filter((i) => i.inst_id.startsWith(q) || i.base_ccy.startsWith(q)) })
    }
    if (url.pathname === '/api/settings/markets/eligibility') {
      // The unsaved blocklist, when sent as a draft (ATL-155), decides "blocked"; it comes first.
      const q = url.searchParams
      const draftBlock = q.has('draft[blocklist]') ? [] : q.has('draft[blocklist][]') ? q.getAll('draft[blocklist][]') : null
      const failsOf = (id: string) => {
        const rest = (FAILS[id]?.fails ?? []).filter((f) => f !== 'blocked')
        const blocked = draftBlock ? draftBlock.includes(id) : (FAILS[id]?.fails ?? []).includes('blocked')
        return blocked ? ['blocked', ...rest] : rest
      }
      const rows = q.getAll('inst_ids[]').map((id) => ({
        inst_id: id, eligible: failsOf(id).length === 0, fails: failsOf(id), market: FAILS[id]?.market ?? null, on_whitelist: true,
        volume_24h_usd: '2400000', min_24h_volume_usd: FAILS[id]?.min ?? null, spread_bps: null, max_spread_bps: '8', notes: ['spread_unknown'],
      }))
      return json(200, { data: { env: 'demo', as_of: new Date().toISOString(), settings_version: 6, draft: [], instruments: rows } })
    }
    if (url.pathname === '/api/settings' && method === 'GET')
      return json(200, { data: { id: '9f6c', env: 'demo', version: 6, settings, presets: PRESETS, max_enabled_autonomy_level: 2, updated_at: '2026-10-28T08:05:00Z' } })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
  await fakeApi(page)
  await page.goto('/settings/markets')
  await expect(page.getByTestId('mk-classes')).toBeVisible()
  // The whitelist labels have arrived.
  await expect(page.getByTestId('mk-warn-SOL-USDT-SWAP')).toBeAttached()
}

async function axe(page: Page, selector: string) {
  // .st-version is the shared Settings version pill (settings-risk/risk.css), reported separately on every Settings page.
  const results = await new AxeBuilder({ page }).include(selector).exclude('.st-version').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('Arabic RTL in dark mode: no axe violations, switches and steppers at the inline end, instIds stay LTR', async ({ page }) => {
  await open(page, 'ar', 'dark')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(page.getByTestId('mk-later-futures')).toHaveText('تتوفر في مرحلة لاحقة')
  await expect(page.getByTestId('mk-allow-note')).toContainText('لا يمكن تداول إلا هذه الأدوات')

  for (const field of ['min_24h_volume_usd_crypto_perp', 'max_spread_bps', 'max_instruments_per_day']) {
    const row = page.locator(`.mk-num[data-field="${field}"]`)
    const [stepper, main, rowBox] = [await row.locator('.st-stepper').boundingBox(), await row.locator('.ml-row-main').boundingBox(), await row.boundingBox()]
    // Inline end is the left in RTL.
    expect(stepper!.x + stepper!.width).toBeLessThanOrEqual(main!.x + 1)
    expect(stepper!.x - rowBox!.x).toBeLessThan(24)
    await expect(row.locator('.st-stepper-field')).toHaveAttribute('dir', 'ltr')
  }
  const cls = page.locator('.mk-class[data-market="spot"]')
  const [sw, label] = [await cls.getByRole('switch').boundingBox(), await cls.locator('.sa-row-label').boundingBox()]
  expect(sw!.x + sw!.width).toBeLessThanOrEqual(label!.x + 1)
  await expect(page.getByTestId('mk-list-allow').locator('bdi', { hasText: 'SOL-USDT-SWAP' })).toHaveAttribute('dir', 'ltr')
  await expect(page.getByTestId('mk-warn-XAU-USDT-SWAP')).toContainText('محظورة أيضًا')

  await page.waitForTimeout(400)
  expect(await axe(page, '.mk-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/markets-ar-dark.png`, fullPage: true })

  // The picker's matches open under the field, also in RTL.
  const input = page.getByRole('combobox', { name: 'الأدوات المحظورة' })
  await input.fill('sol')
  await expect(page.getByRole('option', { name: /SOL-USDT-SWAP/ })).toBeVisible()
  // The matches are a popover portalled to <body>, like every popover: outside the page landmark by design.
  const pop = await new AxeBuilder({ page }).include('.mk-pop').disableRules(['region']).analyze()
  expect(pop.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}`))).toEqual([])
  expect(await axe(page, '.mk-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/markets-ar-dark-picker.png` })
})

test('English light and dark: no axe violations; the picker adds an instrument from the universe', async ({ page }) => {
  await open(page, 'en', 'light')
  expect(await axe(page, '.mk-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/markets-en-light.png`, fullPage: true })

  const input = page.getByRole('combobox', { name: 'Blocked instruments' })
  await input.fill('xag')
  await page.getByRole('option', { name: /XAG-USDT-SWAP/ }).click()
  await expect(page.getByTestId('mk-list-deny')).toContainText('XAG-USDT-SWAP')
  await expect(page.getByTestId('save-bar')).toContainText('1 unsaved change')
  // On both lists now: the allowed chip says the blocklist wins.
  await expect(page.getByTestId('mk-warn-XAG-USDT-SWAP')).toContainText('Also blocked, and the blocklist wins')

  await open(page, 'en', 'dark')
  await page.waitForTimeout(400)
  expect(await axe(page, '.mk-page')).toEqual([])
  await page.screenshot({ path: `${SHOTS}/markets-en-dark.png`, fullPage: true })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.screenshot({ path: `${SHOTS}/markets-en-dark-390.png`, fullPage: true })
})
