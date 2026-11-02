import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { autonomyCheck, PM_PROPOSAL_ID, pmActionDetail, pmActionMessage, type PmActionKind } from '../src/features/warroom/fixtures/proposal'
import type { ProposalDetail } from '../src/features/warroom/cards/proposal'

/**
 * ATL-219 criterion 5 (decision 254, design v18 §4.6 "the Portfolio Manager's action card"): a
 * stop change or a close shows the same AutonomyNote, built from its own check 7 values. A level-3
 * stop change above the ask-above amount compares the whole position's notional and waits for the
 * owner; a level-3 close is sent because closing never waits; a level-2 stop change still shows
 * Approve and Reject. Shares fixtures/proposal.ts's pmActionMessage/pmActionDetail with
 * PmActionCard.test.tsx.
 */
const SHOTS = '../../evidence/ATL-219'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-28T11:30:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }

async function fakeApi(page: Page, kind: PmActionKind, detail: ProposalDetail) {
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
    if (p === '/api/warroom/messages') return json(200, { data: url.searchParams.get('thread_id') === MAIN.id ? [pmActionMessage(kind)] : [], meta: { next_cursor: null } })
    if (p === `/api/proposals/${PM_PROPOSAL_ID}` && req.method() === 'GET') return json(200, { data: detail })
    if (p === '/api/portfolio/positions') return json(200, { data: [] })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, kind: PmActionKind, detail: ProposalDetail, width = 1280) {
  await page.setViewportSize({ width, height: 1000 })
  await page.clock.setFixedTime(new Date('2026-10-28T11:32:00Z'))
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' })
  await fakeApi(page, kind, detail)
  await page.goto('/warroom')
  const card = page.getByTestId('pm-prop-card')
  await expect(card).toBeVisible()
  return card
}

test('level 3 stop change above the ask-above amount: it waits for the owner on the whole position’s notional', async ({ page }) => {
  const detail = pmActionDetail('amend_stop')
  detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '163848', ask_above_usd: '3000', note: 'above_ask_threshold' })]
  const card = await open(page, 'amend_stop', detail)
  const note = card.getByTestId('au-note')
  await expect(note).toContainText('Autonomy 3 asks you.')
  await expect(note).toContainText('The whole position')
  await expect(note).toContainText('163,848.00')
  await expect(note).toContainText('3,000.00')
  await expect(card.getByTestId('pr-approve')).toBeVisible()
  await expect(card.getByTestId('pr-reject')).toBeVisible()
  await card.screenshot({ path: `${SHOTS}/pm-amend-above-en.png` })
})

test('level 3 close: sent because closing never waits, whatever its size', async ({ page }) => {
  const detail = pmActionDetail('close', { status: 'sent', decided_by: 'autonomy:3', decided_at: '2026-10-28T10:46:40.000000Z' })
  detail.risk_check!.checks = [autonomyCheck(7, { autonomy_level: 3, max_enabled_level: 4, notional_usd: '262830', ask_above_usd: '3000', note: 'risk_reducing' })]
  const card = await open(page, 'close', detail)
  await expect(card.getByTestId('pr-status')).toContainText('Sent without asking')
  const note = card.getByTestId('au-note')
  await expect(note).toContainText('Autonomy 3 didn’t ask.')
  await expect(note).toContainText('Closing never waits, whatever its size.')
  await expect(card.getByTestId('pr-actions')).toHaveCount(0)
  await card.screenshot({ path: `${SHOTS}/pm-close-sent-en.png` })
})

test('level 2 stop change: Approve and Reject show, with no autonomy note; no axe violations', async ({ page }) => {
  const detail = pmActionDetail('amend_stop')
  const card = await open(page, 'amend_stop', detail, 390)
  await expect(card.getByTestId('pr-approve')).toBeVisible()
  await expect(card.getByTestId('pr-reject')).toBeVisible()
  await expect(card.getByTestId('au-note')).toHaveCount(0)
  const over = await card.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(over).toBeLessThanOrEqual(1)
  const results = await new AxeBuilder({ page }).include('[data-testid="pm-prop-card"]').analyze()
  expect(results.violations).toEqual([])
})
