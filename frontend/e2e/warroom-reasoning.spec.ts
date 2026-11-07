import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }

/*
 * ATL-97: "Show reasoning" on a stored agent message, against a fake Laravel serving the ATL-38
 * War Room contract with an ATL-88 final message (reasoning_md, two tool calls, tokens, cost).
 * Captures the open disclosure in en/ar × light/dark at 1280 px, and checks that Tab reaches the
 * toggle with a visible focus ring. Screenshots go to ../../evidence/ATL-97/.
 */

const SHOTS = '../../evidence/ATL-97'
const T = { en: enWarroom, ar: arWarroom }
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const NOW = Date.now()
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

const MAIN = { id: 'main-1', kind: 'main', key: 'main', title: 'Main', last_message_at: iso(60_000), created_at: iso(30 * 86_400_000) }
const QUESTION = {
  id: 'q-1',
  thread_id: 'main-1',
  author_type: 'human',
  author: OWNER.name,
  kind: 'text',
  content_md: '@Historian what usually happens to BTC after a 3-day 10% drop?',
  card: null,
  reply_to: null,
  mentions: ['historian'],
  reasoning_md: null,
  tool_calls: null,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  unmatched_numbers: null,
  created_at: iso(120_000),
}
const ANSWER = {
  ...QUESTION,
  id: 'a-1',
  author_type: 'agent',
  author: 'historian',
  content_md: 'History weakly supports a bounce: 12 cases, 24h median +1.2%.',
  reply_to: 'q-1',
  mentions: [],
  reasoning_md:
    'Regime first: BTC has been ranging for 3 days on the daily bars.\n\nThen analogs on the hourly bars: a 3-day drop of 10% or more, since 2019. Only 12 cases, so the verdict is capped.',
  tool_calls: [
    {
      id: 'toolu_1',
      name: 'mcp__history__detect_regime',
      input: { inst_id: 'BTC-USDT-SWAP', bar: '1D' },
      input_truncated: false,
      as_of: iso(600_000),
      output: JSON.stringify({
        as_of: iso(600_000),
        source: 'history',
        data: { status: 'ok', sample: { n: 41, period: '2019-01-11..2026-10-29' }, result: { label: 'range', probability: 0.71, days_in_regime: 3, method: 'hmm' } },
      }),
      output_truncated: false,
      is_error: null,
    },
    {
      id: 'toolu_2',
      name: 'mcp__history__find_analogs',
      input: { inst_id: 'BTC-USDT-SWAP', bar: '1H', lookback_years: 7 },
      input_truncated: false,
      as_of: null,
      output: 'tool failed: timeout',
      output_truncated: false,
      is_error: true,
    },
  ],
  tokens_in: 2984,
  tokens_out: 412,
  cost_usd: '0.018000',
  created_at: iso(60_000),
}

async function fakeApi(page: Page) {
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
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: [ANSWER, QUESTION], meta: { next_cursor: null } })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: { taken_at: iso(0), items: [] } })
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
  await expect(page.locator('[data-msg-id="a-1"]')).toBeVisible()
}

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    const dir = lang === 'ar' ? 'rtl' : 'ltr'
    test(`the open reasoning disclosure, ${dir}-${theme}`, async ({ page }) => {
      const t = T[lang]
      await open(page, lang, theme)
      await expect(page.locator('html')).toHaveAttribute('dir', dir)
      const answer = page.locator('[data-msg-id="a-1"]')
      const toggle = answer.getByTestId('wr-reason-toggle')
      await expect(toggle).toHaveAttribute('aria-expanded', 'false')
      await expect(toggle).toContainText(t.reason.show)

      await toggle.focus()
      await page.keyboard.press('Enter')
      await expect(toggle).toHaveAttribute('aria-expanded', 'true')
      await expect(toggle).toContainText(t.reason.hide)
      const panel = answer.getByTestId('wr-reason-panel')
      await expect(panel.getByTestId('wr-reason-text')).toContainText('Regime first')
      const tools = panel.getByTestId('wr-tool')
      await expect(tools).toHaveCount(2)
      await expect(tools.nth(0)).toContainText('history.detect_regime')
      await expect(tools.nth(0)).toContainText('BTC-USDT-SWAP, 1D')
      await expect(tools.nth(1)).toContainText(t.reason.res.err)

      // Open the first call: its input and output JSON stay left-to-right in Arabic too.
      await tools.nth(0).getByRole('button').first().click()
      const pre = tools.nth(0).locator('.io-pre').first()
      await expect(pre).toBeVisible()
      await expect(pre).toHaveCSS('direction', 'ltr')

      // The chevron points to the reading direction while closed and down while open.
      const chev = tools.nth(1).locator('.tool-chev')
      const closed = await chev.evaluate((el) => getComputedStyle(el).transform)
      if (dir === 'rtl') expect(closed).toContain('matrix(-1')
      else expect(closed === 'none' || closed.startsWith('matrix(1')).toBe(true)

      expect(await page.locator('main, [role="log"]').first().innerText()).not.toMatch(/[٠-٩۰-۹]/)
      mkdirSync(SHOTS, { recursive: true })
      await answer.scrollIntoViewIfNeeded()
      await page.screenshot({ path: `${SHOTS}/reasoning-${lang}-${theme}.png` })
    })
  }
}

test('Tab reaches the "Show reasoning" toggle with a visible focus ring', async ({ page }) => {
  await open(page, 'en', 'light')
  const toggle = page.locator('[data-msg-id="a-1"]').getByTestId('wr-reason-toggle')
  let reached = false
  for (let i = 0; i < 60 && !reached; i++) {
    await page.keyboard.press('Tab')
    reached = await toggle.evaluate((el) => el === document.activeElement)
  }
  expect(reached, 'Tab reaches the toggle').toBe(true)
  expect(await toggle.evaluate((el) => el.matches(':focus-visible'))).toBe(true)
  // The accent focus ring (3px at 40%), once the 1 ms reduced-motion transition has settled.
  await expect.poll(() => toggle.evaluate((el) => getComputedStyle(el).boxShadow)).toMatch(/0px 0px 0px 3px/)
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/reasoning-focus-en-light.png` })
  await page.keyboard.press('Enter')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
})
