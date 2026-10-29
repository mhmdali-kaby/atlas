import { execFile, spawn, type ChildProcess } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { connect } from 'node:net'
import path from 'node:path'
import { promisify } from 'node:util'
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { BACKEND_DIR, BACKEND_ENV, EVIDENCE_DIR, REVERB, SERVICES_DIR, withEnv } from '../support/stack'

/**
 * ATL-40 on the real stack: Laravel (playwright.config webServer), plus Reverb and
 * `php artisan atlas:bridge`, which this file starts itself so it can stop and restart Reverb.
 * Python publishes through `python -m atlas_shared.bus --demo-system-message` on the e2e Redis DB.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-40')
const NEWS_SHOTS = path.join(EVIDENCE_DIR, 'ATL-70')
const run = promisify(execFile)

test.describe.configure({ mode: 'serial' })
test.use({ viewport: { width: 1440, height: 900 } })

let reverb: ChildProcess | null = null
let bridge: ChildProcess | null = null

function start(args: string[]): ChildProcess {
  const child = spawn('php', ['artisan', ...args], { cwd: BACKEND_DIR, env: withEnv(BACKEND_ENV), stdio: 'ignore' })
  child.unref()
  return child
}

function portOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host: REVERB.host, port })
    socket.once('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.once('error', () => resolve(false))
  })
}

async function waitForPort(port: number, open: boolean) {
  await expect.poll(() => portOpen(port), { timeout: 20_000, intervals: [100] }).toBe(open)
}

async function startReverb() {
  reverb = start(['reverb:start', `--host=${REVERB.host}`, `--port=${REVERB.port}`])
  await waitForPort(REVERB.port, true)
}

async function stopReverb() {
  reverb?.kill('SIGTERM')
  reverb = null
  await waitForPort(REVERB.port, false)
}

/** Publishes one warroom.message to the demo Main thread from Python, as an agent would. */
async function publishFromPython(text: string) {
  await run('uv', ['run', '--package', 'atlas-shared', 'python', '-m', 'atlas_shared.bus', '--demo-system-message', '--text', text], {
    cwd: SERVICES_DIR,
    env: withEnv({ REDIS_HOST: BACKEND_ENV.REDIS_HOST!, REDIS_PORT: BACKEND_ENV.REDIS_PORT!, REDIS_DB: BACKEND_ENV.REDIS_DB! }),
  })
}

/**
 * ATL-70: publishes ATL-65's shared news.alert fixture (services/ingest/tests/fixtures/alerts/
 * news_alert_envelope.json) to atlas.warroom from Python, with a fresh event id and cluster so the
 * bridge stores it as a new news_alert message, as ingest would.
 */
const NEWS_FIXTURE = 'ingest/tests/fixtures/alerts/news_alert_envelope.json'
const PUBLISH_NEWS = `
import json, sys, uuid
from atlas_shared.bus import publish
from atlas_shared.events import EventEnvelope
env = json.load(open(sys.argv[1]))
cluster = str(uuid.uuid4())
env.update(id=str(uuid.uuid4()), ts=None, correlation_id="news:" + cluster)
env = {k: v for k, v in env.items() if v is not None}
env["payload"].update(json.loads(sys.argv[2]), cluster_id=cluster, news_item_id=str(uuid.uuid4()))
publish("atlas.warroom", EventEnvelope.model_validate(env))
`
async function publishNewsAlert(payload: Record<string, unknown>) {
  await run('uv', ['run', '--package', 'atlas-shared', 'python', '-c', PUBLISH_NEWS, NEWS_FIXTURE, JSON.stringify(payload)], {
    cwd: SERVICES_DIR,
    env: withEnv({ REDIS_HOST: BACKEND_ENV.REDIS_HOST!, REDIS_PORT: BACKEND_ENV.REDIS_PORT!, REDIS_DB: BACKEND_ENV.REDIS_DB! }),
  })
}

const feedOf = (page: Page) => page.getByTestId('wr-feed')
const input = (page: Page) => page.getByTestId('wr-composer-input')

async function openMain(page: Page) {
  await page.goto('/warroom')
  await expect(feedOf(page).locator('[data-msg-id]').first()).toBeVisible()
  // Echo has connected: no banner after the first-connection grace.
  await expect(page.getByTestId('wr-banner-down')).toHaveCount(0)
}

test.beforeAll(async () => {
  mkdirSync(SHOTS, { recursive: true })
  mkdirSync(NEWS_SHOTS, { recursive: true })
  await startReverb()
  bridge = start(['atlas:bridge', '--consumer=e2e-atl-40', '--block=200'])
})

test.afterAll(async () => {
  bridge?.kill('SIGTERM')
  if (reverb) await stopReverb()
})

