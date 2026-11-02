import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-202: the Journal against a fake Laravel serving ATL-200's GET /api/journal (cursor pages) and
 * GET /api/journal/{id}, with ATL-194's post-mortem data. The empty state and a filled list, in
 * English light and Arabic dark, with no axe violations. Screenshots go to ../../evidence/ATL-202/.
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-202'
const NOW = Date.now()
const HOUR = 3_600_000
const N = 30
const INSTS = ['BTC-USDT-SWAP', 'XAU-USDT-SWAP', 'TSLA-USDT-SWAP', 'ETH-USDT-SWAP', 'SPY-USDT-SWAP']
const ASSET: Record<string, string> = { BTC: 'crypto', ETH: 'crypto', XAU: 'commodity', TSLA: 'stock', SPY: 'etf' }
const SETUPS = ['breakout', 'pullback_to_support', 'range_fade', 'squeeze_expansion']
const REASONS = ['take_profit', 'stop_loss', 'time_stop', 'take_profit', 'invalidation', 'manual']
const iso = (ms: number) => new Date(ms).toISOString().replace('Z', '000Z')
const uuid = (i: number) => `e${String(i).padStart(7, '0')}-0000-4000-8000-000000000000`

const entries = Array.from({ length: N }, (_, i) => {
  const inst = INSTS[i % INSTS.length]!
  const reason = REASONS[i % REASONS.length]!
  const r = reason === 'stop_loss' ? -1.02 : reason === 'take_profit' ? 1.4 + (i % 3) * 0.4 : reason === 'time_stop' ? 0.04 : -0.35
  const outcome = Math.abs(r) < 0.1 ? 'scratch' : r > 0 ? 'win' : 'loss'
  const closed = NOW - (i * 19 + 2) * HOUR
  const side = i % 2 ? 'short' : 'long'
  const setup = SETUPS[i % SETUPS.length]!
  const asset = ASSET[inst.split('-')[0]!]!
  const pending = i === 1
  const lesson = pending ? null : i % 2 ? 'Skip a breakout whose trigger fires more than 1 ATR past the level: the move is already spent.' : 'When the 1H trend and a strong level agree, let the take-profit work, do not trail early.'
  return {
    id: uuid(i),
    env: 'demo',
    title: `${inst} ${side}`,
    inst_id: inst,
    agent: 'portfolio_manager',
    proposal_id: `b${String(i).padStart(7, '0')}-0000-4000-8000-000000000000`,
    expected_md: 'Plan',
    actual_md: 'Result',
    lessons_md: lesson,
    tags: [`setup:${setup}`, `asset:${asset}`, 'regime:trend', 'catalyst:none', `outcome:${outcome}`, outcome],
    data: {
      side,
      asset_class: asset,
      outcome,
      expected: {
        entry: '100',
        stop: side === 'long' ? '95' : '105',
        take_profits: [
          { price: side === 'long' ? '105' : '95', pct: '50' },
          { price: side === 'long' ? '110' : '90', pct: '30' },
          { price: side === 'long' ? '115' : '85', pct: '20' },
        ],
        expected_r: '1.9',
        probability: '0.55',
        invalidation: 'A 1H close back through the entry zone',
        time_stop_minutes: 1440,
      },
      actual: {
        avg_entry: '100.1',
        avg_exit: (100 + (side === 'long' ? 1 : -1) * r * 5).toFixed(2),
        fees_usd: '-0.42',
        funding_usd: '-0.08',
        realised_pnl_usd: (r * 212.5).toFixed(2),
        initial_risk_usd: '212.50',
        realised_r: r.toFixed(2),
        mfe_r: (Math.max(r, 0) + 0.45).toFixed(2),
        mae_r: (Math.min(r, 0) - 0.2).toFixed(2),
        opened_at: iso(closed - (5 + i) * HOUR),
        closed_at: iso(closed),
        holding_minutes: (5 + i) * 60,
        close_reason: reason,
      },
      votes: [
        { agent: 'technical_analyst', vote: 'take', confidence: '0.7' },
        { agent: 'derivatives_sentiment', vote: 'take', confidence: '0.6' },
        { agent: 'news_intelligence', vote: 'change', confidence: '0.55' },
        { agent: 'historian', vote: 'take', confidence: '0.65' },
        { agent: 'devils_advocate', vote: 'skip', confidence: '0.6' },
      ],
      agents: ['devils_advocate', 'historian', 'news_intelligence', 'strategist', 'technical_analyst'],
      review: {
        right_md: '**Technical Analyst** placed the entry zone at the 4H level where the move turned.',
        wrong_md: '**News Intelligence** rated the event risk as minor, but the release moved price 1.6 ATR within 20 minutes.',
      },
      lesson,
      lesson_pending: pending,
      lesson_attempts: 1,
    },
    created_at: iso(closed + 6 * 60_000),
  }
})

