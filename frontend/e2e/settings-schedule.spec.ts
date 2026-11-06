import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-230: Settings → Schedule against a faked Laravel (ATL-228 schedule.* settings keys). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-230'

const BALANCED = { max_loss_per_trade_pct: '1.0', max_position_pct: '5', max_daily_loss_pct: '4', max_weekly_loss_pct: '10', max_leverage: 5, max_open_positions: 6, max_share_per_instrument_pct: '25', max_correlated_exposure_pct: '35', min_expected_r: '1.8', max_consecutive_losses: 3 }
const PRESETS = { conservative: BALANCED, balanced: BALANCED, aggressive: BALANCED }

const SCHEDULE: Record<string, string | number | boolean> = {
  'schedule.news_morning_brief': '07:30',
  'schedule.historian_regime_table': '07:30',
  'schedule.scout_watchlist': '08:00',
  'schedule.pm_daily_report': '23:30',
  'schedule.historian_weekly_review': 'Sun 18:00',
  'schedule.analysis_interval_minutes': 30,
  'schedule.analysis_first_slot': '08:10',
  'schedule.analysis_last_slot': '23:00',
  'schedule.quiet_hours_enabled': true,
  'schedule.quiet_hours_start': '23:00',
  'schedule.quiet_hours_end': '07:00',
  'schedule.stock_perps_weekend': 'allow',
}

type Entry = { id: string; version: number; diff: Record<string, { from: unknown; to: unknown }>; changed_by: null; created_at: string }

/** A Laravel that keeps what is PATCHed, so a reload shows the saved version. */
async function fakeApi(page: Page, zone = 'Asia/Baghdad') {
  const state = {
    version: 7,
    settings: { risk_profile: 'balanced', ...BALANCED, autonomy_level: 2, onboarding_completed: 1, ...SCHEDULE } as Record<string, unknown>,
    history: [
      { id: 'h7', version: 7, diff: { 'schedule.pm_daily_report': { from: '23:00', to: '23:30' } }, changed_by: null, created_at: '2026-11-04T08:05:00Z' },
      { id: 'h1', version: 1, diff: { risk_profile: { from: null, to: 'balanced' } }, changed_by: null, created_at: '2026-10-28T07:40:00Z' },
    ] as Entry[],
    patches: [] as { version: number; changes: Record<string, unknown> }[],
  }
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
    if (url.pathname === '/api/me/preferences') return json(200, { data: { name: OWNER.name, locale: 'en', timezone: zone, theme: 'system' } })
    if (url.pathname === '/api/settings/history') return json(200, { data: state.history })
    const doc = () => ({ data: { id: '9f6c', env: 'demo', version: state.version, settings: state.settings, presets: PRESETS, max_enabled_autonomy_level: 4, updated_at: '2026-11-06T08:05:00Z' } })
    if (url.pathname === '/api/settings' && method === 'GET') return json(200, doc())
    if (url.pathname === '/api/settings' && method === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}') as { version: number; changes: Record<string, unknown> }
      state.patches.push(body)
      if (body.version !== state.version) return json(409, { message: 'stale', current_version: state.version })
      const diff = Object.fromEntries(Object.entries(body.changes).map(([k, v]) => [k, { from: state.settings[k], to: v }]))
      state.settings = { ...state.settings, ...body.changes }
      state.version += 1
      state.history = [{ id: `h${state.version}`, version: state.version, diff, changed_by: null, created_at: new Date().toISOString() }, ...state.history]
      return json(200, doc())
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
}

const plain = (s: string | null) => (s ?? '').replace(/[⁦-⁩]/g, '')
const every = (page: Page) => page.getByRole('textbox', { name: /^(Every|التكرار)(, changed)?$/ })

