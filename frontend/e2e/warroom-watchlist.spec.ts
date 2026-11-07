import { expect, test, type Page } from '@playwright/test'
import arWarroom from '../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { apiItem, apiWatchlist, cards, pinItem, watchlistMessage } from '../src/features/warroom/fixtures/watchlist'
import type { ApiWatchlist } from '../src/features/warroom/watchlist'

/**
 * ATL-115: Market Scout's watchlist card in the War Room feed. A fake Laravel serves the Main
 * thread with the 08:00 watchlist message (ATL-108's card) and ATL-103's watchlist API, with a
 * small in-memory state so Remove and Pin answer like the server. The browser clock is set inside
 * the 10-minute edit window (it closes at 08:10 Baghdad = 05:10 UTC).
 */
const SHOTS = '../../evidence/ATL-115'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T05:00:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const SOL = { inst_id: 'SOL-USDT-SWAP', inst_type: 'SWAP', asset_class: 'crypto', base_ccy: 'SOL', quote_ccy: 'USDT', settle_ccy: 'USDT', blocked: false }
const SOL_SPOT = { ...SOL, inst_id: 'SOL-USDT', inst_type: 'SPOT' }

type Answer = 'ok' | '409' | '500'

interface FakeOptions {
  lang?: 'en' | 'ar'
  card?: Record<string, unknown>
  answer?: Answer
  /** Arabic agent text, so the RTL screenshots carry no English prose. */
  arabic?: boolean
}

async function fakeApi(page: Page, opts: FakeOptions = {}, removedAt = '2026-10-28T05:08:31.000000Z') {
  const card = opts.card ?? cards.published()
  const message = watchlistMessage(card, { id: 'msg-wl-v1', thread_id: MAIN.id })
  if (opts.arabic) {
    message.content_md = 'قائمة اليوم: BTC-USDT-SWAP شراء (0.58)، XAU-USDT-SWAP بيع (0.52)، TSLA-USDT-SWAP محايد (0.40).'
    const c = message.card as Record<string, unknown>
    c.backdrop = [
      'يتجه BTC و ETH صعودًا مع ارتفاع العقود المفتوحة، والتقلب المحقق في الثلث الأدنى من الشهر.',
      'بيانات التضخم الأمريكية الساعة 15:30 هي حدث اليوم.',
      'يتحرك الذهب في نطاق تحت 4,120 بعد أربعة أيام من التدفقات الخارجة.',
    ]
    c.picks = (c.picks as Array<Record<string, unknown>>).map((p, i) => ({
      ...p,
      thesis: ['على بُعد نصف ATR من أعلى مستوى في 20 يومًا عند 119,950، مع ارتفاع العقود المفتوحة 6.1% خلال 24 ساعة.', 'رُفض مرتين عند قمة النطاق 4,120 هذا الأسبوع.', 'النتائج بعد إغلاق السوق الأمريكية الساعة 23:05.'][i],
      invalidation: ['إغلاق أربع ساعات تحت 116,800 ينهي فكرة الاختراق.', 'إغلاق ساعة فوق 4,128 يعني أن قمة النطاق انكسرت.', ''][i],
      catalysts: (p.catalysts as Array<Record<string, unknown>>).map((k) => ({ ...k, name: k.name === 'US CPI (Sep)' ? 'التضخم الأمريكي (سبتمبر)' : k.name === 'Tesla Q3 earnings' ? 'نتائج تسلا للربع الثالث' : 'كلمة والر' })),
    }))
    c.rejected = (c.rejected as Array<Record<string, unknown>>).map((r) => ({ ...r, reason: 'لا محفّز خلال 48 ساعة.' }))
  }
  // The API holds what the Scout saved: the card's picks (capped values, text) as items.
  let state: ApiWatchlist = apiWatchlist({ items: ((message.card as Record<string, unknown>).picks as Array<Record<string, unknown>>).map((pk) => apiItem(pk)) })
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
    if (p === '/api/warroom/messages') return json(200, { data: [message], meta: { next_cursor: null } })
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    if (p === '/api/watchlists') return json(200, { env: 'demo', date: state.date, timezone: 'Asia/Baghdad', watchlist: state })
    if (p === '/api/instruments') {
      const q = (url.searchParams.get('q') ?? '').toUpperCase()
      return json(200, { data: [SOL, SOL_SPOT].filter((i) => i.inst_id.startsWith(q) || i.base_ccy.startsWith(q)) })
    }
    const write = /^\/api\/watchlists\/([^/]+)\/(?:items\/([^/]+)\/(remove|restore)|(pin))$/.exec(p)
    if (write && req.method() === 'POST') {
      if (opts.answer === '409')
        return json(409, { ok: false, error: 'edit_window_closed', message: 'The edit window for this watchlist has closed.', edit_window_ends_at: state.edit_window_ends_at })
      if (opts.answer === '500') return json(500, { message: 'Server Error' })
      const [, , itemId, action, pin] = write
      const all = [...state.items, ...state.removed_items] as Array<Record<string, unknown>>
      if (pin) {
        const inst = String((req.postDataJSON() as { inst_id: string }).inst_id).toUpperCase()
        const item = pinItem(inst, all.length + 1)
        state = { ...state, items: [...state.items, item] }
        return json(200, { watchlist: state, item })
      }
      const item = all.find((i) => i.id === itemId)!
      const changed = { ...item, removed_by_owner_at: action === 'remove' ? removedAt : null }
      const rest = all.filter((i) => i.id !== itemId)
      const next = [...rest, changed].sort((a, b) => Number(a.rank) - Number(b.rank))
      state = { ...state, items: next.filter((i) => !i.removed_by_owner_at), removed_items: next.filter((i) => i.removed_by_owner_at) }
      return json(200, { watchlist: state, item: changed })
    }
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, opts: FakeOptions & { at: string; theme?: 'light' | 'dark'; tall?: boolean }) {
  const lang = opts.lang ?? 'en'
  const theme = opts.theme ?? 'light'
  // Tall enough that the whole card is on screen for its screenshot.
  await page.setViewportSize({ width: 1280, height: opts.tall === false ? 900 : 1700 })
  await page.clock.install({ time: new Date(opts.at) })
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ colorScheme: theme })
  await fakeApi(page, opts)
  await page.goto('/warroom')
  const card = page.getByTestId('wl-card')
  await expect(card).toBeVisible()
  return card
}

