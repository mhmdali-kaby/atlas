import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import arAutonomyNote from '../src/i18n/locales/ar/autonomy-note.json' with { type: 'json' }
import enAutonomyNote from '../src/i18n/locales/en/autonomy-note.json' with { type: 'json' }
import { riskCheckMessage } from '../src/features/warroom/fixtures/debate'
import { autonomyCheck, PROPOSAL_ID, proposalMessage, sentDetail } from '../src/features/warroom/fixtures/proposal'
import type { ProposalDetail } from '../src/features/warroom/cards/proposal'

/**
 * ATL-219 (decision 254, design v18 §4.6): the War Room says who pressed send. The risk check
 * card's Autonomy step and the proposal card both show the AutonomyNote, built from the risk
 * check's own stored level-3 "sent" values — $2,997.79 within the $3,000.00 ask-above amount — never
 * from a hard-coded figure. A fake Laravel serves one risk_check message and one sent-by-autonomy
 * proposal in the Main thread.
 */
const SHOTS = '../../evidence/ATL-219'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:33:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }

function autonomyDetail(): ProposalDetail {
  const detail = sentDetail()
  detail.decided_by = 'autonomy:3'
  detail.risk_check = { ...detail.risk_check!, decision: 'send_now', checks: [...detail.risk_check!.checks!, autonomyCheck(9, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '2997.79', ask_above_usd: '3000' })] }
  return detail
}

function messages(lang: 'en' | 'ar') {
  const risk = riskCheckMessage('approved', { autonomy_level: 3, max_enabled_level: 4, notional_usd: '2997.79', ask_above_usd: '3000', decision: 'send_now' })
  const prop = proposalMessage()
  if (lang === 'ar') {
    risk.content_md = '**معتمد.**'
    prop.content_md = '**شراء SOL-USDT-SWAP** النسخة 2.'
  }
  return [risk, prop]
}

async function fakeApi(page: Page, lang: 'en' | 'ar') {
  const detail = autonomyDetail()
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
    if (p === '/api/warroom/messages') return json(200, { data: url.searchParams.get('thread_id') === MAIN.id ? messages(lang) : [], meta: { next_cursor: null } })
    if (p === `/api/proposals/${PROPOSAL_ID}` && req.method() === 'GET') return json(200, { data: detail })
    if (p === `/api/proposals/${PROPOSAL_ID}/mark` && req.method() === 'GET') return json(200, { data: { inst_id: detail.inst_id, mark: null, mark_at: null } })
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark', width = 1280) {
  await page.setViewportSize({ width, height: 1400 })
  await page.clock.setFixedTime(new Date('2026-10-28T11:33:00Z'))
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
  await fakeApi(page, lang)
  await page.goto('/warroom')
  const risk = page.getByTestId('db-risk')
  const proposal = page.getByTestId('pr-card')
  await expect(risk).toBeVisible()
  await expect(proposal).toBeVisible()
  return { risk, proposal, riskReason: risk.getByTestId('db-rk-autonomy-reason'), note: proposal.getByTestId('au-note') }
}

async function axeViolations(page: Page) {
  const results = await new AxeBuilder({ page }).include('[data-testid="db-risk"]').include('[data-testid="pr-card"]').analyze()
  return results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
}

for (const lang of ['en', 'ar'] as const) {
  test(`${lang}: the autonomy line shows on both the risk check card and the proposal card`, async ({ page }) => {
    const c = await open(page, lang, 'light')
    const T = lang === 'en' ? enAutonomyNote : arAutonomyNote
    await expect(c.riskReason).toContainText('2,997.79')
    await expect(c.riskReason).toContainText('3,000.00')
    await expect(c.riskReason).toContainText(lang === 'en' ? 'is within your' : 'ضمن مبلغ')
    await expect(c.note).toContainText('2,997.79')
    await expect(c.note).toContainText('3,000.00')
    await expect(c.note).toContainText(T.lead.send_now.replace('{{n}}', '3'))
    await expect(c.proposal.getByTestId('pr-status')).toContainText(lang === 'en' ? 'Sent without asking' : 'أُرسل دون سؤالك')
    await c.risk.screenshot({ path: `${SHOTS}/risk-${lang}.png` })
    await c.proposal.screenshot({ path: `${SHOTS}/proposal-${lang}.png` })
  })
}

test('Arabic dark, 390 px: no horizontal scroll, and axe reports no violations', async ({ page }) => {
  const c = await open(page, 'ar', 'dark', 390)
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expect(c.riskReason).toContainText('2,997.79')
  await expect(c.note).toContainText('2,997.79')
  for (const loc of [c.risk, c.proposal]) {
    const over = await loc.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(over).toBeLessThanOrEqual(1)
  }
  await page.waitForTimeout(300)
  expect(await axeViolations(page)).toEqual([])
  await c.risk.screenshot({ path: `${SHOTS}/risk-ar-dark-390.png` })
  await c.proposal.screenshot({ path: `${SHOTS}/proposal-ar-dark-390.png` })
})