test('the owner sends "hello team": it appears once, at the inline end, and survives a reload', async ({ page }) => {
  await openMain(page)
  await input(page).fill('hello team')
  await input(page).press('Enter')

  const mine = feedOf(page).locator('[data-shape="owner"]', { hasText: /^hello team/ })
  await expect(mine).toHaveCount(1)
  await expect(mine.getByTestId('wr-sending')).toHaveCount(0)
  await expect(mine).not.toHaveAttribute('data-local', /./)

  // Right-aligned in English: the bubble ends at the column's end, past its middle.
  const bubble = await mine.locator('.bubble').boundingBox()
  const column = await feedOf(page).locator('.wr-column').boundingBox()
  expect(bubble && column).toBeTruthy()
  expect(bubble!.x).toBeGreaterThan(column!.x + column!.width / 2)
  expect(bubble!.x + bubble!.width).toBeGreaterThan(column!.x + column!.width - 48)
  await page.screenshot({ path: path.join(SHOTS, 'sent-hello-team.png') })

  await page.reload()
  await expect(feedOf(page).locator('[data-shape="owner"]', { hasText: /^hello team/ })).toHaveCount(1)
})

test('a warroom.message published from Python appears in the open feed within 2 s', async ({ page }) => {
  await openMain(page)
  const text = `Live from Python ${Date.now()}`
  await publishFromPython(text)
  await expect(feedOf(page).getByText(text)).toBeVisible({ timeout: 2_000 })
  await expect(page.getByTestId('wr-live')).toContainText(text)
  await expect(feedOf(page).getByText(text)).toHaveCount(1)
})

test('stopping Reverb shows the banner; restarting clears it and brings in what was published meanwhile', async ({ page }) => {
  test.setTimeout(120_000)
  await openMain(page)

  await stopReverb()
  const down = page.getByTestId('wr-banner-down')
  await expect(down).toBeVisible({ timeout: 15_000 })
  await expect(down).toContainText(enWarroom.live.down)
  await page.screenshot({ path: path.join(SHOTS, 'banner-down.png') })

  // Stored by the bridge, but its broadcast fails: only the refetch on reconnect can show it.
  const text = `Published while Reverb was down ${Date.now()}`
  await publishFromPython(text)
  await expect(feedOf(page).getByText(text)).toHaveCount(0)

  await startReverb()
  await expect(down).toHaveCount(0, { timeout: 60_000 })
  await expect(feedOf(page).getByText(text)).toBeVisible()
  await expect(feedOf(page).getByText(text)).toHaveCount(1)
})

test('Arabic, dark: a failed send shows "Not sent" with Try again at the inline end, and retrying posts once', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'ar')
    localStorage.setItem('atlas.theme', 'dark')
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await openMain(page)
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(input(page)).toHaveAttribute('placeholder', arWarroom.composer.placeholder)

  let posts = 0
  let fail = true
  await page.route('**/api/warroom/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    posts += 1
    if (fail) return route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"Server Error"}' })
    return route.fallback()
  })

  const text = `مرحبا يا فريق ${Date.now()}`
  await input(page).fill(text)
  await input(page).press('Enter')
  const mine = feedOf(page).locator('[data-shape="owner"]', { hasText: text })
  const failed = mine.getByTestId('wr-failed')
  await expect(failed).toBeVisible()
  await expect(failed).toContainText(arWarroom.send.notSent)
  // The owner's bubble sits at the inline end, which is the left in Arabic.
  const bubble = await mine.locator('.bubble').boundingBox()
  const column = await feedOf(page).locator('.wr-column').boundingBox()
  expect(bubble!.x + bubble!.width).toBeLessThan(column!.x + column!.width / 2)
  await page.screenshot({ path: path.join(SHOTS, 'ar-dark-failed.png') })

  fail = false
  await failed.getByRole('button', { name: arWarroom.tryAgain }).click()
  await expect(mine).toHaveCount(1)
  await expect(mine.getByTestId('wr-failed')).toHaveCount(0)
  await expect(mine).not.toHaveAttribute('data-local', /./)
  expect(posts).toBe(2)
  await page.screenshot({ path: path.join(SHOTS, 'ar-dark-sent.png') })
})

test('typing "@His" and choosing Historian inserts "@Historian " and the sent message mentions historian', async ({ page }) => {
  await openMain(page)
  await input(page).pressSequentially('@His')
  const picker = page.getByTestId('wr-picker')
  await expect(picker.getByRole('option')).toHaveCount(1)
  await expect(picker.getByRole('option', { name: /Historian/ })).toBeVisible()
  await expect(picker.getByRole('option', { name: /Historian/ })).toHaveAttribute('aria-selected', 'true')
  await page.waitForTimeout(400) // the 250ms pop-in, for the screenshot
  await page.screenshot({ path: path.join(SHOTS, 'picker.png') })
  await picker.getByRole('option', { name: /Historian/ }).click()
  await expect(input(page)).toHaveValue('@Historian ')
  await expect(input(page)).toBeFocused()

  await input(page).pressSequentially('what happens after CPI?')
  const posted = page.waitForResponse((r) => r.url().endsWith('/api/warroom/messages') && r.request().method() === 'POST')
  await input(page).press('Enter')
  const res = await posted
  expect(res.status()).toBe(201)
  const body = (await res.json()) as { data: { mentions: string[]; content_md: string } }
  expect(body.data.content_md).toBe('@Historian what happens after CPI?')
  expect(body.data.mentions).toEqual(['historian'])
})

