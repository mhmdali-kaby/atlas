import { mkdirSync } from 'node:fs'
import path from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import arShell from '../../src/i18n/locales/ar/shell.json' with { type: 'json' }
import enShell from '../../src/i18n/locales/en/shell.json' with { type: 'json' }
import arWarroom from '../../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-39: the War Room on the real stack in the four appearance projects (en/ar × light/dark).
 * WarroomDemoSeeder gives the demo Main thread 500 messages, one every two minutes, the newest an
 * owner message at 2026-10-28T02:00:00Z = 05:00 in Baghdad; plus an empty BTC-USDT-SWAP thread and
 * an empty private Historian thread.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-39')
const T = { en: { shell: enShell, wr: enWarroom }, ar: { shell: arShell, wr: arWarroom } }
const LAST_OWNER_TEXT = 'Hold new entries until CPI at 15:30, then send me the plan.'
const AGENT_SLUGS = Object.keys(enWarroom.agents)

test.use({ viewport: { width: 1440, height: 900 } })

const feedOf = (page: Page) => page.getByTestId('wr-feed')
/** Message nodes the feed has in the DOM right now (the virtualiser keeps only the visible window). */
const messageNodes = (page: Page) => feedOf(page).locator('[data-msg-id]').count()

async function openWarRoom(page: Page) {
  await page.goto('/')
  await expect(page).toHaveURL(/\/warroom$/)
  await expect(feedOf(page).getByText(LAST_OWNER_TEXT)).toBeVisible()
}

test('"/" lands on the War Room with Main selected, the nine agents offline and the threads', async ({ page, lang }) => {
  const t = T[lang]
  await openWarRoom(page)
  const sidebar = page.getByRole('complementary', { name: t.shell.nav.label })
  await expect(sidebar.getByRole('navigation', { name: t.shell.nav.areas }).getByRole('link', { name: t.shell.area.warroom })).toHaveAttribute(
    'aria-current',
    'true',
  )

  const agents = page.getByTestId('wr-agents')
  await expect(agents.getByText(t.wr.sidebar.allOffline)).toBeVisible()
  const rows = agents.getByRole('button')
  await expect(rows).toHaveCount(9)
  for (const [i, slug] of AGENT_SLUGS.entries()) {
    const name = t.wr.agents[slug as keyof typeof t.wr.agents].name
    await expect(rows.nth(i)).toHaveAttribute('data-status', 'offline')
    await expect(rows.nth(i)).toHaveAccessibleName(t.wr.agentLabel.replace('{{name}}', name).replace('{{status}}', t.wr.status.offline))
  }

  const threads = page.getByTestId('wr-threads').getByRole('link')
  await expect(threads).toHaveCount(3)
  await expect(threads.nth(0)).toHaveAttribute('aria-current', 'page')
  await expect(threads.nth(0)).toContainText(t.wr.main)
  await expect(threads.nth(1)).toHaveAccessibleName('BTC-USDT-SWAP')
  await expect(threads.nth(2)).toContainText(t.wr.agents.historian.name)
  await expect(page.getByTestId('wr-inspector').getByRole('heading', { level: 2 })).toHaveText(t.wr.main)
})

test('500 messages keep under 60 message nodes in the DOM, and the top loads the next older page', async ({ page, lang }) => {
  test.skip(lang === 'ar', 'virtualisation does not depend on the language; the dark project covers the second run')
  await openWarRoom(page)
  expect(await messageNodes(page)).toBeLessThan(60)
  const firstBefore = await feedOf(page).locator('[data-msg-id]').first().getAttribute('data-msg-id')

  // Reaching the top asks for the next page with the cursor, and its messages join the feed.
  const older = page.waitForResponse((r) => r.url().includes('/api/warroom/messages') && r.url().includes('cursor=') && r.ok())
  await feedOf(page).evaluate((el) => el.scrollTo({ top: 0 }))
  const body = (await (await older).json()) as { data: Array<{ id: string }> }
  expect(body.data).toHaveLength(50)
  const oldestOfPage = body.data.at(-1)!.id
  await expect.poll(() => feedOf(page).locator('[data-msg-id]').first().getAttribute('data-msg-id')).not.toBe(firstBefore)
  // The reader keeps their place: the new page sits above, and scrolling up reaches it.
  await feedOf(page).evaluate((el) => el.scrollTo({ top: 0 }))
  await expect(feedOf(page).locator(`[data-msg-id="${oldestOfPage}"]`)).toBeAttached()
  expect(await messageNodes(page)).toBeLessThan(60)
})

test('an owner message sits at the inline end with its time in Baghdad', async ({ page, lang }, info) => {
  await openWarRoom(page)
  const message = feedOf(page).locator('[data-shape="owner"]', { hasText: LAST_OWNER_TEXT })
  const bubble = message.locator('.bubble')
  await expect(bubble).toBeVisible()
  // 2026-10-28T02:00:00Z is 05:00 in Asia/Baghdad, in both languages.
  await expect(message.locator('time')).toHaveText('05:00')

  // The bubble hugs the column's inline end: its right edge in English, its left edge in Arabic,
  // with the free space on the other side.
  const [box, row] = [await bubble.boundingBox(), await message.boundingBox()]
  const rightGap = row!.x + row!.width - (box!.x + box!.width)
  const leftGap = box!.x - row!.x
  if (lang === 'en') {
    expect(rightGap, 'English: flush with the right edge').toBeLessThan(16)
    expect(leftGap, 'English: free space on the left').toBeGreaterThan(40)
  } else {
    expect(leftGap, 'Arabic: flush with the left edge').toBeLessThan(16)
    expect(rightGap, 'Arabic: free space on the right').toBeGreaterThan(40)
  }

  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-owner-message.png` })
})

test('no colour-contrast failures in the War Room (axe)', async ({ page }) => {
  await openWarRoom(page)
  await page.waitForTimeout(400)
  const results = await new AxeBuilder({ page })
    .include('.shell-app')
    .withRules(['color-contrast'])
    .analyze()
  const failures = results.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(' ')}: ${n.failureSummary ?? ''}`))
  expect(failures).toEqual([])
})

test('a thread id that is not in the list shows "not here" with a way back to Main', async ({ page, lang }, info) => {
  await page.goto('/warroom/threads/00000000-0000-0000-0000-000000000000')
  const notFound = page.getByTestId('wr-feed-not-found')
  await expect(notFound).toBeVisible()
  await expect(notFound.getByRole('heading')).toHaveText(T[lang].wr.notFound.title)
  await expect(page.getByTestId('wr-feed-loading')).toHaveCount(0)
  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-unknown-thread.png` })

  const contrast = await new AxeBuilder({ page }).include('.shell-app').withRules(['color-contrast']).analyze()
  expect(contrast.violations.flatMap((v) => v.nodes.map((n) => n.target.join(' ')))).toEqual([])

  await notFound.getByRole('link', { name: T[lang].wr.notFound.action }).click()
  await expect(page).toHaveURL(/\/warroom$/)
  await expect(feedOf(page).getByText(LAST_OWNER_TEXT)).toBeVisible()
})
