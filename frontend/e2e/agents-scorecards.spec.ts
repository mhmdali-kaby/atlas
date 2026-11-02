import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import type { ScoreDays } from '../src/features/agents/scorecards/api'
import { scoresFixture } from '../src/features/agents/scorecards/fixtures'

/*
 * ATL-201: Agents → Scorecards against a fake Laravel serving ATL-200's GET /api/agents/scores
 * (docs/handoff/ATL-200-journal-scores-api.md). English light and Arabic dark: no axe violations,
 * the page is right to left in Arabic and the trend lines mirror (newest day at the inline end).
 * Screenshots go to ../../evidence/ATL-201/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-201'

async function fakeApi(page: Page) {
  const days: number[] = []
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/agents/scores') {
      const d = Number(url.searchParams.get('days') ?? 30) as ScoreDays
      days.push(d)
      return json(200, scoresFixture(d))
    }
    if (url.pathname === '/api/agents') return json(200, { data: [], meta: { env: 'demo', day: '2026-10-28', timezone: 'Asia/Baghdad', day_start: '', day_end: '', settings_version: 1 } })
    if (url.pathname === '/broadcasting/auth') return json(403, { message: 'Forbidden' })
    return json(404, { message: 'Not found' })
  })
  return days
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/agents/scorecards')
  await expect(page.getByTestId('sc-card')).toHaveCount(9)
}

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'
  test(`renders ${lang}-${theme}: nine cards, no axe violations, ${dir} with trends running toward the inline end`, async ({ page }) => {
    const days = await fakeApi(page)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    expect(days.every((d) => d === 30)).toBe(true)

    // Trend lines mirror: the newest day's dot sits at the inline end of its chart.
    const trends = page.locator('.sc-trend:not(.is-none)')
    const n = await trends.count()
    expect(n).toBeGreaterThanOrEqual(3)
    for (let i = 0; i < n; i += 1) {
      const svg = await trends.nth(i).locator('svg').boundingBox()
      const dot = await trends.nth(i).getByTestId('sc-trend-dot').boundingBox()
      expect(svg && dot).toBeTruthy()
      const dotX = dot!.x + dot!.width / 2
      if (dir === 'rtl') expect(dotX - svg!.x).toBeLessThan(6)
      else expect(svg!.x + svg!.width - dotX).toBeLessThan(6)
    }
    // The calibration line is a number line: 0% stays on the left in both languages.
    const line = page.locator('.sc-cal-line').first()
    const ends = line.locator('.sc-cal-end')
    const zero = await ends.nth(0).boundingBox()
    const hundred = await ends.nth(1).boundingBox()
    expect(zero!.x).toBeLessThan(hundred!.x)

    // The headline cards use the page's own direction: the name sits at the inline start of its card.
    const head = page.locator('.sc-card').first()
    const box = await head.boundingBox()
    const name = await head.locator('.sc-name').boundingBox()
    if (dir === 'rtl') expect(box!.x + box!.width - (name!.x + name!.width)).toBeLessThan(box!.width / 2)
    else expect(name!.x - box!.x).toBeLessThan(box!.width / 2)

    const axe = await new AxeBuilder({ page }).include('[data-testid="sc-page"]').analyze()
    expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((x) => x.target.join(' ')).join(', ')}`)).toEqual([])

    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/scorecards-${dir}-${theme}.png`, fullPage: true })
  })
}

test('the period control refetches with days=7 and 90, and the sidebar links to Status and Scorecards', async ({ page }) => {
  const days = await fakeApi(page)
  await open(page, 'en', 'light')
  await page.getByRole('radio', { name: '7 days' }).click()
  await expect.poll(() => days).toContain(7)
  await expect(page.getByTestId('sc-nd-selection_edge')).toContainText('n = 18, needs 20')
  await page.getByRole('radio', { name: '90 days' }).click()
  await expect.poll(() => days).toContain(90)
  await expect(page.getByTestId('sc-shorter')).toBeVisible()
  // Keyboard: arrows move along the period control.
  await page.getByRole('radio', { name: '90 days' }).focus()
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByRole('radio', { name: '30 days' })).toBeFocused()
  await page.keyboard.press('Space')
  await expect(page.getByRole('radio', { name: '30 days' })).toBeChecked()

  await expect(page.getByRole('link', { name: 'Scorecards' })).toHaveAttribute('aria-current', 'page')
  await page.getByRole('link', { name: 'Status' }).click()
  await expect(page).toHaveURL(/\/agents$/)
})