test('inside the window, removing an item and pinning SOL-USDT-SWAP update the card; at zero the controls go', async ({ page }) => {
  // 08:08:30 Baghdad: 1:30 left.
  const card = await open(page, { at: '2026-10-28T05:08:30Z' })
  await expect(card.getByTestId('wl-countdown')).toContainText('1:')
  await expect(card.getByTestId('wl-remove').first()).toBeVisible()

  // Remove TSLA: it collapses to a record line with Undo.
  await card.getByRole('button', { name: 'Remove TSLA-USDT-SWAP from today’s watchlist' }).click()
  const removed = card.getByTestId('wl-removed')
  await expect(removed).toContainText('TSLA-USDT-SWAP')
  await expect(removed).toContainText('Removed by you at')
  await expect(removed.getByTestId('wl-undo')).toBeFocused()
  await expect(card.getByTestId('wl-pick')).toHaveCount(2)

  // Pin SOL-USDT-SWAP through the search popover.
  await card.getByTestId('wl-pin-instrument').click()
  const input = card.getByTestId('wl-pop-input')
  await expect(input).toBeFocused()
  await input.fill('sol')
  await expect(card.getByTestId('wl-opt').first()).toHaveAttribute('data-inst', 'SOL-USDT-SWAP')
  await page.keyboard.press('Enter')
  await expect(card.getByTestId('wl-pop')).toHaveCount(0)
  const sol = card.locator('[data-wl-row="SOL-USDT-SWAP"]')
  await expect(sol).toContainText('Pinned by you')
  await expect(sol).toContainText(enWarroom.wl.pinnedNote)
  await expect(card.getByTestId('wl-pin-instrument')).toBeFocused()
  await card.screenshot({ path: `${SHOTS}/window-open-edited-en-light.png` })

  // The countdown reaches zero: the band, Remove, Undo and Pin go, the footer records the edits.
  await page.clock.fastForward('02:00')
  await expect(card.getByTestId('wl-band')).toHaveCount(0)
  await expect(card.getByTestId('wl-remove')).toHaveCount(0)
  await expect(card.getByTestId('wl-undo')).toHaveCount(0)
  await expect(card.getByTestId('wl-pin-rejected')).toHaveCount(0)
  await expect(card.getByTestId('wl-foot')).toContainText('Editing closed at 08:10. You removed TSLA-USDT-SWAP and pinned SOL-USDT-SWAP.')
  await card.screenshot({ path: `${SHOTS}/window-closed-en-light.png` })
})

