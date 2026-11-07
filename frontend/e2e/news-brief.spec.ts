import { expect, test, type Locator, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import { alertAnalysisMessage, instrumentBriefMessage, morningBriefMessage, solAlertMessage } from '../src/features/warroom/fixtures/newsBrief'

/**
 * ATL-116: News Intelligence's news_brief card in the War Room feed. The morning brief in Arabic
 * (RTL) and dark mode, with crypto news down, is screenshotted and checked for aligned tables and
 * unclipped content; the other two forms render beside it. A fake Laravel serves Main with the
 * fixture messages (src/features/warroom/fixtures/newsBrief.ts). The clock is fixed at 08:00
 * Baghdad on 28 Oct so "today", "tomorrow" and past events read as they did that morning.
 */
const SHOTS = '../../evidence/ATL-116'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'ar', theme: 'dark' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T10:21:30.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }

/** The agent's own words in Arabic, as News Intelligence writes them for an Arabic-speaking owner. */
function messages() {
  const morning = morningBriefMessage(
    { content_md: '**موجز الصباح.** ثلاثة أمور تحرّك أدواتنا اليوم: مؤشر أسعار المستهلك الأمريكي الساعة 15:30، ونتائج تسلا الساعة 23:05، ويوم رابع من التدفقات الخارجة من صناديق الذهب.' },
    { degraded_asset_classes: ['crypto'], degraded_since: { crypto: '2026-10-28T03:10:00Z' } },
  )
  const tsla = instrumentBriefMessage(
    { thread_id: MAIN.id, content_md: 'نتائج تسلا الساعة 23:05 هي الحدث؛ وتسعّر عقود الخيارات حركة بنسبة 9%.' },
    { what_to_watch: 'تقرير الساعة 23:05 هو الحاسم. قد يُنفَّذ وقف تحت 438 بسعر سيئ. قد يرغب @PM في توسيعه.' },
  )
  const alert = solAlertMessage()
  const analysis = alertAnalysisMessage(
    { content_md: 'مؤكَّد ومنعكس جزئيًا في السعر: ارتفع SOL-USDT-SWAP بمقدار +1.2 ATR منذ ظهور الخبر الساعة 12:02.' },
    { what_to_watch: '@Scout يستدعي هذا إعادة تشغيل خلال اليوم. @PM لا مراكز لدينا في SOL.' },
  )
  // The API's order: newest first.
  return [tsla, analysis, alert, morning]
}

async function fakeApi(page: Page) {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const origin = route.request().headers()['origin'] ?? '*'
    const json = (status: number, body?: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' },
        body: body === undefined ? '' : JSON.stringify(body),
      })
    if (route.request().method() === 'OPTIONS') {
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
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (url.pathname === '/api/warroom/messages') return json(200, { data: messages(), meta: { next_cursor: null } })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

/** Nothing inside `root` is cut off: no element scrolls sideways, and every box stays inside the card. */
async function expectNotClipped(root: Locator) {
  const problems = await root.evaluate((card) => {
    const box = card.getBoundingClientRect()
    const out: string[] = []
    for (const el of card.querySelectorAll<HTMLElement>('*')) {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0 || el.closest('.sr-only')) continue
      const style = getComputedStyle(el)
      if (style.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) out.push(`scrolls: ${el.className} "${el.textContent?.slice(0, 30)}"`)
      if (r.left < box.left - 1 || r.right > box.right + 1) out.push(`outside: ${el.tagName}.${el.className} "${el.textContent?.slice(0, 30)}"`)
    }
    return out
  })
  expect(problems).toEqual([])
}

/** Every body cell sits under its column header: same inline start and end (± 1px). */
async function expectColumnsAligned(table: Locator) {
  const misaligned = await table.evaluate((t) => {
    const heads = [...t.querySelectorAll('thead th')].map((h) => h.getBoundingClientRect())
    const out: string[] = []
    t.querySelectorAll('tbody tr:not(.nb-day)').forEach((row, ri) => {
      ;[...row.children].forEach((cell, ci) => {
        const r = cell.getBoundingClientRect()
        const h = heads[ci]
        if (!h || Math.abs(r.left - h.left) > 1 || Math.abs(r.right - h.right) > 1) out.push(`row ${ri} col ${ci}`)
      })
    })
    return out
  })
  expect(misaligned).toEqual([])
}

test('the morning brief in Arabic (RTL) and dark mode has aligned tables and no clipped content', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-28T05:00:00Z'))
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'ar')
    localStorage.setItem('atlas.theme', 'dark')
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await fakeApi(page)
  await page.goto('/warroom')

  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)
  const msg = page.locator('[data-msg-id="msg-news-morning"]')
  const card = msg.getByTestId('nb-card')
  await expect(card).toHaveAttribute('aria-label', arWarroom.news_brief.form.morning_brief)
  await expect(card.getByTestId('nb-degraded')).toContainText('لم تصل أخبار العملات المشفرة منذ الساعة 06:10')
  await card.getByTestId('nb-more').click()
  await expect(card.getByTestId('nb-story')).toHaveCount(10)
  await expect(card.getByTestId('nb-day').first()).toContainText('اليوم')

  // Mirrored: the head tile at the inline start, which is the right in RTL.
  const box = (await card.boundingBox())!
  const tile = (await card.locator('.nb-tile').boundingBox())!
  expect(tile.x).toBeGreaterThan(box.x + box.width / 2)

  await expectColumnsAligned(card.getByTestId('nb-cal'))
  await expectColumnsAligned(card.getByTestId('nb-flow'))
  await expectNotClipped(card)

  // A viewport tall enough for the whole card, so the screenshot is the card and not the feed's scroll.
  await page.setViewportSize({ width: 1280, height: 3000 })
  await msg.scrollIntoViewIfNeeded()
  await expectNotClipped(card)
  await card.screenshot({ path: `${SHOTS}/morning-brief-ar-dark.png` })

  for (const id of ['msg-sol-analysis', 'msg-news-tsla']) {
    const other = page.locator(`[data-msg-id="${id}"]`)
    await other.scrollIntoViewIfNeeded()
    await expectNotClipped(other.getByTestId('nb-card'))
    await other.getByTestId('nb-card').screenshot({ path: `${SHOTS}/${id}-ar-dark.png` })
  }
})

test('at 390 px the morning brief stacks its tables instead of scrolling sideways', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-28T05:00:00Z'))
  await page.setViewportSize({ width: 390, height: 4000 })
  await page.addInitScript(() => {
    localStorage.setItem('atlas.lang', 'ar')
    localStorage.setItem('atlas.theme', 'dark')
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  await fakeApi(page)
  await page.goto('/warroom')
  const msg = page.locator('[data-msg-id="msg-news-morning"]')
  await msg.scrollIntoViewIfNeeded()
  await expectNotClipped(msg.getByTestId('nb-card'))
  await msg.getByTestId('nb-card').screenshot({ path: `${SHOTS}/morning-brief-ar-dark-390.png` })
})
