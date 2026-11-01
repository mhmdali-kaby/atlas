import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arProposal from '../src/i18n/locales/ar/proposal.json' with { type: 'json' }
import enProposal from '../src/i18n/locales/en/proposal.json' with { type: 'json' }
import type { ProposalDetail } from '../src/features/warroom/cards/proposal'
import { PROPOSAL_ID, proposalDetail, proposalMessage, sentDetail } from '../src/features/warroom/fixtures/proposal'

/**
 * ATL-164: the owner approves a proposal from its War Room card (brief §12, §7.9 step 5; design
 * v11 wr-proposal). A fake Laravel serves the Main thread with one Strategist proposal awaiting
 * approval; POST /approve answers 200 and the proposal becomes `sent`, which the card then shows.
 * Runs in English light, English dark and Arabic RTL dark, with axe on the card each time.
 */
const SHOTS = '../../evidence/ATL-164'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:19:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const T = { en: enProposal, ar: arProposal }

async function fakeApi(page: Page, lang: 'en' | 'ar') {
  const state: { detail: ProposalDetail; approves: number } = { detail: proposalDetail(), approves: 0 }
  if (lang === 'ar') {
    state.detail.rationale_md = 'الموافقة على صناديق SOL الفورية، مع اختراق على إطار الساعة بمراكز شراء جديدة لم تزدحم بعد.'
    state.detail.da_review!.card = { ...state.detail.da_review!.card, strongest_objection: 'مركز BTC مترابط بنسبة 0.82، وخفض الحجم إلى 3% يعالج ذلك.' }
  }
  const message = proposalMessage()
  if (lang === 'ar') message.content_md = '**شراء SOL-USDT-SWAP** النسخة 2: حد الدخول 248.60–250.20، والوقف 241.80، وثلاثة أهداف.'
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
    if (p === '/api/warroom/messages') return json(200, { data: url.searchParams.get('thread_id') === MAIN.id ? [message] : [], meta: { next_cursor: null } })
    if (p === `/api/proposals/${PROPOSAL_ID}` && req.method() === 'GET') return json(200, { data: state.detail })
    if (p === `/api/proposals/${PROPOSAL_ID}/approve` && req.method() === 'POST') {
      state.approves += 1
      state.detail = { ...sentDetail(), rationale_md: state.detail.rationale_md, da_review: state.detail.da_review }
      return json(200, { ok: true, error: null, message: null, execution: { ok: true, status: 'sent', reason: 'sent', detail: 'Order sent; stop confirmed on OKX.' }, proposal: state.detail })
    }
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
  return state
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
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
  const state = await fakeApi(page, lang)
  await page.goto('/warroom')
  const card = page.getByTestId('pr-card')
  await expect(card).toHaveAttribute('data-status', 'awaiting_owner')
  return { card, state }
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="pr-card"]').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

for (const [lang, theme] of [['en', 'light'], ['en', 'dark'], ['ar', 'dark']] as const) {
  test(`${lang} ${theme}: the owner approves and the card shows "sent", with no axe violations`, async ({ page }) => {
    const { card, state } = await open(page, lang, theme)
    if (lang === 'ar') await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
    if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)

    const t = T[lang]
    await expect(card.getByTestId('pr-status')).toHaveText(t.st.awaiting_owner)
    await expect(card.getByTestId('pr-countdown')).toContainText('6:00')
    await expect(card.getByTestId('ladder')).toHaveAttribute('dir', 'ltr')
    await expect(card.getByTestId('pr-approve')).toBeVisible()
    await page.waitForTimeout(200)
    expect(await axeViolations(page)).toEqual([])
    await card.screenshot({ path: `${SHOTS}/awaiting-${lang}-${theme}.png` })

    await card.getByTestId('pr-approve').click()
    await expect(card).toHaveAttribute('data-status', 'sent')
    await expect(card.getByTestId('pr-status')).toHaveText(t.st.sent)
    await expect(card.getByTestId('pr-actions')).toHaveCount(0)
    await expect(card.getByTestId('pr-countdown')).toHaveCount(0)
    await expect(card.getByTestId('pr-outcome')).toContainText('249.40')
    expect(state.approves).toBe(1)
    await page.waitForTimeout(200)
    expect(await axeViolations(page)).toEqual([])
    await card.screenshot({ path: `${SHOTS}/sent-${lang}-${theme}.png` })
  })
}

test('Arabic RTL: the Reject and Edit sheets open, and a widened stop is refused', async ({ page }) => {
  const { card } = await open(page, 'ar', 'light')
  await card.getByTestId('pr-reject').click()
  const submit = page.getByTestId('pr-reject-submit')
  await expect(submit).toBeDisabled()
  await page.getByTestId('pr-reject-text').fill('الوقت غير مناسب')
  await expect(submit).toBeEnabled()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/reject-sheet-ar.png` })
  await page.keyboard.press('Escape')

  await card.getByTestId('pr-edit').click()
  const stop = page.locator('input[data-testid$="-stop"]')
  await stop.fill('240.50')
  await expect(page.getByText(T.ar.err.stopWiden.replace('{{stop}}', '241.80'))).toBeVisible()
  await expect(page.getByTestId('pr-edit-save')).toBeDisabled()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${SHOTS}/edit-sheet-ar.png` })
})

test('narrow feed: the compact ladder and the target rows replace the full ladder', async ({ page }) => {
  const { card } = await open(page, 'en', 'light')
  await page.setViewportSize({ width: 390, height: 1400 })
  await expect(card.getByTestId('ladder-compact').first()).toBeVisible()
  await expect(card.getByTestId('ladder')).toBeHidden()
  await card.screenshot({ path: `${SHOTS}/awaiting-en-390.png` })
})
