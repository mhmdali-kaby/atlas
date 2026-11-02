import { expect, test, type Page } from '@playwright/test'
import { PROPOSAL_ID, proposalDetail, proposalMessage } from '../src/features/warroom/fixtures/proposal'

/*
 * ATL-220: a notification link's target (design v18 "Autonomy and notifications" §5.6) against a
 * fake Laravel. `/warroom?message=<id>` and `?proposal=<id>` load the card, scroll it into view,
 * flash it once and drop the query; an unknown id shows a "couldn't find it" toast instead.
 * Screenshots go to ../../evidence/ATL-220/.
 */

const SHOTS = '../../evidence/ATL-220'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const MAIN = { id: 'thread-main', kind: 'main', key: 'main', title: 'Main', last_message_at: '2026-10-29T05:00:00.000000Z', created_at: '2026-10-01T00:00:00.000000Z' }
const PAGE_SIZE = 50

/** 55 plain-text messages, oldest first; `m-0` is the oldest, so it sits on the second page. */
function allMessages() {
  return Array.from({ length: 55 }, (_, i) => ({
    id: `m-${i}`,
    thread_id: MAIN.id,
    author_type: 'agent',
    author: 'historian',
    kind: 'text',
    content_md: `Message ${i} of the morning review.`,
    card: null,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    unmatched_numbers: null,
    created_at: new Date(Date.parse('2026-10-29T00:00:00Z') + i * 60_000).toISOString(),
  }))
}

/**
 * `thread`: the Main thread's own messages, newest first, as the real cursor API would return
 * page by page (an opaque numeric-offset cursor stands in for Laravel's real one here).
 */
async function fakeApi(page: Page, thread: unknown[]) {
  const all = allMessages()
  const newestFirst = [...thread]
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    const p = url.pathname
    if (p === '/sanctum/csrf-cookie') return json(204)
    if (p === '/api/auth/status') return json(200, { owner_exists: true })
    if (p === '/api/auth/me') return json(200, { data: OWNER })
    if (p === '/api/warroom/threads') return json(200, { data: [MAIN] })
    if (p === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date().toISOString(), items: [] } })
    const singleMessage = /^\/api\/warroom\/messages\/([^/]+)$/.exec(p)
    if (singleMessage) {
      const found = [...all, ...thread].find((m): m is { id: string } => typeof m === 'object' && m !== null && 'id' in m && (m as { id: string }).id === singleMessage[1])
      return found ? json(200, { data: found }) : json(404, { message: 'Not found' })
    }
    if (p === '/api/warroom/messages') {
      if (url.searchParams.get('thread_id') !== MAIN.id) return json(200, { data: [], meta: { next_cursor: null } })
      const cursor = url.searchParams.get('cursor')
      const limit = Number(url.searchParams.get('limit') ?? String(PAGE_SIZE))
      const offset = cursor ? Number(cursor) : 0
      const slice = newestFirst.slice(offset, offset + limit)
      const nextOffset = offset + limit
      return json(200, { data: slice, meta: { next_cursor: nextOffset < newestFirst.length ? String(nextOffset) : null } })
    }
    if (p === `/api/proposals/${PROPOSAL_ID}` && req.method() === 'GET') return json(200, { data: proposalDetail() })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, path: string, thread: unknown[], lang: 'en' | 'ar' = 'en', width = 1280) {
  await page.addInitScript(([l]) => localStorage.setItem('atlas.lang', l), [lang] as const)
  await page.setViewportSize({ width, height: 900 })
  await fakeApi(page, thread)
  await page.goto(path)
}

test('?message=<id> older than the first page loads it, scrolls it into view and flashes it once', async ({ page }) => {
  const thread = [...allMessages()].reverse() // newest first, as the API returns a page
  await open(page, '/warroom?message=m-0', thread)
  const target = page.locator('[data-msg-id="m-0"]')
  await expect(target).toBeVisible({ timeout: 10_000 })
  await expect(target).toHaveClass(/is-flash/)
  await expect(target).toBeInViewport()
  await expect(page).toHaveURL('/warroom')
  await page.screenshot({ path: `${SHOTS}/message-older-page.png` })
})

test('?proposal=<id> lands on that proposal’s card', async ({ page }) => {
  const thread = [proposalMessage()]
  await open(page, `/warroom?proposal=${PROPOSAL_ID}`, thread)
  const card = page.getByTestId('pr-card')
  await expect(card).toBeVisible()
  const msg = page.locator('[data-msg-id="m-prop-412"]')
  await expect(msg).toHaveClass(/is-flash/)
  await expect(card).toBeInViewport()
  await expect(page).toHaveURL('/warroom')
})

test('an unknown message id shows the not-found toast with no error; Arabic at 390px has no horizontal scroll', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  const thread = [...allMessages()].reverse().slice(0, 10)
  await open(page, '/warroom?message=does-not-exist', thread, 'ar', 390)
  await expect(page.locator('[data-toast="info"]')).toContainText('تعذّر العثور على هذه الرسالة')
  await expect(page).toHaveURL('/warroom')
  const [scrollWidth, clientWidth] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(scrollWidth).toBeLessThanOrEqual(clientWidth)
  expect(errors).toEqual([])
  await page.screenshot({ path: `${SHOTS}/message-not-found-ar-390.png` })
})
