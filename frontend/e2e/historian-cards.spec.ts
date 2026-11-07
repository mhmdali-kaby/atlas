import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { changedVerdictMessage, noDataVerdictMessage, regimeTableMessage, verdictMessage } from '../src/features/warroom/fixtures/historian'

/**
 * ATL-96: the Historian's research_report (HistoryVerdict) and regime_table cards in the War Room
 * feed, in en/ar × light/dark at 1280 px. A fake Laravel serves the Main thread with the ATL-93
 * fixture messages (src/features/warroom/fixtures/historian.ts).
 */
const SHOTS = '../../evidence/ATL-96'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-30T09:12:03.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
// The API's order: newest first.
const MESSAGES = [verdictMessage(), changedVerdictMessage(), noDataVerdictMessage(), regimeTableMessage()].map((m, i) => ({
  ...m,
  thread_id: MAIN.id,
  created_at: new Date(Date.parse('2026-10-30T09:12:03Z') - i * 60_000).toISOString(),
}))

/**
 * The agent's own words in the owner's language, so the Arabic screenshots carry no English: the
 * Historian writes content_md, question, key_stat and notes; Atlas translates none of them.
 */
const ARABIC_TEXT: Record<string, { content_md: string; card: Record<string, unknown> }> = {
  'msg-verdict-btc': {
    content_md: 'يدعم التاريخ ارتدادًا بشكل ضعيف: وسيط 24 ساعة +1.2%، وأفضل عُشر أعلى من الدخول بـ 4.7%.',
    card: { question: 'BTC بعد هبوط 10% في 3 أيام', key_stat: 'وسيط 24 ساعة +1.2% مقابل +0.2% للأساس؛ +7.7% في أفضل حالة' },
  },
  'msg-verdict-tsla': {
    content_md: 'الحالات أقل من أن نقول إن التاريخ يعاكس البيع.',
    card: {
      question: 'بيع TSLA بعد فجوة صعود عقب الأرباح',
      key_stat: 'ارتفعت 3 من 5 خلال 24 ساعة',
      data_quality_notes: ['بيانات مرجعية قبل 2026-03-01 (ليست أسعار OKX).', 'فجوة واحدة على شموع 4 ساعات في 2026-08-14، أُصلحت.'],
    },
  },
  'msg-verdict-eth': { content_md: 'لا نمط لأيام الأسبوع.', card: { question: 'ETH أيام الاثنين بعد عطلة نهاية أسبوع حمراء', key_stat: 'لا عطلات مطابقة في التاريخ الذي بُحث فيه' } },
  'msg-regime-table': { content_md: 'جدول الأنظمة: العملات الكبرى في اتجاه غالبًا، والمعادن في نطاق.', card: {} },
}

function inLanguage(lang: 'en' | 'ar') {
  if (lang === 'en') return MESSAGES
  return MESSAGES.map((m) => {
    const ar = ARABIC_TEXT[m.id]
    return ar ? { ...m, content_md: ar.content_md, card: { ...m.card, ...ar.card } } : m
  })
}

async function fakeApi(page: Page, lang: 'en' | 'ar' = 'en') {
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
    if (url.pathname === '/api/warroom/messages') return json(200, { data: inLanguage(lang), meta: { next_cursor: null } })
    if (url.pathname === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

const ENGLISH_ONLY = [enWarroom.hv.row.base, enWarroom.hv.row.cases, enWarroom.hv.weak, enWarroom.rt.title, enWarroom.hv.v.weakly_supports, 'Data as of']

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    test(`Historian cards render in ${lang} ${theme}`, async ({ page }) => {
      const wr = lang === 'en' ? enWarroom : arWarroom
      await page.setViewportSize({ width: 1280, height: 900 })
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

      await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
      const verdict = page.locator('[data-msg-id="msg-verdict-btc"]')
      const card = verdict.getByTestId('hv-card')
      await expect(card.getByTestId('hv-question')).toHaveText(lang === 'en' ? 'BTC after a 3-day 10% drop' : 'BTC بعد هبوط 10% في 3 أيام')
      await expect(card.getByTestId('hv-verdict')).toContainText(wr.hv.v.weakly_supports)
      await expect(card.getByTestId('hv-horizon')).toHaveCount(4)
      // The untraced "4.7%" in the body is marked and described.
      const mark = verdict.getByTestId('unv').filter({ hasText: '4.7%' })
      await expect(mark).toHaveAccessibleDescription(wr.hv.unv)

      if (lang === 'ar') {
        // Mirrored: the card's head tile sits at the inline start, which is the right in RTL.
        const box = (await card.boundingBox())!
        const tile = (await card.locator('.hv-tile').boundingBox())!
        expect(tile.x).toBeGreaterThan(box.x + box.width / 2)
        for (const s of ENGLISH_ONLY) await expect(page.getByTestId('wr-feed')).not.toContainText(s)
      }

      await verdict.scrollIntoViewIfNeeded()
      await verdict.screenshot({ path: `${SHOTS}/verdict-${lang}-${theme}.png` })
      const changed = page.locator('[data-msg-id="msg-verdict-tsla"]')
      await changed.scrollIntoViewIfNeeded()
      await changed.screenshot({ path: `${SHOTS}/verdict-changed-${lang}-${theme}.png` })
      const sparse = page.locator('[data-msg-id="msg-verdict-eth"]')
      await sparse.scrollIntoViewIfNeeded()
      await expect(sparse.getByTestId('hv-n')).toHaveText(wr.hv.noCases)
      await sparse.screenshot({ path: `${SHOTS}/verdict-nodata-${lang}-${theme}.png` })

      const table = page.locator('[data-msg-id="msg-regime-table"]')
      await table.scrollIntoViewIfNeeded()
      await expect(table.getByTestId('rt-row')).toHaveCount(10)
      await table.getByTestId('rt-sort-prob').click()
      await expect(table.getByTestId('rt-sort-prob').locator('xpath=..')).toHaveAttribute('aria-sort', 'descending')
      await table.screenshot({ path: `${SHOTS}/regime-table-${lang}-${theme}.png` })
      await page.screenshot({ path: `${SHOTS}/feed-${lang}-${theme}.png` })
    })
  }
}

test('at 390 px the horizon table fades at its inline end while columns are off-screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page)
  await page.goto('/warroom')
  const dist = page.locator('[data-msg-id="msg-verdict-btc"]').getByTestId('hv-dist')
  await dist.scrollIntoViewIfNeeded()
  const overflows = await dist.evaluate((el) => el.scrollWidth > el.clientWidth + 1)
  if (overflows) await expect(dist).toHaveClass(/has-more/)
  await page.locator('[data-msg-id="msg-verdict-btc"]').screenshot({ path: `${SHOTS}/verdict-en-390.png` })
})
