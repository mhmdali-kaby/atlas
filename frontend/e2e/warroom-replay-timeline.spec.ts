import AxeBuilder from '@axe-core/playwright'
import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-207: the Replay day line against a fake Laravel. In Arabic the line runs right to left: 00:00
 * and the playhead at the day's start sit on the right, and a morning marker sits to the right of
 * an evening one. The sidebar lists the last seven days with their counts. English light and
 * Arabic dark pass axe; screenshots go to ../../evidence/ATL-207/.
 */

const SHOTS = '../../evidence/ATL-207'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const DAY_MS = 86_400_000
const HOUR = 3_600_000
const YESTERDAY = new Date(Date.now() + 3 * HOUR - DAY_MS).toISOString().slice(0, 10)
const DAY_START = Date.parse(`${YESTERDAY}T00:00:00Z`) - 3 * HOUR
const MAIN = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: new Date().toISOString(), created_at: new Date(Date.now() - 30 * DAY_MS).toISOString() }

function message(i: number, at: number, over: Record<string, unknown> = {}) {
  return {
    id: `tl-${i}`,
    thread_id: 'main-1',
    author_type: 'agent',
    author: 'historian',
    kind: 'text',
    content_md: `Message ${i}: BTC-USDT-SWAP range holds.`,
    card: null,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    unmatched_numbers: null,
    created_at: new Date(at).toISOString(),
    ...over,
  }
}

function day() {
  const list = Array.from({ length: 60 }, (_, i) => message(i, DAY_START + 7 * HOUR + i * 15 * 60_000))
  list.push(message(100, DAY_START + 8 * HOUR + 1000, { kind: 'watchlist', author: 'market_scout', content_md: 'Watchlist for today: BTC, XAU, TSLA.' }))
  list.push(message(101, DAY_START + 19 * HOUR + 1000, { kind: 'system_alert', author: 'risk_engine', author_type: 'system', content_md: 'Data stale on BTC-USDT-SWAP for 31 s.', card: { severity: 'warning', title: 'Data stale' } }))
  return list.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
}

async function fakeApi(page: Page) {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const cors = { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) => route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: [], meta: { next_cursor: null } })
    if (url.pathname === '/api/warroom/replay/days') {
      const to = url.searchParams.get('to') ?? YESTERDAY
      const start = Date.parse(`${to}T12:00:00Z`)
      const days = Array.from({ length: 7 }, (_, i) => ({ date: new Date(start - i * DAY_MS).toISOString().slice(0, 10), count: i === 0 ? 62 : i * 3 }))
      return json(200, { data: days, meta: { timezone: 'Asia/Baghdad' } })
    }
    if (url.pathname === '/api/warroom/replay') return json(200, { data: url.searchParams.get('date') === YESTERDAY ? day() : [], meta: {} })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date().toISOString(), items: [] } })
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
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1280, height: 900 })
  await fakeApi(page)
  await page.goto(`/warroom/replay/${YESTERDAY}`)
  await expect(page.locator('[data-msg-id="tl-59"]')).toBeVisible()
}

const centreX = async (page: Page, selector: string) => {
  const box = await page.locator(selector).first().boundingBox()
  if (!box) throw new Error(`no box for ${selector}`)
  return box.x + box.width / 2
}

test('in Arabic the day line is mirrored: 00:00, the day start and morning sit on the right', async ({ page }) => {
  await open(page, 'ar', 'light')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  const track = (await page.getByTestId('rp-track').boundingBox())!
  const hours = page.locator('.rp-hr')
  const midnight = (await hours.first().boundingBox())!
  const end = (await hours.last().boundingBox())!
  expect(midnight.x).toBeGreaterThan(end.x)
  expect(midnight.x + midnight.width).toBeGreaterThan(track.x + track.width - 4)

  // Morning watchlist marker to the right of the evening alert.
  const watch = await centreX(page, '[data-testid="rp-marker"][data-kind="watchlist"]')
  const alert = await centreX(page, '[data-testid="rp-marker"][data-kind="system_alert"]')
  expect(watch).toBeGreaterThan(alert)

  // At the day's end the playhead is on the left; Home puts it on the right edge.
  expect(await centreX(page, '[data-testid="rp-head"]')).toBeLessThan(track.x + 4)
  await page.getByTestId('rp-head').focus()
  await page.keyboard.press('Home')
  expect(await centreX(page, '[data-testid="rp-head"]')).toBeGreaterThan(track.x + track.width - 4)
  await expect(page.getByTestId('rp-before')).toBeVisible()

  // A press at 25% from the right edge is 06:00.
  await page.mouse.click(track.x + track.width * 0.75, track.y + track.height / 2)
  await expect(page.getByTestId('rp-time')).toHaveText('06:00:00')
  // ArrowLeft goes forward in time in Arabic: to the first message, at 07:00.
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByTestId('rp-time')).toHaveText('07:00:00')
  await expect(page.locator('[data-testid="rp-feed"] [data-msg-id]')).toHaveCount(1)
})

test('in English the line runs left to right and the sidebar lists the last seven days', async ({ page }) => {
  await open(page, 'en', 'light')
  const watch = await centreX(page, '[data-testid="rp-marker"][data-kind="watchlist"]')
  const alert = await centreX(page, '[data-testid="rp-marker"][data-kind="system_alert"]')
  expect(watch).toBeLessThan(alert)
  const days = page.getByTestId('rp-side-days').locator('a')
  await expect(days).toHaveCount(7)
  await expect(days.first()).toHaveAttribute('aria-current', 'page')
  await expect(days.first()).toContainText('62')
  await expect(page.getByTestId('rp-side-live')).toContainText('Back to live')
  await expect(page.getByTestId('wr-agents')).toHaveCount(0)
})

test('below 1280px the inspector is an overlay opened from the toolbar', async ({ page }) => {
  await open(page, 'en', 'light')
  await page.setViewportSize({ width: 1100, height: 900 })
  await expect(page.getByTestId('rp-inspector')).toHaveCount(0)
  // ATL-224: at 1100 px with the sidebar open the toolbar is compact, so its toggle is in More (⋯).
  await page.getByTestId('toolbar-more').click()
  await page.getByRole('menuitemcheckbox', { name: 'Show or hide the day’s moments' }).click()
  await expect(page.getByTestId('rp-inspector')).toBeVisible()
  await page.getByTestId('rp-moment').first().click()
  await expect(page.getByTestId('rp-inspector')).toHaveCount(0)
  await expect(page.getByTestId('rp-time')).toHaveText('08:00:01')
})

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  test(`${lang} ${theme}: the replay page with its day line passes axe`, async ({ page }) => {
    await open(page, lang, theme)
    // Midway through the day, so past and future markers and moments are both drawn.
    await page.getByTestId('rp-marker').first().click()
    await expect(page.getByTestId('rp-time')).toHaveText('08:00:01')
    mkdirSync(SHOTS, { recursive: true })
    await page.screenshot({ path: `${SHOTS}/replay-line-${lang}-${theme}.png` })
    const results = await new AxeBuilder({ page }).include('[data-testid="rp-page"]').include('[data-testid="sidebar-sections"]').analyze()
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
  })
}
