import { expect, test, type Page } from '@playwright/test'
import type { ProposalDetail } from '../src/features/warroom/cards/proposal'
import { PROPOSAL_ID, proposalDetail, proposalMessage, sentDetail } from '../src/features/warroom/fixtures/proposal'

/*
 * ATL-240 (decision #255 (7)): Live is unmistakable everywhere. With GET /api/environment saying
 * live, the toolbar badge and the footer read Live in the live style on War Room, Portfolio,
 * Settings and Backtester, in en and ar, light and dark. Approve on a live proposal opens the
 * real-order sheet: Cancel sends nothing, Confirm sends live_confirmed: true; a demo proposal
 * approves at once. Every other endpoint answers 404, which the pages show as their error states:
 * the shell is what these tests look at.
 */

const SHOTS = '../../evidence/ATL-240'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:19:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const WORD = { en: 'Live', ar: 'حقيقي' } as const

type Env = 'demo' | 'live'

async function fakeApi(page: Page, opts: { env: Env; proposalEnv?: Env }) {
  const state: { detail: ProposalDetail; approves: (string | null)[] } = {
    detail: proposalDetail({ env: opts.proposalEnv ?? opts.env }),
    approves: [],
  }
  const envDoc = {
    data: {
      active_env: opts.env,
      live_started_at: opts.env === 'live' ? '2026-10-25T08:00:00.000000Z' : null,
      first_month_ends_at: opts.env === 'live' ? '2026-11-24T08:00:00.000000Z' : null,
      in_first_live_month: opts.env === 'live',
      switched_at: opts.env === 'live' ? '2026-10-25T08:00:00.000000Z' : null,
      confirmation_text: 'TRADE LIVE',
      preflight: { ok: true, items: [] },
    },
  }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const origin = req.headers()['origin'] ?? '*'
    const cors = { 'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS')
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE' } })
    const p = url.pathname
    if (p === '/sanctum/csrf-cookie') return json(204)
    if (p === '/api/auth/status') return json(200, { owner_exists: true })
    if (p === '/api/auth/me') return json(200, { data: OWNER })
    if (p === '/api/environment') return json(200, envDoc)
    if (p === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (p === '/api/warroom/messages') return json(200, { data: url.searchParams.get('thread_id') === MAIN.id ? [proposalMessage()] : [], meta: { next_cursor: null } })
    if (p === `/api/proposals/${PROPOSAL_ID}` && req.method() === 'GET') return json(200, { data: state.detail })
    if (p === `/api/proposals/${PROPOSAL_ID}/mark`) return json(200, { data: { inst_id: state.detail.inst_id, mark: '249.95', mark_at: new Date().toISOString() } })
    if (p === `/api/proposals/${PROPOSAL_ID}/approve` && req.method() === 'POST') {
      const body = req.postData()
      state.approves.push(body)
      if (state.detail.env === 'live' && !(body && (JSON.parse(body) as { live_confirmed?: unknown }).live_confirmed === true)) {
        return json(422, { ok: false, error: 'live_confirmation_required', message: 'This is a live proposal with real money. Confirm the live approval to send it.', execution: [], proposal: state.detail })
      }
      state.detail = { ...sentDetail(), env: state.detail.env }
      return json(200, { ok: true, error: null, message: null, execution: { ok: true, status: 'sent', reason: 'sent', detail: 'Order sent; stop confirmed on OKX.' }, proposal: state.detail })
    }
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
  return state
}

async function prepare(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.clock.setFixedTime(new Date('2026-10-28T11:32:00Z'))
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
}

const PAGES = [
  ['warroom', '/warroom'],
  ['portfolio', '/portfolio'],
  ['settings', '/settings/risk'],
  ['backtester', '/backtester'],
] as const

for (const lang of ['en', 'ar'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    test(`${lang} ${theme}: the toolbar badge and the footer read ${WORD[lang]} in the live style on every page`, async ({ page }) => {
      await prepare(page, lang, theme)
      await fakeApi(page, { env: 'live' })
      for (const [name, path] of PAGES) {
        await page.goto(path)
        const badge = page.getByTestId('env-badge')
        await expect(badge).toHaveText(WORD[lang])
        await expect(badge).toHaveAttribute('data-env', 'live')
        await expect(badge).toHaveClass(/bg-live/)
        const footer = page.getByTestId('footer-env')
        await expect(footer).toHaveText(WORD[lang])
        await expect(footer).toHaveClass(/shell-footer-live/)
        await expect(page.locator('html')).toHaveClass(/is-live/)
        if (lang === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
        // The live colour is the orange token, never the loss red, and the word is there too.
        const bg = await footer.evaluate((el) => getComputedStyle(el).backgroundColor)
        expect(bg).toMatch(/rgb\(255, (149|159), (0|10)\)/)
        await page.screenshot({ path: `${SHOTS}/${name}-${lang}-${theme}.png` })
      }
    })
  }
}

test('demo: the badge and footer read Demo, with no live wire', async ({ page }) => {
  await prepare(page, 'en', 'light')
  await fakeApi(page, { env: 'demo' })
  await page.goto('/portfolio')
  await expect(page.getByTestId('env-badge')).toHaveText('Demo')
  await expect(page.getByTestId('footer-env')).toHaveText('Demo')
  await expect(page.locator('html')).not.toHaveClass(/is-live/)
})

for (const lang of ['en', 'ar'] as const) {
  test(`${lang}: Approve on a live proposal opens the real-order sheet; Cancel sends nothing, Confirm sends live_confirmed: true`, async ({ page }) => {
    await prepare(page, lang, 'light')
    const state = await fakeApi(page, { env: 'live' })
    await page.goto('/warroom')
    const card = page.getByTestId('pr-card')
    await expect(card).toHaveAttribute('data-status', 'awaiting_owner')

    await card.getByTestId('pr-approve').click()
    const sheet = page.getByTestId('pr-live-sheet')
    await expect(sheet).toBeVisible()
    await expect(page.getByTestId('pr-live-title')).toContainText(lang === 'en' ? 'This places a real order' : 'هذا يضع أمرًا حقيقيًا')
    await expect(page.getByTestId('live-tag')).toHaveText(WORD[lang])
    await expect(sheet.getByTestId('lvo-inst')).toContainText('SOL-USDT-SWAP')
    await expect(sheet.getByTestId('lvo-size')).toContainText('$')
    await expect(sheet.getByTestId('lvo-risk')).toContainText('$')
    await expect(page.getByTestId('pr-live-go')).toBeFocused()
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${SHOTS}/real-order-sheet-${lang}.png` })

    await page.getByTestId('pr-live-cancel').click()
    await expect(sheet).toHaveCount(0)
    expect(state.approves).toEqual([])
    await expect(card).toHaveAttribute('data-status', 'awaiting_owner')

    await card.getByTestId('pr-approve').click()
    await page.getByTestId('pr-live-go').click()
    await expect(card).toHaveAttribute('data-status', 'sent')
    expect(state.approves).toHaveLength(1)
    expect(JSON.parse(state.approves[0]!)).toEqual({ live_confirmed: true })
  })
}

test('a demo proposal approves with no extra sheet', async ({ page }) => {
  await prepare(page, 'en', 'light')
  const state = await fakeApi(page, { env: 'demo' })
  await page.goto('/warroom')
  const card = page.getByTestId('pr-card')
  await expect(card).toHaveAttribute('data-status', 'awaiting_owner')
  await card.getByTestId('pr-approve').click()
  await expect(card).toHaveAttribute('data-status', 'sent')
  await expect(page.getByTestId('pr-live-sheet')).toHaveCount(0)
  expect(state.approves).toHaveLength(1)
  expect(state.approves[0] ?? '').not.toContain('live_confirmed')
})
