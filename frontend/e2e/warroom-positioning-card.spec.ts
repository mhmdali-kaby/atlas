import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { btcCoveringMessage, tslaUnreliableMessage } from '../src/features/warroom/fixtures/positioning'

/**
 * ATL-140: the Derivatives & Sentiment Analyst's PositioningReport card in the War Room feed
 * (design v11 Phase 2 §7). A fake Laravel serves the Main thread with a BTC short-covering report
 * and an unreliable TSLA report.
 */
const SHOTS = '../../evidence/ATL-140'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:06:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const T = { en: enWarroom, ar: arWarroom }

/** The agent's own words in the owner's language, so the Arabic screenshots carry no English prose. */
function messages(lang: 'en' | 'ar') {
  const btc = btcCoveringMessage()
  const tsla = tslaUnreliableMessage()
  if (lang === 'ar') {
    btc.content_md = '**BTC-USDT-SWAP: التمركز يعارض الشراء.** السعر +3.0% والعقود المفتوحة −6.0% خلال 24 ساعة: تغطية مراكز بيع، لا شراء جديد.'
    Object.assign(btc.card!, {
      funding_view: 'التمويل سالب (z −2.1 لسبعة أيام): مراكز البيع تدفع لتبقى.',
      key_evidence: [
        'العقود المفتوحة −6.0% خلال 24 ساعة والسعر +3.0% (as_of 2026-10-28T11:00Z)',
        'التمويل −0.008% كل 8 ساعات، وz لسبعة أيام −2.1 (as_of 2026-10-28T10:58Z)',
      ],
    })
    tsla.content_md = '**بيانات غير موثوقة لـ TSLA-USDT-SWAP:** العقود المفتوحة تقرأ 0. محايد تجاه الشراء والبيع.'
    Object.assign(tsla.card!, {
      funding_view: 'التمويل +0.010% كل 8 ساعات، في حدود متوسط 30 يومًا.',
      unreliable_reasons: { oi_zero: 'العقود المفتوحة تقرأ 0، وهذا غير معقول لعقد دائم مُدرج.' },
      key_evidence: ['العقود المفتوحة 0 في الحساب التجريبي (as_of 2026-10-28T11:00Z)'],
    })
  }
  // The API's order: newest first.
  return [tsla, btc]
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
  await page.setViewportSize({ width, height: 1600 })
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
  const cards = page.getByTestId('pos-card')
  await expect(cards).toHaveCount(2)
  return { btc: cards.and(page.locator('[data-reliable="true"]')), tsla: cards.and(page.locator('[data-reliable="false"]')) }
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="pos-card"]').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('Arabic RTL, dark: both cards render, translated, with no axe violations', async ({ page }) => {
  const { btc, tsla } = await open(page, 'ar', 'dark')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)

  await expect(btc.getByTestId('pos-regime')).toHaveText(T.ar.pos.regime.short_covering)
  await expect(btc.getByTestId('pos-meaning')).toHaveText(T.ar.pos.meaning.short_covering)
  await expect(btc.getByTestId('pos-stance-long')).toContainText(T.ar.pos.st.long.opposes)
  await expect(btc.getByTestId('pos-stance-short')).toContainText(T.ar.pos.st.short.supports)
  await expect(btc.getByTestId('pos-crowding')).toContainText('72%')
  await expect(btc.getByTestId('pos-sq-short')).toHaveText('64%')
  // A figure is an LTR island: the % sits right of its digits, and the sign left of them, in Arabic too.
  const pct = btc.getByTestId('pos-sq-short')
  await expect(pct).toHaveAttribute('dir', 'ltr')
  await expect(btc.getByTestId('pos-basis')).toContainText('+4.3 bps')

  await expect(tsla.getByTestId('pos-unreliable')).toContainText(T.ar.pos.unrelTitle)
  await expect(tsla.getByTestId('pos-squeeze')).toHaveCount(0)
  await expect(tsla.getByTestId('pos-premium')).toContainText('+0.62%')

  // RTL: the stance pills run right to left, long first.
  const lx = (await btc.getByTestId('pos-stance-long').boundingBox())!.x
  const sx = (await btc.getByTestId('pos-stance-short').boundingBox())!.x
  expect(lx).toBeGreaterThan(sx)

  await page.waitForTimeout(300)
  expect(await axeViolations(page)).toEqual([])
  await btc.screenshot({ path: `${SHOTS}/btc-ar-dark.png` })
  await tsla.screenshot({ path: `${SHOTS}/tsla-ar-dark.png` })
})

for (const [lang, theme] of [
  ['en', 'light'],
  ['en', 'dark'],
  ['ar', 'light'],
] as const) {
  test(`${lang} ${theme}: no axe violations`, async ({ page }) => {
    const { btc, tsla } = await open(page, lang, theme)
    await expect(btc.getByTestId('pos-stances')).toBeVisible()
    await page.waitForTimeout(300)
    expect(await axeViolations(page)).toEqual([])
    await btc.screenshot({ path: `${SHOTS}/btc-${lang}-${theme}.png` })
    await tsla.screenshot({ path: `${SHOTS}/tsla-${lang}-${theme}.png` })
  })
}

test('narrow feed: the quadrant stacks above the regime text and nothing scrolls sideways', async ({ page }) => {
  const { btc } = await open(page, 'ar', 'light', 390)
  const quad = (await btc.getByTestId('pos-quad').boundingBox())!
  const name = (await btc.getByTestId('pos-regime').boundingBox())!
  expect(name.y).toBeGreaterThanOrEqual(quad.y + quad.height - 1)
  const overflow = await btc.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await btc.screenshot({ path: `${SHOTS}/btc-ar-390.png` })
})