const PAGE = 12

async function fakeApi(page: Page, list: typeof entries) {
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return json(200, { data: OWNER })
    if (url.pathname === '/api/journal') {
      let all = list
      const tag = url.searchParams.get('tag')
      if (tag) all = all.filter((e) => e.tags.includes(tag))
      const outcome = url.searchParams.get('outcome')
      if (outcome) all = all.filter((e) => e.data.outcome === outcome)
      const at = Number(url.searchParams.get('cursor') ?? '0')
      const limit = Number(url.searchParams.get('limit') ?? PAGE)
      const next = at + limit < all.length ? String(at + limit) : null
      return json(200, { data: all.slice(at, at + limit), meta: { env: 'demo', per_page: limit, next_cursor: next, prev_cursor: null } })
    }
    const one = /^\/api\/journal\/(.+)$/.exec(url.pathname)
    if (one) {
      const e = list.find((x) => x.id === one[1])
      return e ? json(200, { data: e }) : json(404, { message: 'Not found' })
    }
    if (url.pathname === '/api/connections') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', path = '/journal') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(path)
}

async function noAxe(page: Page) {
  const axe = await new AxeBuilder({ page }).include('[data-testid="journal-page"]').analyze()
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
}

for (const [lang, theme] of [
  ['en', 'light'],
  ['ar', 'dark'],
] as const) {
  const dir = lang === 'ar' ? 'rtl' : 'ltr'

  test(`empty journal, ${dir}-${theme}, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page, [])
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    await expect(page.getByTestId('journal-empty')).toBeVisible()
    await expect(page.getByTestId('journal-empty')).toContainText(lang === 'ar' ? 'لا توجد مراجعات بعد' : 'No post-mortems yet')
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/journal-empty-${dir}-${theme}.png` })
  })

  test(`filled journal, ${dir}-${theme}: list, entry, infinite scroll, with no axe violations`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await fakeApi(page, entries)
    await open(page, lang, theme)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByTestId('journal-row')).toHaveCount(PAGE)
    // The newest shows beside the list without being written to the URL.
    await expect(page.getByTestId('journal-entry')).toHaveAttribute('data-id', entries[0]!.id)
    await expect(page).toHaveURL(/\/journal$/)
    expect(await page.locator('main').innerText()).not.toMatch(/[٠-٩۰-۹]/)
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/journal-list-${dir}-${theme}.png` })

    // The list mirrors: in Arabic the list pane sits at the right.
    const list = (await page.getByTestId('journal-list').boundingBox())!
    const pane = (await page.getByTestId('journal-entry-pane').boundingBox())!
    if (dir === 'ltr') expect(list.x).toBeLessThan(pane.x)
    else expect(list.x).toBeGreaterThan(pane.x)

    // Open the pending one: its lesson is still being written.
    await page.getByTestId('journal-row').nth(1).click()
    await expect(page).toHaveURL(new RegExp(`entry=${entries[1]!.id}`))
    await expect(page.getByTestId('journal-lesson')).toContainText(lang === 'ar' ? 'الدرس قيد الكتابة' : 'Lesson still being written')
    await noAxe(page)
    await page.screenshot({ path: `${SHOTS}/journal-pending-${dir}-${theme}.png` })

    // Scroll the list to the end: pages of 12 load until all 30 are shown.
    const box = page.getByTestId('journal-list')
    for (let step = 0; step < 30; step++) {
      await box.evaluate((el) => el.scrollBy(0, 1200))
      await page.waitForTimeout(80)
      if ((await page.getByTestId('journal-row').count()) === N) break
    }
    await expect(page.getByTestId('journal-row')).toHaveCount(N)
    await expect(page.locator('.jr-end')).toBeVisible()
  })
}

test('filters live in the URL and survive a reload (Arabic dark, phone width)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeApi(page, entries)
  await open(page, 'ar', 'dark', '/journal?outcome=loss&tag=setup%3Abreakout')
  await expect(page.getByTestId('journal-chips')).toBeVisible()
  const rows = page.getByTestId('journal-row')
  await expect(rows.first()).toBeVisible()
  // every filter stays on screen: nothing scrolls sideways
  const overflow = await page.locator('.jr-filters').evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: `${SHOTS}/journal-filtered-phone-rtl-dark.png` })
  await page.reload()
  await expect(page.getByTestId('journal-chips')).toContainText('اختراق')
  await rows.first().click()
  await expect(page.getByTestId('journal-entry')).toBeVisible()
  await expect(page.getByTestId('journal-list')).toBeHidden()
  await noAxe(page)
  await page.screenshot({ path: `${SHOTS}/journal-entry-phone-rtl-dark.png`, fullPage: true })
  await page.goBack()
  await expect(page.getByTestId('journal-list')).toBeVisible()
})
