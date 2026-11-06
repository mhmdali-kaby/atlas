import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-233: the Data sources (after Connect) and Market history (after Autonomy) onboarding steps,
 * walked with fixtures at 1280 and 390 px, in English and Arabic, light and dark.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-233'
const FINNHUB_KEY = 'fh-e2e-fixture-key-9XyZ'

const PRESETS = {
  conservative: { max_loss_per_trade_pct: '0.5', max_position_pct: '2', max_daily_loss_pct: '2', max_weekly_loss_pct: '5', max_leverage: 2, max_open_positions: 3, max_share_per_instrument_pct: '15', max_correlated_exposure_pct: '20', min_expected_r: '2.0', max_consecutive_losses: 2 },
  balanced: { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 },
  aggressive: { max_loss_per_trade_pct: '2.0', max_position_pct: '10', max_daily_loss_pct: '8', max_weekly_loss_pct: '20', max_leverage: 10, max_open_positions: 10, max_share_per_instrument_pct: '40', max_correlated_exposure_pct: '50', min_expected_r: '1.5', max_consecutive_losses: 4 },
} as const

const CONNECTION = {
  id: 'a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30', exchange: 'okx', env: 'demo', label: 'OKX demo', api_key_last4: 'ab12', has_secret: true,
  has_passphrase: true, permissions: ['read_only', 'trade'], account_mode: 'futures', position_mode: 'net_mode', uid_last4: '6123',
  last_verified_at: '2026-10-28T08:00:00Z', last_test_error: null, created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
}

type Source = Record<string, unknown> & { key: string; provider: string; category: string; enabled: boolean; requires_key: boolean; has_key: boolean; key_last4: string | null }

function sources(): Source[] {
  const now = Date.now()
  const iso = (ms: number) => new Date(ms).toISOString()
  const row = (key: string, provider: string, name: string, category: string, keyed = false): Source => ({
    key, provider, name, kind: key.startsWith('rss.') ? 'rss' : 'api', category, url: null, enabled: true, requires_key: keyed, has_key: false,
    key_last4: null, poll_seconds: 300, status: keyed ? 'needs_key' : 'ok', consecutive_failures: 0, disabled_until: null,
    last_success_at: keyed ? null : iso(now - 60_000), last_item_at: null, last_error: null, hourly_24h: Array(24).fill(0), covered_by: null,
  })
  return [
    row('okx.announcements', 'okx', 'OKX announcements', 'exchange'),
    row('rss.coindesk', 'coindesk', 'CoinDesk', 'crypto_news'),
    row('rss.cointelegraph', 'cointelegraph', 'Cointelegraph', 'crypto_news'),
    row('cryptopanic', 'cryptopanic', 'CryptoPanic', 'crypto_news', true),
    row('rss.cnbc', 'cnbc', 'CNBC', 'markets_news'),
    row('finnhub.news', 'finnhub', 'Finnhub news', 'markets_news', true),
    row('finnhub.calendar', 'finnhub', 'Finnhub economic calendar', 'calendar', true),
    row('forexfactory.calendar', 'forexfactory', 'ForexFactory calendar', 'calendar'),
    row('finnhub.earnings', 'finnhub', 'Finnhub earnings', 'earnings', true),
    row('alternative_me.fng', 'alternative_me', 'Crypto Fear & Greed Index', 'sentiment'),
  ]
}

const CLASSES = [
  ['crypto', 'BTC ETH SOL XRP DOGE ADA AVAX LINK DOT TRX TON SUI APT ARB OP NEAR LTC BCH ETC FIL ATOM INJ SEI TIA'],
  ['stock', 'TSLA NVDA AAPL AMZN GOOGL MSFT META AMD PLTR TSM ORCL LLY COST'],
  ['etf', 'SPY QQQ IWM'],
  ['index', 'US100'],
  ['commodity', 'XAU XAG CL NG XCU'],
] as const