async function axe(page: Page) {
  // .st-version is the shared Settings version pill, reported on every Settings page (see settings-money.spec.ts).
  const results = await new AxeBuilder({ page }).include('.sch-page').exclude('.st-version').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('interval 60: the preview reads 08:10, 09:10 … 22:10 before saving; a reload shows 60 and Activity lists it', async ({ page }) => {
  await open(page, 'en', 'light')
  const state = await fakeApi(page)
  await page.goto('/settings/schedule')
  await expect(every(page)).toHaveValue('30')
  await expect.poll(async () => plain(await page.getByTestId('sch-preview').textContent())).toBe('Cycles at 08:10, 08:40 … 22:40')

  await every(page).fill('60')
  await expect.poll(async () => plain(await page.getByTestId('sch-preview').textContent())).toBe('Cycles at 08:10, 09:10 … 22:10')
  expect(state.patches).toEqual([])

  await page.getByTestId('sch-save').click()
  await expect(page.getByTestId('settings-version')).toHaveText('Version 8')
  expect(state.patches).toEqual([{ env: 'demo', version: 7, changes: { 'schedule.analysis_interval_minutes': 60 } }])

  await page.reload()
  await expect(every(page)).toHaveValue('60')
  await expect.poll(async () => plain(await page.getByTestId('sch-preview').textContent())).toBe('Cycles at 08:10, 09:10 … 22:10')
  const latest = page.getByTestId('sch-activity').getByRole('listitem').first()
  await expect(latest).toContainText('Version 8')
  await expect.poll(async () => plain(await latest.textContent())).toContain('Analysis interval: 30 min → 60 min')
})

test('first slot after the last: the server error shows under the slot and nothing is saved', async ({ page }) => {
  await open(page, 'en', 'light')
  const msg = 'The First analysis cycle must be earlier than the Last analysis cycle.'
  await fakeApi(page)
  await page.route('http://localhost:8000/api/settings', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' },
      body: JSON.stringify({ message: msg, errors: { 'changes.schedule.analysis_first_slot': [msg] } }),
    })
  })
  await page.goto('/settings/schedule')
  const first = page.getByRole('textbox', { name: /^First cycle(, changed)?$/ })
  await first.fill('23:30')
  await page.getByTestId('sch-save').click()
  await expect(page.getByTestId('sch-analysis_first_slot-err')).toHaveText(msg)
  await expect(page.getByTestId('settings-version')).toHaveText('Version 7')
})

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    for (const width of [1280, 390]) {
      test(`${lang} ${theme} ${width}px: every schedule key with its saved value`, async ({ page }) => {
        await open(page, lang, theme, width)
        await fakeApi(page, width === 390 ? 'Europe/London' : 'Asia/Baghdad')
        await page.goto('/settings/schedule')
        await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
        await expect(every(page)).toHaveValue('30')
        const values: [string, string][] = [
          ['#sch-news_morning_brief', '07:30'],
          ['#sch-historian_regime_table', '07:30'],
          ['#sch-scout_watchlist', '08:00'],
          ['#sch-pm_daily_report', '23:30'],
          ['#sch-historian_weekly_review', '18:00'],
          ['#sch-analysis_first_slot', '08:10'],
          ['#sch-analysis_last_slot', '23:00'],
          ['#sch-quiet_hours_start', '23:00'],
          ['#sch-quiet_hours_end', '07:00'],
        ]
        for (const [sel, v] of values) await expect(page.locator(sel)).toHaveValue(v)
        await expect(page.locator('#sch-wk-day')).toHaveValue('Sun')
        await expect(page.getByTestId('sch-quiet')).toHaveAttribute('aria-checked', 'true')
        await expect(page.locator('[data-weekend="allow"]')).toHaveAttribute('aria-checked', 'true')
        // Quiet hours 23:00–07:00 cross midnight.
        await expect.poll(async () => plain(await page.getByTestId('sch-quiet_hours_end-sub').textContent())).toBe(
          lang === 'en' ? 'Until 07:00 the next day.' : 'حتى 07:00 من اليوم التالي.',
        )
        // Times use the app's Western digits in both languages.
        expect(plain(await page.getByTestId('sch-preview').textContent())).toMatch(/08:10, 08:40 … 22:40/)
        // In Arabic the times still read left to right: 08:10 sits left of 22:40.
        const xs = await page.getByTestId('sch-preview-times').evaluate((el) => {
          const node = el.firstChild as Text
          return ['08:10', '22:40'].map((n) => {
            const r = document.createRange()
            const i = node.data.indexOf(n)
            r.setStart(node, i)
            r.setEnd(node, i + 5)
            return r.getBoundingClientRect().x
          })
        })
        expect(xs[0]!).toBeLessThan(xs[1]!)
        if (width === 390) await expect(page.getByTestId('sch-news_morning_brief-alt')).toBeVisible()
        await page.waitForTimeout(600)
        expect(await axe(page)).toEqual([])
        await page.screenshot({ path: `${SHOTS}/schedule-${lang}-${theme}-${width}.png`, fullPage: true })
      })
    }
  }
}
