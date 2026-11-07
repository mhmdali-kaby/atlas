import AxeBuilder from '@axe-core/playwright'
import { mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import type { Page, Route } from '@playwright/test'
import { summarise, type SourcesReport } from '../../src/features/settings-data-sources/model'
import { iso, mixed, source } from '../../src/features/settings-data-sources/testFixtures'
import { EVIDENCE_DIR, FRONTEND_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-68: Settings → Data sources in the four appearance projects (en/ar × light/dark). The owner is
 * signed in against the real Laravel; GET and PATCH /api/data-sources are routed in the browser with
 * ATL-66's shapes, so every status, the outage notice and the toggle's 500 are deterministic.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-68')

type Strings = Record<string, unknown>
const locale = (lang: string): Strings =>
  JSON.parse(readFileSync(path.join(FRONTEND_DIR, 'src/i18n/locales', lang, 'settings-data-sources.json'), 'utf8')) as Strings
const str = (s: Strings, key: string): string => key.split('.').reduce<unknown>((o, k) => (o as Strings)[k], s) as string
const plain = (s: string) => s.replace(/<\/?[a-z]+>/g, '').replace(/\{\{[a-z]+\}\}/g, '')

test.use({ viewport: { width: 1280, height: 900 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function serve(page: Page, list: SourcesReport, patch: (route: Route) => Promise<void> = (r) => r.fulfill({ json: { data: {} } })) {
  let current = list
  await page.route('**/api/data-sources', (route) => route.fulfill({ json: current }))
  await page.route('**/api/data-sources/*', patch)
  return { set: (next: SourcesReport) => (current = next) }
}

async function open(page: Page) {
  await page.goto('/settings/sources')
  await expect(page.getByTestId('src-summary')).toBeVisible()
}

const row = (page: Page, key: string) => page.locator(`[data-testid="src-row"][data-key="${key}"]`)

/** Every row's parts sit inside its card, and no text box is wider than its own box (ellipsis aside). */
async function boundsCheck(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement.scrollWidth - document.documentElement.clientWidth
    const bad: string[] = []
    for (const r of document.querySelectorAll<HTMLElement>('[data-testid="src-row"]')) {
      const card = r.closest('.src-group')!.getBoundingClientRect()
      for (const el of r.querySelectorAll<HTMLElement>('.src-mono, .src-name, .src-status, .src-yield, [role="switch"]')) {
        const b = el.getBoundingClientRect()
        if (b.width === 0) continue
        if (b.left < card.left - 0.5 || b.right > card.right + 0.5) bad.push(`${r.dataset.key} ${el.className}`)
      }
      // The status word is never cut: it is the signal.
      const st = r.querySelector<HTMLElement>('.src-status')!
      if (st.scrollWidth > st.clientWidth + 1) bad.push(`${r.dataset.key} status clipped`)
    }
    for (const el of document.querySelectorAll<HTMLElement>('.src-sum-title, .src-notice-title, .src-group-title, .src-cats')) {
      if (el.scrollWidth > el.clientWidth + 1) bad.push(`${el.className} overflows`)
    }
    return { doc, bad }
  })
  expect(overflow.doc, 'page scrolls sideways (px)').toBeLessThanOrEqual(1)
  expect(overflow.bad).toEqual([])
}

test('lists the sources grouped by kind with the right status for ok, degraded, needs_key and disabled rows', async ({ page, lang }, info) => {
  const s = locale(lang)
  await serve(page, mixed(Date.now()))
  await open(page)

  await expect(page.getByTestId('src-group')).toHaveCount(5)
  const cats = await page.getByTestId('src-group').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.cat))
  expect(cats).toEqual(['exchange', 'crypto_news', 'markets_news', 'calendar', 'sentiment'])
  await expect(page.locator('#src-g-crypto_news .src-group-title')).toHaveText(str(s, 'cat.crypto_news'))
  await expect(page.locator('#src-g-crypto_news [data-testid="src-row"]')).toHaveCount(3)

  // ok → Working; degraded → Retrying at <time>; needs_key → Needs a key; disabled → Off.
  await expect(row(page, 'rss.coindesk')).toHaveAttribute('data-status', 'working')
  await expect(row(page, 'rss.coindesk').getByTestId('src-status')).toHaveText(str(s, 'st.working'))
  await expect(row(page, 'rss.theblock')).toHaveAttribute('data-status', 'retrying')
  await expect(row(page, 'rss.theblock').getByTestId('src-status')).toContainText(plain(str(s, 'st.retrying')).trim())
  await expect(row(page, 'rss.theblock').getByTestId('src-status')).toContainText(/\d\d:\d\d/)
  await expect(row(page, 'rss.theblock').getByTestId('src-error')).toContainText('HTTP 503 Service Unavailable')
  await expect(row(page, 'cryptopanic')).toHaveAttribute('data-status', 'needs_key')
  await expect(row(page, 'cryptopanic').getByTestId('src-status')).toHaveText(str(s, 'st.needs_key'))
  await expect(row(page, 'cryptopanic').getByRole('switch')).toBeDisabled()
  await expect(row(page, 'rss.cnbc')).toHaveAttribute('data-status', 'off')
  await expect(row(page, 'rss.cnbc').getByTestId('src-status')).toHaveText(str(s, 'st.off'))
  await expect(row(page, 'rss.cnbc').getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  // A missing key is a visible notice, not an error (brief §7.2).
  await expect(page.getByTestId('src-sum-keys')).toBeVisible()

  await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
  const errDir = await row(page, 'rss.theblock').locator('.src-error-text').evaluate((el) => getComputedStyle(el).direction)
  expect(errDir).toBe('ltr')

  await page.waitForTimeout(700) // the wire's bars finish rising
  await boundsCheck(page)
  const axe = await new AxeBuilder({ page }).include('[data-testid="sources-page"]').analyze()
  const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(serious.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}`))).toEqual([])

  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-mixed.png`, fullPage: true })

  // Phone width: the yield column wraps under the text and nothing spills out of its card.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  await boundsCheck(page)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-390.png`, fullPage: true })
})

