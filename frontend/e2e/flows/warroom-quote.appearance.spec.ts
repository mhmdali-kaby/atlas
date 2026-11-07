import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from './appearance'

/**
 * ATL-223 round 2: the War Room reply-quote link (`.msg-quote` in Message.tsx and LiveRunRow.tsx)
 * carried aria-label "Show the message {{name}} wrote" with no overlap with its visible content
 * (the quoted author and excerpt) — axe's label-content-name-mismatch flagged it wherever a reply
 * is on screen. The fix drops the aria-label and appends a visually-hidden hint after the visible
 * author and excerpt, so the accessible name begins with the same text that is on screen.
 *
 * WarroomDemoSeeder's Main thread ends with a Historian reply (second-to-last message) to an
 * earlier owner question ("@Historian what usually happens after CPI?"), visible without
 * scrolling. This checks the rule clears on /warroom with that reply on screen, at phone and
 * desktop width, in both languages (ATL-25), and that clicking the quote still jumps to (and
 * flashes) the original message, unscoped (the account-menu spec's run stays scoped to the
 * toolbar; this one covers the page body).
 *
 * ATL-225 fixed the sidebar's AgentList rows and the toolbar's language switch, the two
 * pre-existing mismatches this spec used to exclude, so the scan now runs with no
 * include/exclude at all.
 */
const QUESTION_TEXT = 'what usually happens after CPI'

async function openWarRoom(page: Page) {
  await page.goto('/')
  await expect(page).toHaveURL(/\/warroom$/)
  const quote = page.locator('.msg-quote', { hasText: QUESTION_TEXT })
  await expect(quote).toBeVisible()
  await page.waitForTimeout(300)
  return quote
}

const SIZES = [
  ['390px', { width: 390, height: 844 }],
  ['desktop', { width: 1280, height: 900 }],
] as const

for (const [label, size] of SIZES) {
  test(`${label}: axe reports no label-content-name-mismatch on /warroom with a reply`, async ({ page, lang }) => {
    await page.setViewportSize(size)
    const quote = await openWarRoom(page)
    const leads = lang === 'ar' ? 'أنت' : 'You'
    await expect(quote).toHaveAccessibleName(new RegExp(`^${leads}`))

    const results = await new AxeBuilder({ page }).withRules(['label-content-name-mismatch']).analyze()
    expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
  })
}

test('clicking the quote jumps to and flashes the original message', async ({ page, lang }) => {
  test.skip(lang === 'ar', 'the jump behaviour does not depend on language; the English run covers it')
  const quote = await openWarRoom(page)
  await quote.click()

  // The quoted parent is the owner's own question, so it is a `.bubble`; a reply that merely
  // quotes it (like the message the clicked `.msg-quote` lives in) is a sibling of `.bubble`,
  // never matched by this filter (warroom-reply.spec.ts uses the same technique).
  const target = page.locator('[data-shape="owner"]').filter({ has: page.locator('.bubble', { hasText: QUESTION_TEXT }) })
  await expect(target).toHaveClass(/is-flash/)
})
