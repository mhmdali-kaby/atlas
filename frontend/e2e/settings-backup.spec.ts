import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/* ATL-232: Settings → Backup against a faked Laravel (ATL-231 GET /api/backup, POST /api/backup/run). */

const SHOTS = '../../evidence/ATL-232'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const FOLDER = '/Volumes/Backup/Atlas'
const H = 3_600_000

type Run = { id: string; file: string; path: string | null; bytes: number | null; status: 'ok' | 'failed'; error: string | null; started_at: string; finished_at: string }

function fileAt(at: number) {
  const d = new Date(at).toISOString()
  return `atlas-${d.slice(0, 10).replace(/-/g, '')}-${d.slice(11, 19).replace(/:/g, '')}.dump`
}
function ok(id: string, at: number, bytes: number): Run {
  const file = fileAt(at)
  return { id, file, path: `${FOLDER}/${file}`, bytes, status: 'ok', error: null, started_at: new Date(at).toISOString(), finished_at: new Date(at + 47_000).toISOString() }
}
function docOf(runs: Run[]) {
  const good = runs.find((r) => r.status === 'ok') ?? null
  const age = good ? Math.round((Date.now() - Date.parse(good.finished_at)) / 1000) : 0
  return {
    env: 'demo',
    settings: { enabled: true, folder: FOLDER, time: '03:00', timezone: 'Asia/Baghdad', keep: 14 },
    runs,
    last_good: good ? { ...good, age_seconds: age } : null,
    stale_after_hours: 26,
    stale: !good || age > 26 * 3600,
  }
}
/** Nightly runs for the last `days` days, the newest `newestH` hours ago. */
function nightly(newestH: number, days = 9): Run[] {
  const t0 = Date.now() - newestH * H
  return Array.from({ length: days }, (_, i) => ok(`b${i}`, t0 - i * 24 * H, (684 - i * 9) * 1e6))
}

type Prefs = { locale: 'en' | 'ar'; theme: 'light' | 'dark' }

async function fakeApi(page: Page, runs: Run[], prefs: Prefs) {
  const state = { runs: [...runs], posts: 0, queued: null as Run | null, released: false }
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
    if (url.pathname === '/api/me/preferences') return json(200, { data: { name: OWNER.name, timezone: 'Asia/Baghdad', ...prefs } })
    if (url.pathname === '/api/settings' && method === 'GET')
      return json(200, { data: { id: 's1', env: 'demo', version: 23, settings: { onboarding_completed: 1 }, presets: {}, updated_at: '2026-10-28T00:00:00Z' } })
    if (url.pathname === '/api/backup') {
      if (state.queued && state.released) {
        state.runs = [state.queued, ...state.runs].slice(0, 10)
        state.queued = null
      }
      return json(200, { data: docOf(state.runs) })
    }
    if (url.pathname === '/api/backup/run' && method === 'POST') {
      state.posts += 1
      state.queued = ok('bnew', Date.now(), 690e6)
      return json(202, { data: { queued: true, message: 'A backup will start in a moment.' } })
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

async function open(page: Page, runs: Run[], prefs: Prefs = { locale: 'en', theme: 'light' }, width = 1280) {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(
    ([lang, theme]) => {
      localStorage.setItem('atlas.lang', lang as string)
      localStorage.setItem('atlas.theme', theme as string)
    },
    [prefs.locale, prefs.theme],
  )
  const api = await fakeApi(page, runs, prefs)
  await page.goto('/settings/backup')
  await expect(page.getByTestId('bk-status')).toBeVisible()
  return api
}

for (const width of [1280, 390]) {
  for (const locale of ['en', 'ar'] as const) {
    for (const theme of ['light', 'dark'] as const) {
      const tag = `${width}-${locale}-${theme}`
      test(`a good backup 3 h old shows its time, size and file (${tag})`, async ({ page }) => {
        const runs = nightly(3)
        await open(page, runs, { locale, theme }, width)
        const html = page.locator('html')
        await expect(html).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
        if (theme === 'dark') await expect(html).toHaveClass(/(^|\s)dark(\s|$)/)
        const card = page.getByTestId('bk-status')
        await expect(card).toHaveAttribute('data-status', 'ok')
        await expect(card.getByTestId('bk-size')).toHaveText('684 MB')
        await expect(card.getByTestId('bk-file')).toHaveText(runs[0]!.file)
        await expect(card.getByTestId('bk-when')).toHaveText(/\d{2}:\d{2}/)
        await expect(card.getByTestId('bk-overdue')).toHaveCount(0)
        const box = await page.getByTestId('bk-page').boundingBox()
        const docWidth = await page.evaluate(() => document.documentElement.scrollWidth)
        expect(docWidth).toBeLessThanOrEqual(width)
        expect(box).not.toBeNull()
        await page.screenshot({ path: `${SHOTS}/ok-${tag}.png`, fullPage: true })
      })

      test(`30 h old and none show the orange "no recent backup" state (${tag})`, async ({ page }) => {
        await open(page, nightly(30), { locale, theme }, width)
        const card = page.getByTestId('bk-status')
        await expect(card).toHaveAttribute('data-status', 'overdue')
        const notice = card.getByTestId('bk-overdue')
        await expect(notice).toHaveAttribute('data-tone', 'warning')
        await expect(notice).toContainText(locale === 'ar' ? 'لا توجد نسخة حديثة' : 'No recent backup')
        await page.screenshot({ path: `${SHOTS}/overdue-${tag}.png`, fullPage: true })

        await page.unrouteAll({ behavior: 'ignoreErrors' })
        await fakeApi(page, [], { locale, theme })
        await page.reload()
        await expect(page.getByTestId('bk-status')).toHaveAttribute('data-status', 'none')
        await expect(page.getByTestId('bk-status').getByRole('heading')).toContainText(locale === 'ar' ? 'لا توجد نسخة حديثة' : 'No recent backup')
        await page.screenshot({ path: `${SHOTS}/none-${tag}.png`, fullPage: true })
      })
    }
  }
}

test('Back up now disables itself while the run is in progress, then the new run tops the list', async ({ page }) => {
  const api = await open(page, nightly(3))
  const button = page.getByTestId('bk-now')
  await expect(button).toBeEnabled()
  await button.click()
  await expect(button).toBeDisabled()
  await expect(page.getByTestId('bk-status')).toHaveAttribute('data-status', 'running')
  await expect(page.getByTestId('bk-run').first()).toHaveAttribute('data-status', 'running')
  await page.screenshot({ path: `${SHOTS}/running-1280-en-light.png`, fullPage: true })
  expect(api.posts).toBe(1)

  api.released = true
  await expect(button).toBeEnabled({ timeout: 8000 })
  const first = page.getByTestId('bk-run').first()
  await expect(first).toHaveAttribute('data-status', 'ok')
  await expect(first).toContainText('690 MB')
  await expect(page.getByTestId('bk-status').getByTestId('bk-size')).toHaveText('690 MB')
  expect(api.posts).toBe(1)
})

test('has no axe violations', async ({ page }) => {
  await open(page, nightly(3))
  // Let the page's entry fade finish; .st-version is the shared Settings pill, checked with Settings.
  await page.waitForTimeout(400)
  const results = await new AxeBuilder({ page }).include('[data-testid="bk-page"]').exclude('.st-version').analyze()
  expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
})
