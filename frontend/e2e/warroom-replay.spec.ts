import AxeBuilder from '@axe-core/playwright'
import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }

/*
 * ATL-206: War Room Replay against a fake Laravel serving GET /api/warroom/replay?date= with a
 * 10,000-message day (brief §19). The feed is virtualised: scrolled to the end, it keeps fewer
 * than 200 message nodes in the DOM. English light and Arabic dark pass axe; screenshots go to
 * ../../evidence/ATL-206/.
 */

const SHOTS = '../../evidence/ATL-206'
const T = { en: enWarroom, ar: arWarroom }
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const DAY_MS = 86_400_000
// Yesterday in Baghdad (UTC+3), the day the toolbar's Replay opens.
const YESTERDAY = new Date(Date.now() + 3 * 3_600_000 - DAY_MS).toISOString().slice(0, 10)
const DAY_START = Date.parse(`${YESTERDAY}T00:00:00Z`) - 3 * 3_600_000
const COUNT = 10_000
const AGENTS = ['historian', 'news_intelligence', 'market_scout', 'technical_analyst', 'strategist']
const MAIN = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: new Date().toISOString(), created_at: new Date(Date.now() - 30 * DAY_MS).toISOString() }

function dayOfMessages() {
  const step = Math.floor(DAY_MS / COUNT)
  return Array.from({ length: COUNT }, (_, i) => {
    const human = i % 25 === 0
    const alert = !human && i % 997 === 0
    return {
      id: `rp-${i}`,
      thread_id: 'main-1',
      author_type: human ? 'human' : alert ? 'system' : 'agent',
      author: human ? OWNER.name : alert ? 'risk_engine' : AGENTS[i % AGENTS.length],
      kind: alert ? 'system_alert' : 'text',
      content_md: alert ? `Data stale on BTC-USDT-SWAP for 31 s (message ${i}).` : `Message ${i} of the day: BTC-USDT-SWAP range holds between 67,100 and 68,400.`,
      card: alert ? { severity: 'warning', title: 'Data stale' } : null,
      reply_to: i > 0 && i % 40 === 1 ? `rp-${i - 1}` : null,
      mentions: [],
      reasoning_md: i % 9 === 0 && !human && !alert ? 'Regime first: range for 3 days on the daily bars.' : null,
      tool_calls: null,
      tokens_in: null,
      tokens_out: null,
      cost_usd: null,
      unmatched_numbers: null,
      created_at: new Date(DAY_START + i * step).toISOString(),
    }
  })
}

async function fakeApi(page: Page) {
  const day = JSON.stringify({ data: dayOfMessages(), meta: { date: YESTERDAY, timezone: 'Asia/Baghdad', from: '', to: '' } })
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const cors = { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body) })
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: [], meta: { next_cursor: null } })
    if (url.pathname === '/api/warroom/replay') return json(200, url.searchParams.get('date') === YESTERDAY ? day : { data: [], meta: {} })
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
  await page.goto('/warroom')
  await page.getByTestId('wr-replay-open').click()
  await expect(page).toHaveURL(new RegExp(`/warroom/replay/${YESTERDAY}$`))
  // The whole day arrives in one response (about 5 MB here), so allow it time to load.
  await expect(page.locator('[data-msg-id="rp-9999"]')).toBeVisible({ timeout: 20_000 })
}

const nodes = (page: Page) => page.locator('[data-testid="rp-feed"] [data-msg-id]').count()

test('a 10,000-message day opens at its end with fewer than 200 message nodes, and scrolls through both ways', async ({ page }) => {
  await open(page, 'en', 'light')
  const feed = page.getByTestId('rp-feed')
  expect(await nodes(page)).toBeLessThan(200)
  await expect(page.locator('.composer')).toHaveCount(0)

  // To the top, then all the way back to the end.
  await feed.evaluate((el) => (el.scrollTop = 0))
  await expect(page.locator('[data-msg-id="rp-0"]')).toBeVisible()
  expect(await nodes(page)).toBeLessThan(200)
  for (let i = 0; i < 6; i++) {
    await feed.evaluate((el) => (el.scrollTop = el.scrollHeight))
    await page.waitForTimeout(100)
  }
  await expect(page.locator('[data-msg-id="rp-9999"]')).toBeVisible()
  expect(await nodes(page)).toBeLessThan(200)
  await expect(page.locator('[data-msg-id="rp-0"]')).toHaveCount(0)

  // Keyboard: Home and End move through the whole day, still virtualised.
  await page.locator('[data-msg-id="rp-9999"]').focus()
  await page.keyboard.press('Home')
  await expect(page.locator('[data-msg-id="rp-0"]')).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.locator('[data-msg-id="rp-9999"]')).toBeFocused()
  expect(await nodes(page)).toBeLessThan(200)
})

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  test(`${lang} ${theme}: the replay page passes axe`, async ({ page }) => {
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    await expect(page.getByTestId('rp-banner')).toContainText(T[lang].rp.live)
    await expect(page.getByTestId('rp-inspector')).toContainText('10,000')
    // The date picker open too, so its days are checked.
    mkdirSync(SHOTS, { recursive: true })
    await page.screenshot({ path: `${SHOTS}/replay-${lang}-${theme}.png` })
    const results = await new AxeBuilder({ page }).include('[data-testid="rp-page"]').analyze()
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])

    await page.getByTestId('rp-date').click()
    await expect(page.getByTestId('rp-calendar')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/replay-picker-${lang}-${theme}.png` })
    const picker = await new AxeBuilder({ page }).include('[data-testid="rp-calendar"]').analyze()
    expect(picker.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
  })
}