/** GET /api/data/backfill: `done` of the instruments finished, two fetching, the rest waiting. */
function backfill(done: number) {
  const at = new Date().toISOString()
  const insts = CLASSES.flatMap(([cls, list]) => list.split(' ').map((b) => ({ id: `${b}-USDT-SWAP`, cls })))
  const instruments = insts.map((x, i) => {
    const status = i < done ? 'done' : done > 0 && i < done + 2 ? 'running' : 'pending'
    const rows = status === 'done' ? 1000 : status === 'running' ? 400 : 0
    return {
      inst_id: x.id, asset_class: x.cls, open_issues: 0,
      datasets: [{ dataset: 'candles:1H', status, depth_days: 730, target_from: null, cursor_ts: null, rows_written: rows, expected: 1000, percent: ((rows / 1000) * 100).toFixed(1), last_error: null, updated_at: at }],
    }
  })
  const by = (s: string) => instruments.filter((i) => i.datasets[0]!.status === s).length
  return {
    data: {
      env: 'demo', as_of: at, instruments,
      totals: { instruments: insts.length, jobs: insts.length, by_status: { pending: by('pending'), running: by('running'), done: by('done'), failed: 0, paused: 0 }, rows_written: 0, percent: null },
      quality: { open: 0, open_by_kind: { gap: 0, duplicate: 0, zero_volume: 0, outlier: 0 } },
      quality_issues: [],
    },
  }
}

async function fakeApi(page: Page) {
  const state = {
    version: 1,
    settings: { risk_profile: 'balanced', ...PRESETS.balanced, autonomy_level: 2, onboarding_completed: 0 } as Record<string, string | number>,
    sources: sources(),
    done: 0,
    backfillCalls: 0,
    keyPuts: [] as { path: string; body: unknown }[],
  }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    const doc = () => ({ data: { id: '9f6c', env: 'demo', version: state.version, settings: state.settings, presets: PRESETS, max_enabled_autonomy_level: 2, updated_at: '2026-10-28T08:00:00Z' } })
    if (method === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [CONNECTION] })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as { version: number; changes: Record<string, string | number> }
      if (body.version !== state.version) return json(409, { message: 'stale', current_version: state.version })
      state.settings = { ...state.settings, ...body.changes }
      state.version += 1
      return json(200, doc())
    }
    if (url.pathname === '/api/data-sources' && method === 'GET') return json(200, { data: state.sources, summary: {} })
    const key = /^\/api\/data-sources\/([^/]+)\/key$/.exec(url.pathname)
    if (key && method === 'PUT') {
      const body = JSON.parse(req.postData() ?? '{}') as { api_key: string }
      state.keyPuts.push({ path: url.pathname, body })
      const provider = state.sources.find((s) => s.key === decodeURIComponent(key[1]!))!.provider
      state.sources = state.sources.map((s) => (s.requires_key && s.provider === provider ? { ...s, has_key: true, key_last4: body.api_key.slice(-4), status: 'ok' } : s))
      return json(200, { data: state.sources.find((s) => s.key === decodeURIComponent(key[1]!)) })
    }
    if (url.pathname === '/api/data/backfill') {
      state.backfillCalls += 1
      return json(200, backfill(state.done))
    }
    if (url.pathname === '/api/data/storage') return json(200, { data: { hypertables: [], total_bytes: 0 } })
    return json(404, { message: 'Not found' })
  })
  return state
}

async function prefs(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(([l, th]) => {
    localStorage.setItem('atlas.lang', l)
    localStorage.setItem('atlas.theme', th)
  }, [lang, theme] as const)
}

const T = {
  en: { sources: 'Choose where news comes from', risk: 'Choose a risk profile', autonomy: 'Choose how much Atlas does on its own', history: 'Atlas is collecting market history', skip: 'Skip', key: 'Finnhub key', step3: 'Step 3 of 6', step6: 'Step 6 of 6', finish: 'Finish setup' },
  ar: { sources: 'اختر من أين تأتي الأخبار', risk: 'اختر مستوى المخاطرة', autonomy: 'اختر مقدار ما يفعله أطلس بنفسه', history: 'يجمع أطلس سجل السوق', skip: 'تخطٍّ', key: 'مفتاح Finnhub', step3: 'الخطوة 3 من 6', step6: 'الخطوة 6 من 6', finish: 'إنهاء الإعداد' },
}

const noOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

for (const width of [1280, 390] as const) {
  for (const [lang, theme] of [['en', 'light'], ['ar', 'dark'], ['en', 'dark'], ['ar', 'light']] as const) {
    test(`Data sources and Market history at ${width} px in ${lang} ${theme}`, async ({ page }) => {
      const t = T[lang]
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 })
      await prefs(page, lang, theme)
      const api = await fakeApi(page)

      // Connect is done (a verified key), so the owner continues to Data sources.
      await page.goto('/onboarding?step=connect')
      await expect(page.getByTestId('saved-key')).toBeVisible()
      await page.getByTestId('ob-next').click()
      await expect(page).toHaveURL(/step=sources$/)
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(t.sources)
      await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
      await expect(page.getByTestId('ob-step-of')).toHaveText(t.step3)
      if (width === 390) await expect(page.locator('.ob-steps-sm')).toBeVisible()
      else await expect(page.locator('.ob-steps-ol')).toBeVisible()
      await expect(page.getByTestId('sources-summary')).toBeVisible()
      await expect(page.locator('section[data-step="sources"]')).toHaveCSS('opacity', '1')
      expect(await noOverflow(page)).toBe(true)
      await page.screenshot({ path: `${SHOTS}/sources-${width}-${lang}-${theme}.png`, fullPage: true })

      if (lang === 'en' && theme === 'light') {
        // Enter a Finnhub key: Continue saves it; Back shows only its last 4.
        await page.getByLabel(t.key, { exact: true }).fill(FINNHUB_KEY)
        await page.getByTestId('ob-next').click()
        await expect(page).toHaveURL(/step=risk$/)
        expect(api.keyPuts).toHaveLength(1)
        expect(api.keyPuts[0]!.body).toEqual({ api_key: FINNHUB_KEY })
        await page.getByRole('button', { name: 'Back' }).click()
        await expect(page.getByTestId('ob-key-mask')).toHaveText('•••• 9XyZ')
        await expect(page.getByText(FINNHUB_KEY)).toHaveCount(0)
        await page.screenshot({ path: `${SHOTS}/sources-saved-${width}.png`, fullPage: true })
        await page.getByTestId('ob-skip').click()
      } else {
        // Skip moves on with nothing saved.
        await page.getByRole('button', { name: t.skip, exact: true }).click()
        expect(api.keyPuts).toHaveLength(0)
      }

      await expect(page).toHaveURL(/step=risk$/)
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(t.risk)
      await page.getByTestId('ob-next').click()
      await expect(page).toHaveURL(/step=autonomy$/)
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(t.autonomy)
      await page.getByTestId('ob-next').click()

      // Market history: 0% to start, and Continue (Finish) is enabled anyway.
      await expect(page).toHaveURL(/step=backfill$/)
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(t.history)
      await expect(page.getByTestId('ob-step-of')).toHaveText(t.step6)
      const bar = page.getByTestId('backfill-bar')
      await expect(bar).toHaveAttribute('aria-valuenow', '0')
      await expect(page.getByTestId('ob-next')).toBeEnabled()
      await expect(page.getByTestId('ob-next')).toHaveText(t.finish)
      // It updates while open: the fake backfill moves on, the next 5 s poll shows it.
      api.done = 14
      await expect(page.getByTestId('backfill-insts')).toContainText('14', { timeout: 8_000 })
      await expect(bar).not.toHaveAttribute('aria-valuenow', '0')
      expect(api.backfillCalls).toBeGreaterThanOrEqual(2)
      await expect(page.locator('section[data-step="backfill"]')).toHaveCSS('opacity', '1')
      expect(await noOverflow(page)).toBe(true)
      await page.screenshot({ path: `${SHOTS}/backfill-${width}-${lang}-${theme}.png`, fullPage: true })

      await page.getByTestId('ob-next').click()
      await expect(page).toHaveURL(/\/portfolio$/)
      expect(api.settings.onboarding_completed).toBe(1)
      // Onboarded: the app opens at the War Room, not at the new steps.
      await page.goto('/')
      await expect(page).toHaveURL(/\/warroom/)
    })
  }
}
