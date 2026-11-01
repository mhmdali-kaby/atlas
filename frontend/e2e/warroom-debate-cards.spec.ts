import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arDebate from '../src/i18n/locales/ar/debate.json' with { type: 'json' }
import { daRerateMessage, daReviewMessage, riskCheckMessage, voteSummaryMessage } from '../src/features/warroom/fixtures/debate'

/**
 * ATL-165: the debate cards in the War Room feed (design v11 Phase 2 §6): the Devil's Advocate
 * review and its re-rate, the vote summary (one voter missing) and the risk check (rejected at
 * check 6, and resized). A fake Laravel serves the Main thread with them.
 */
const SHOTS = '../../evidence/ATL-165'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:24:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }

function messages(lang: 'en' | 'ar') {
  const da = daReviewMessage()
  const rerate = daRerateMessage()
  const vote = voteSummaryMessage(true)
  const rejected = riskCheckMessage('rejected')
  const resized = riskCheckMessage('resized')
  if (lang === 'ar') {
    // The agents' own words in Arabic, so the Arabic screens carry no English prose.
    da.content_md = '**4 من 5: مشكلتان جديتان.** مركز BTC الطويل المفتوح يجعل هذه الصفقة رهانًا ثانيًا على حركة الكريبتو.'
    Object.assign(da.card!, {
      strongest_objection: 'مركز BTC الطويل ما زال مفتوحًا، وارتباط SOL به خلال 30 يومًا يبلغ 0.82، فوق 0.8.',
      fix: 'خفّضوا الحجم إلى 3% من رأس المال.',
    })
    rerate.content_md = 'أعدتُ تقييم النسخة 2: **2 من 5.**'
    vote.content_md = '**نجح التصويت: 0.76 مقابل عتبة 0.60.**'
    rejected.content_md = '**مرفوض عند الفحص 6.**'
    resized.content_md = '**عُدِّل الحجم.**'
  }
  // The API's order: newest first.
  return [resized, rejected, vote, rerate, da]
}

async function fakeApi(page: Page, lang: 'en' | 'ar') {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const origin = req.headers()['origin'] ?? '*'
    const json = (status: number, body?: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' },
        body: body === undefined ? '' : JSON.stringify(body),
      })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': origin,
          'Access-Control-Allow-Credentials': 'true',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE',
        },
      })
    }
    const p = url.pathname
    if (p === '/sanctum/csrf-cookie') return json(204)
    if (p === '/api/auth/status') return json(200, { owner_exists: true })
    if (p === '/api/auth/me') return json(200, { data: OWNER })
    if (p === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (p === '/api/warroom/messages') {
      const data = url.searchParams.get('thread_id') === MAIN.id ? messages(lang) : []
      return json(200, { data, meta: { next_cursor: null } })
    }
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', width = 1280) {
  await page.setViewportSize({ width, height: 1400 })
  await page.clock.install({ time: new Date('2026-10-28T11:32:00Z') })
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ colorScheme: theme })
  await fakeApi(page, lang)
  await page.goto('/warroom')
  const reviews = page.getByTestId('db-review')
  await expect(reviews).toHaveCount(2)
  await expect(page.getByTestId('db-votes')).toBeVisible()
  await expect(page.getByTestId('db-risk')).toHaveCount(2)
  return {
    review: page.locator('[data-testid="db-review"][data-round="1"]'),
    rerate: page.locator('[data-testid="db-review"][data-round="2"]'),
    vote: page.getByTestId('db-votes'),
    rejected: page.locator('[data-testid="db-risk"][data-result="rejected"]'),
    resized: page.locator('[data-testid="db-risk"][data-result="resized"]'),
  }
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="db-review"]').include('[data-testid="db-votes"]').include('[data-testid="db-risk"]').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('Arabic RTL, dark: all three cards render, translated, with no axe violations', async ({ page }) => {
  const c = await open(page, 'ar', 'dark')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)

  await expect(c.review.getByTestId('db-objection')).toContainText(arDebate.strongest)
  await expect(c.review.getByTestId('db-ci')).toHaveCount(10)
  await expect(c.review.getByTestId('db-rating')).toContainText('4 من 5')
  await expect(c.rerate.getByTestId('db-was')).toContainText('كان 4 من 5')

  const news = c.vote.locator('[data-agent="news_intelligence"]')
  await expect(news.getByTestId('db-voter-vote')).toHaveText(arDebate.v.missing)
  await expect(c.vote.getByTestId('db-vt-math')).toHaveText('2.69 ÷ 3.55 = 0.76')
  await expect(c.vote.getByTestId('db-vt-needs')).toContainText('0.60')

  await expect(c.rejected.getByTestId('db-rk-stopped')).toContainText('توقّف عند الفحص 6')
  const states = await c.rejected.getByTestId('db-rk-step').evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.state))
  expect(states).toEqual(['pass', 'pass', 'pass', 'pass', 'pass', 'fail', 'not_run', 'not_run', 'not_run'])
  await expect(c.rejected.getByTestId('db-rk-step').nth(6)).toContainText(arDebate.rk.st.not_run)

  // In RTL the checklist's icon column sits at the inline start, the right edge.
  const row = c.review.getByTestId('db-ci').first()
  const ic = (await row.locator('.db-ci-ic').boundingBox())!
  const name = (await row.locator('.db-ci-name').boundingBox())!
  expect(ic.x).toBeGreaterThan(name.x)

  await page.waitForTimeout(300)
  expect(await axeViolations(page)).toEqual([])
  for (const [k, loc] of Object.entries(c)) await loc.screenshot({ path: `${SHOTS}/${k}-ar-dark.png` })
})

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'light']] as const) {
  test(`${lang} ${theme}: no axe violations`, async ({ page }) => {
    const c = await open(page, lang, theme)
    await expect(c.vote).toBeVisible()
    await page.waitForTimeout(300)
    expect(await axeViolations(page)).toEqual([])
    for (const [k, loc] of Object.entries(c)) await loc.screenshot({ path: `${SHOTS}/${k}-${lang}-${theme}.png` })
  })
}

test('phone width: the vote table folds and nothing scrolls sideways', async ({ page }) => {
  const c = await open(page, 'ar', 'light', 390)
  await expect(c.vote).toBeVisible()
  for (const loc of [c.review, c.vote, c.rejected]) {
    const over = await loc.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(over).toBeLessThanOrEqual(1)
  }
  await c.vote.screenshot({ path: `${SHOTS}/vote-ar-390.png` })
  await c.review.screenshot({ path: `${SHOTS}/review-ar-390.png` })
})
