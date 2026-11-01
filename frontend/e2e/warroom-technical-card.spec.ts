import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { solSetupMessage, xauNoSetupMessage } from '../src/features/warroom/fixtures/technicalSetup'

/**
 * ATL-139: the Technical Analyst's TechnicalSetup card in the War Room feed (design v11 Phase 2
 * §7). A fake Laravel serves the Main thread with a SOL long breakout and an XAU "No setup", and an
 * instrument thread for SOL, so clicking the instrument opens it in the Inspector.
 */
const SHOTS = '../../evidence/ATL-139'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:04:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const SOL_THREAD = { id: 'thread-sol', kind: 'instrument', key: 'SOL-USDT-SWAP', title: 'SOL-USDT-SWAP', last_message_at: null, created_at: '2026-10-28T09:10:00.000000Z' }
const T = { en: enWarroom, ar: arWarroom }

/** The agent's own words in the owner's language, so the Arabic screenshots carry no English prose. */
function messages(lang: 'en' | 'ar') {
  const sol = solSetupMessage()
  const xau = xauNoSetupMessage()
  if (lang === 'ar') {
    sol.content_md = '**SOL-USDT-SWAP: إعداد اختراق للشراء.** يُفعَّل بإغلاق ساعة فوق 250.20 مع حجم مرتفع. العائد المتوقع 1.8R والجودة 0.72.'
    Object.assign(sol.card!, {
      trigger: 'إغلاق ساعة فوق 250.20 مع حجم نسبي أعلى من 1.3',
      invalidation: 'إغلاق ساعة دون 244.00، مستوى الاختراق',
      est_slippage_bps: '14',
      targets: [
        { price: '258.50', r_multiple: '1.20', reason: 'قمة الأسبوع الماضي', close_pct: '50' },
        { price: '264.60', r_multiple: '2.00', reason: 'منطقة مقاومة على إطار 4 ساعات', close_pct: '30' },
        { price: '272.20', r_multiple: '3.00', reason: 'هدف الحركة المقيسة', close_pct: '20' },
      ],
    })
    xau.content_md = '**XAU-USDT-SWAP: لا إعداد.** السعر في منتصف النطاق بلا زخم في أي اتجاه.'
    Object.assign(xau.card!, { no_setup_reason: 'إغلاق ساعة دون 4,082.0 يحوّله إلى إعداد كسر للبيع.' })
  }
  // The API's order: newest first.
  return [xau, sol]
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
    if (p === '/api/warroom/threads') return json(200, { data: [MAIN, SOL_THREAD] })
    if (p === '/api/warroom/messages') {
      const data = url.searchParams.get('thread_id') === MAIN.id ? messages(lang) : []
      return json(200, { data, meta: { next_cursor: null } })
    }
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.setViewportSize({ width: 1280, height: 1400 })
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
  const cards = page.getByTestId('ta-card')
  await expect(cards).toHaveCount(2)
  return { setup: cards.and(page.locator('[data-setup="breakout"]')), none: cards.and(page.locator('[data-setup="none"]')) }
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="ta-card"]').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

test('Arabic RTL, dark: both cards render, the ladder stays left-to-right, no axe violations', async ({ page }) => {
  const { setup, none } = await open(page, 'ar', 'dark')
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)

  await expect(setup.getByTestId('ta-headline')).toHaveText('اختراق للشراء')
  await expect(setup.getByTestId('ta-thin')).toBeVisible()
  await expect(setup.getByTestId('ta-entry')).toContainText('248.60–250.20')
  const ladder = setup.getByTestId('ladder')
  await expect(ladder).toBeVisible()
  await expect(ladder).toHaveAttribute('dir', 'ltr')
  // A number line: the stop draws left of the entry, the entry left of every target, in Arabic too.
  const x = async (id: string, nth = 0) => (await ladder.getByTestId(id).nth(nth).boundingBox())!.x
  const stopX = await x('ld-stop')
  const entryX = await x('ld-entry')
  expect(stopX).toBeLessThan(entryX)
  expect(entryX).toBeLessThan(await x('ld-target', 0))
  expect(await x('ld-target', 0)).toBeLessThan(await x('ld-target', 2))
  // Labels on one row never touch (≥ 10 px apart, design §4), so nothing overlaps.
  const below = await ladder.locator('.ladder-label.is-below').evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => [r.left, r.right]).sort((a, b) => a[0]! - b[0]!))
  for (let i = 1; i < below.length; i++) expect(below[i]![0]! - below[i - 1]![1]!).toBeGreaterThanOrEqual(9.5)

  await expect(none.getByTestId('ta-headline')).toHaveText(T.ar.ta.noSetup)
  await expect(none.getByTestId('ta-condition')).toContainText('4,082.0')
  await expect(none.getByTestId('ta-entry')).toHaveCount(0)

  await page.waitForTimeout(300)
  expect(await axeViolations(page)).toEqual([])
  await setup.screenshot({ path: `${SHOTS}/setup-ar-dark.png` })
  await none.screenshot({ path: `${SHOTS}/none-ar-dark.png` })
})

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'light']] as const) {
  test(`${lang} ${theme}: no axe violations`, async ({ page }) => {
    const { setup } = await open(page, lang, theme)
    await expect(setup.getByTestId('ladder')).toBeVisible()
    await page.waitForTimeout(300)
    expect(await axeViolations(page)).toEqual([])
    await setup.screenshot({ path: `${SHOTS}/setup-${lang}-${theme}.png` })
  })
}

test('narrow feed: the compact ladder replaces the full one', async ({ page }) => {
  const { setup } = await open(page, 'en', 'light')
  await page.setViewportSize({ width: 390, height: 1400 })
  await expect(setup.getByTestId('ladder-compact')).toBeVisible()
  await expect(setup.getByTestId('ladder')).toBeHidden()
  await setup.screenshot({ path: `${SHOTS}/setup-en-390.png` })
})

for (const lang of ['en', 'ar'] as const) {
  test(`${lang}: clicking the instrument opens it in the Inspector`, async ({ page }) => {
    const { setup } = await open(page, lang, lang === 'ar' ? 'dark' : 'light')
    const inst = setup.getByTestId('ta-inst')
    await expect(inst).toHaveAccessibleName(T[lang].ta.inspect.replace('{{inst}}', 'SOL-USDT-SWAP'))
    await inst.click()
    await expect(page).toHaveURL(/\/warroom\/threads\/thread-sol$/)
    const inspector = page.getByTestId('wr-inspector')
    await expect(inspector.locator('.insp-name')).toHaveText('SOL-USDT-SWAP')
    await expect(inspector).toContainText(T[lang].instSub)
  })
}

test('keyboard: the instrument button takes focus with a visible ring and opens on Enter', async ({ page }) => {
  const { setup } = await open(page, 'en', 'light')
  const inst = setup.getByTestId('ta-inst')
  await inst.focus()
  await expect(inst).toBeFocused()
  const ring = await inst.evaluate((el) => getComputedStyle(el).boxShadow)
  expect(ring).not.toBe('none')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/warroom\/threads\/thread-sol$/)
})