test('toggling a source sends PATCH {enabled:false} and shows Off; a 500 reverts the switch with an error toast', async ({ page, lang }, info) => {
  const s = locale(lang)
  const now = Date.now()
  const list = mixed(now)
  const patches: { url: string; body: unknown }[] = []
  let answer = 200
  const api = await serve(page, list, async (route) => {
    patches.push({ url: new URL(route.request().url()).pathname, body: route.request().postDataJSON() })
    if (answer === 200) {
      const data = list.data.map((x) => (x.key === 'rss.coindesk' ? { ...x, enabled: false, status: 'disabled' as const } : x))
      api.set({ data, summary: summarise(data) })
      await route.fulfill({ json: { data: data.find((x) => x.key === 'rss.coindesk') } })
    } else {
      await route.fulfill({ status: 500, json: { message: 'Server Error' } })
    }
  })
  await open(page)

  const coindesk = row(page, 'rss.coindesk')
  const name = 'CoinDesk'
  await coindesk.getByRole('switch', { name: str(s, 'sw.label').replace('{{name}}', name) }).click()
  await expect.poll(() => patches).toEqual([{ url: '/api/data-sources/rss.coindesk', body: { enabled: false } }])
  await expect(coindesk).toHaveAttribute('data-status', 'off')
  await expect(coindesk.getByTestId('src-status')).toHaveText(str(s, 'st.off'))
  await expect(coindesk.getByRole('switch')).toHaveAttribute('aria-checked', 'false')

  // The 500: the switch flips at once, then rolls back, and the error toast names the source.
  answer = 500
  const yahoo = row(page, 'rss.yahoo')
  await expect(yahoo.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  await yahoo.getByRole('switch').click()
  await expect.poll(() => patches.length).toBe(2)
  expect(patches[1]).toEqual({ url: '/api/data-sources/rss.yahoo', body: { enabled: false } })
  await expect(yahoo.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  await expect(yahoo).toHaveAttribute('data-status', 'failing')
  const toastEl = page.locator('[data-toast="error"]')
  await expect(toastEl).toContainText(str(s, 'sw.failOff').replace('{{name}}', 'Yahoo Finance'))
  await expect(toastEl).toContainText(str(s, 'sw.failBody'))
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-toggle-500.png` })
})

test('when the summary says every crypto news source is down, a warning banner names that class', async ({ page, lang }, info) => {
  const s = locale(lang)
  const now = Date.now()
  const data = [
    source('okx.announcements', 'exchange', now, { provider: 'okx', name: 'OKX announcements' }),
    source('rss.coindesk', 'crypto_news', now, {
      name: 'CoinDesk',
      status: 'degraded',
      consecutive_failures: 3,
      disabled_until: iso(now + 9 * 60_000),
      last_error: 'HTTP 403 Forbidden from www.coindesk.com',
    }),
    source('rss.decrypt', 'crypto_news', now, { name: 'Decrypt', enabled: false, status: 'disabled' }),
    source('cryptopanic', 'crypto_news', now, {
      provider: 'cryptopanic',
      name: 'CryptoPanic',
      requires_key: true,
      status: 'needs_key',
      last_success_at: null,
      hourly_24h: Array(24).fill(0),
    }),
    source('rss.cnbc', 'markets_news', now, { name: 'CNBC Top News' }),
  ]
  await serve(page, { data, summary: { ...summarise(data), crypto_news: { total: 3, available: 0, all_down: true } } })
  await open(page)

  const banner = page.getByTestId('src-down')
  await expect(banner).toHaveCount(1)
  await expect(banner).toHaveAttribute('role', 'alert')
  await expect(banner).toHaveAttribute('data-cat', 'crypto_news')
  await expect(banner).toContainText(str(s, 'down.crypto_news'))
  await expect(banner).toContainText(str(s, 'down.until.crypto_news'))

  await banner.getByRole('button', { name: str(s, 'down.show') }).click()
  await expect(page.locator('#src-gt-crypto_news')).toBeFocused()

  await page.waitForTimeout(700)
  await boundsCheck(page)
  const axe = await new AxeBuilder({ page }).include('[data-testid="sources-page"]').analyze()
  const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(serious.flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}`))).toEqual([])
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-crypto-down.png`, fullPage: true })
})

test('loading, error and empty states', async ({ page, lang }, info) => {
  const s = locale(lang)
  let mode: 'error' | 'empty' = 'error'
  await page.route('**/api/data-sources', (route) =>
    mode === 'error' ? route.fulfill({ status: 500, json: { message: 'Server Error' } }) : route.fulfill({ json: { data: [], summary: {} } }),
  )
  await page.goto('/settings/sources')
  const error = page.getByTestId('src-error')
  await expect(error).toBeVisible({ timeout: 15_000 })
  await expect(error.getByRole('heading')).toHaveText(str(s, 'loadError.title'))
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-error.png` })
  mode = 'empty'
  await error.getByRole('button', { name: str(s, 'retry') }).click()
  const empty = page.getByTestId('src-empty')
  await expect(empty).toBeVisible()
  await expect(empty.getByRole('heading')).toHaveText(str(s, 'empty.title'))
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-empty.png` })
})