test('a 409 edit_window_closed hides the controls and shows the closed notice', async ({ page }) => {
  const card = await open(page, { at: '2026-10-28T05:05:00Z', answer: '409' })
  await card.getByRole('button', { name: 'Remove BTC-USDT-SWAP from today’s watchlist' }).click()
  const notice = card.getByTestId('wl-conflict')
  await expect(notice).toBeVisible()
  await expect(notice).toHaveAttribute('role', 'alert')
  await expect(notice).toContainText(enWarroom.wl.conflictTitle)
  await expect(notice).toContainText('The edit window had already closed, so BTC-USDT-SWAP is still on the list.')
  await expect(notice).toBeFocused()
  await expect(card.getByTestId('wl-band')).toHaveCount(0)
  await expect(card.getByTestId('wl-remove')).toHaveCount(0)
  await expect(card.getByTestId('wl-pin-rejected')).toHaveCount(0)
  // The row came back.
  await expect(card.getByTestId('wl-pick')).toHaveCount(3)
  await card.screenshot({ path: `${SHOTS}/conflict-409-en-light.png` })
})

test('a 500 rolls the removal back and says nothing changed', async ({ page }) => {
  const card = await open(page, { at: '2026-10-28T05:05:00Z', answer: '500' })
  await card.getByRole('button', { name: 'Remove XAU-USDT-SWAP from today’s watchlist' }).click()
  await expect(page.getByText('Couldn’t remove XAU-USDT-SWAP')).toBeVisible()
  await expect(card.getByTestId('wl-pick')).toHaveCount(3)
  await expect(card.getByTestId('wl-band')).toBeVisible()
})

for (const theme of ['light', 'dark'] as const) {
  for (const window of ['open', 'closed'] as const) {
    test(`the card in Arabic (RTL), ${theme}, window ${window}: nothing clipped, bias is icon + word`, async ({ page }) => {
      const at = window === 'open' ? '2026-10-28T05:03:00Z' : '2026-10-28T11:32:00Z'
      const card = await open(page, { at, lang: 'ar', theme, arabic: true, card: cards.newsDown() })
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')

      // Bias: an icon plus the Arabic word, on every analysed pick.
      const biases = card.getByTestId('wl-bias')
      await expect(biases).toHaveCount(3)
      for (const [i, b] of (['long', 'short', 'neutral'] as const).entries()) {
        await expect(biases.nth(i)).toContainText(arWarroom.wl.bias[b])
        await expect(biases.nth(i).getByTestId(`wl-bias-icon-${b}`)).toBeVisible()
      }
      // Mirrored: the head tile sits at the inline start (the right), bias arrows flip.
      const box = (await card.boundingBox())!
      const tile = (await card.locator('.hv-tile').boundingBox())!
      expect(tile.x).toBeGreaterThan(box.x + box.width / 2)
      await expect(card.getByTestId('wl-bias-icon-long')).toHaveCSS('transform', 'matrix(-1, 0, 0, 1, 0, 0)')
      // The rail runs right to left: 08:00 at the right.
      const ticks = card.locator('.wl-axis .wl-tick.num.is-first')
      const first = (await ticks.boundingBox())!
      expect(first.x).toBeGreaterThan(box.x + box.width / 2)

      // Nothing clipped: no text box spills out of its container or the card.
      const spills = await card.evaluate((root) => {
        const r = root.getBoundingClientRect()
        const out: string[] = []
        for (const el of root.querySelectorAll<HTMLElement>('p, span, b, li, time, bdi, button, h4')) {
          if (el.closest('.wl-meter') || el.classList.contains('sr-only') || el.closest('.sr-only')) continue
          const b = el.getBoundingClientRect()
          if (b.width === 0 || getComputedStyle(el).visibility === 'hidden') continue
          if (b.left < r.left - 1 || b.right > r.right + 1) out.push(`${el.className || el.tagName}: ${el.textContent?.slice(0, 30)}`)
          if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow === 'hidden') out.push(`overflow ${el.className}`)
        }
        return out
      })
      expect(spills).toEqual([])
      // No English UI string leaked into the Arabic card.
      for (const s of [enWarroom.wl.backdrop, enWarroom.wl.notPicked, enWarroom.wl.support, 'Long', 'Short', 'Neutral']) await expect(card).not.toContainText(s)

      await card.screenshot({ path: `${SHOTS}/card-ar-${theme}-${window}.png` })
    })
  }
}

test('English dark, partial evidence, for the record', async ({ page }) => {
  const card = await open(page, { at: '2026-10-28T11:32:00Z', theme: 'dark', card: cards.partialEvidence() })
  await expect(card.getByTestId('wl-partial')).toBeVisible()
  await card.screenshot({ path: `${SHOTS}/partial-en-dark.png` })
})