/* ---- ATL-70: the news_alert card -------------------------------------------------------- */

const NEWS_HEADLINE = `Tesla reports <script>alert(1)</script> **bold** third-quarter earnings ${Date.now()}`

test('a news.alert published to atlas.warroom appears live as the news alert card, untrusted text literal', async ({ page }) => {
  await openMain(page)
  await publishNewsAlert({ headline: NEWS_HEADLINE, sentiment: -0.31 })

  const card = feedOf(page).getByTestId('news-card').filter({ hasText: NEWS_HEADLINE })
  await expect(card).toBeVisible({ timeout: 5_000 })
  await expect(card).toHaveCount(1)
  await expect(card.getByTestId('news-headline')).toHaveText(NEWS_HEADLINE)
  await expect(card.locator('script, strong')).toHaveCount(0)
  await expect(card.getByText(enWarroom.kind.news_alert)).toBeVisible()
  await expect(card.getByRole('img', { name: 'Importance 4 of 5' })).toBeVisible()
  await expect(card.getByTestId('news-type')).toHaveText(enWarroom.news.type.earnings)
  await expect(card.getByRole('listitem')).toHaveText(['TSLA-USDT'])
  // published_at 12:00:00Z = 15:00 Baghdad.
  await expect(card.getByTestId('news-by')).toHaveText('Published by CNBC Top News at 15:00')
  await expect(card.getByTestId('news-sentiment')).toContainText('−0.31')
  const link = card.getByTestId('news-link')
  await expect(link).toHaveAttribute('href', 'https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  await expect(page.getByTestId('wr-live')).toContainText(`${enWarroom.kind.news_alert}.`)
  await card.scrollIntoViewIfNeeded()
  await card.screenshot({ path: path.join(NEWS_SHOTS, 'live-card-en-light.png') })
})

for (const [lang, theme] of [
  ['en', 'light'],
  ['en', 'dark'],
  ['ar', 'light'],
  ['ar', 'dark'],
] as const) {
  test(`the news alert card in ${lang}/${theme}: designed strings, no axe violations, nothing clipped`, async ({ page }) => {
    await page.addInitScript(
      ([l, th]) => {
        localStorage.setItem('atlas.lang', l)
        localStorage.setItem('atlas.theme', th)
      },
      [lang, theme] as const,
    )
    await page.emulateMedia({ colorScheme: theme })
    await openMain(page)
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    const wr = lang === 'ar' ? arWarroom : enWarroom
    const card = feedOf(page).getByTestId('news-card').filter({ hasText: NEWS_HEADLINE })
    await card.scrollIntoViewIfNeeded()
    await expect(card).toBeVisible()
    await expect(card.getByText(wr.kind.news_alert)).toBeVisible()
    await expect(card.getByTestId('news-type')).toHaveText(wr.news.type.earnings)
    await expect(card.getByTestId('news-link')).toContainText('cnbc.com')

    const axe = await new AxeBuilder({ page }).include('[data-testid="news-card"]').analyze()
    expect(axe.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}`))).toEqual([])

    // Nothing inside the card overflows its box, and the card sits inside the feed column.
    const clipped = await card.evaluate((el) => {
      const box = el.getBoundingClientRect()
      return [el, ...Array.from(el.querySelectorAll<HTMLElement>('*'))]
        .filter((n) => !n.closest('.sr-only'))
        .filter((n) => {
          const r = n.getBoundingClientRect()
          const over = n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflowX !== 'visible'
          return over || r.left < box.left - 1 || r.right > box.right + 1
        })
        .map((n) => n.className || n.tagName)
    })
    expect(clipped).toEqual([])
    const column = await feedOf(page).locator('.wr-column').boundingBox()
    const box = await card.boundingBox()
    expect(box!.x).toBeGreaterThanOrEqual(column!.x - 1)
    expect(box!.x + box!.width).toBeLessThanOrEqual(column!.x + column!.width + 1)
    if (lang === 'ar') {
      // RTL: the card starts at the column's right, and sentiment sits at the inline end (left).
      const senti = await card.getByTestId('news-sentiment').boundingBox()
      const chip = await card.getByRole('listitem').first().boundingBox()
      expect(senti!.x).toBeLessThan(chip!.x)
    }
    await card.screenshot({ path: path.join(NEWS_SHOTS, `card-${lang}-${theme}.png`) })
  })
}

test('an importance-5 alert with no linked instruments draws no chips and no NaN', async ({ page }) => {
  await openMain(page)
  const headline = `Fed holds rates, no linked instruments ${Date.now()}`
  await publishNewsAlert({ headline, importance: 5, inst_ids: [], sentiment: 0.0, event_type: 'central_bank', reason: 'importance', held_inst_ids: [] })
  const card = feedOf(page).getByTestId('news-card').filter({ hasText: headline })
  await expect(card).toBeVisible({ timeout: 5_000 })
  await expect(card).toHaveClass(/imp-5/)
  await expect(card.locator('.news-inst')).toHaveCount(0)
  await expect(card).not.toContainText('NaN')
  await card.screenshot({ path: path.join(NEWS_SHOTS, 'importance-5-no-instruments.png') })
})
