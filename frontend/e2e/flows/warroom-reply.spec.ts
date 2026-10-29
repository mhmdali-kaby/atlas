import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import arWarroom from '../../src/i18n/locales/ar/warroom.json' with { type: 'json' }
import enWarroom from '../../src/i18n/locales/en/warroom.json' with { type: 'json' }
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-42: Reply from the feed. WarroomDemoSeeder's demo Main thread ends with an owner message
 * ("Hold new entries until CPI at 15:30…"), which is the target here — it is on screen without
 * scrolling and, being the owner's own bubble, also exercises MessageActions on `is-owner`.
 *
 * This posts into Main, so (unlike the `.appearance.spec.ts` specs, which only read it) it is not
 * matched by that glob: it runs in its own `warroom-reply-*` projects in playwright.config.ts,
 * after the read-only appearance projects and before warroom.live.spec.ts, which also posts into
 * Main (see the dependency comment there).
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-42')
const T = { en: enWarroom, ar: arWarroom }
const LAST_OWNER_TEXT = 'Hold new entries until CPI at 15:30, then send me the plan.'

const feedOf = (page: Page) => page.getByTestId('wr-feed')
const input = (page: Page) => page.getByTestId('wr-composer-input')
// Matches the original message by its own bubble text, not a later reply that quotes it (the
// quote is a sibling of `.bubble`, so a reply to it never matches this filter).
const targetMessage = (page: Page) =>
  feedOf(page)
    .locator('[data-shape="owner"]')
    .filter({ has: page.locator('.bubble', { hasText: LAST_OWNER_TEXT }) })

async function openWarRoom(page: Page) {
  await page.goto('/')
  await expect(page).toHaveURL(/\/warroom$/)
  await expect(targetMessage(page)).toBeVisible()
}

async function postedReplyTo(page: Page, text: string): Promise<string | null> {
  const posted = page.waitForResponse((r) => r.url().endsWith('/api/warroom/messages') && r.request().method() === 'POST')
  await input(page).fill(text)
  await input(page).press('Enter')
  const body = (await (await posted).json()) as { data: { reply_to: string | null } }
  return body.data.reply_to
}

type Box = { x: number; y: number; width: number; height: number }
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

test('choosing Reply shows the quoted chip; sending "agreed" posts reply_to and renders with the quoted parent', async ({ page, lang, theme }) => {
  test.skip(theme === 'dark', 'the reply flow does not depend on theme; the light project covers it in both languages')
  const t = T[lang]
  await openWarRoom(page)

  const target = targetMessage(page)
  await target.hover()
  await target.getByTestId('wr-action-reply').click()

  const chip = page.getByTestId('wr-reply-chip')
  await expect(chip).toBeVisible()
  await expect(chip).toContainText(t.you)
  await expect(chip).toContainText('Hold new entries until CPI')
  await expect(input(page)).toBeFocused()

  const text = `agreed ${Date.now()}`
  const replyTo = await postedReplyTo(page, text)
  expect(replyTo).not.toBeNull()
  await expect(chip).toHaveCount(0)

  const mine = feedOf(page).locator('[data-shape="owner"]', { hasText: text })
  await expect(mine.locator('.msg-quote')).toContainText('Hold new entries until CPI')
})

test('Esc clears the reply; the next message posts with no reply_to', async ({ page, theme }) => {
  test.skip(theme === 'dark', 'the reply flow does not depend on theme; the light project covers it in both languages')
  await openWarRoom(page)

  const target = targetMessage(page)
  await target.hover()
  await target.getByTestId('wr-action-reply').click()
  await expect(page.getByTestId('wr-reply-chip')).toBeVisible()

  await input(page).press('Escape')
  await expect(page.getByTestId('wr-reply-chip')).toHaveCount(0)

  const replyTo = await postedReplyTo(page, `no reply here ${Date.now()}`)
  expect(replyTo).toBeNull()
})

test("the chip's × clears the reply; the next message posts with no reply_to", async ({ page, theme }) => {
  test.skip(theme === 'dark', 'the reply flow does not depend on theme; the light project covers it in both languages')
  await openWarRoom(page)

  const target = targetMessage(page)
  await target.hover()
  await target.getByTestId('wr-action-reply').click()
  await expect(page.getByTestId('wr-reply-chip')).toBeVisible()

  await page.getByTestId('wr-reply-cancel').click()
  await expect(page.getByTestId('wr-reply-chip')).toHaveCount(0)

  const replyTo = await postedReplyTo(page, `still no reply ${Date.now()}`)
  expect(replyTo).toBeNull()
})

test('Reply is reachable by keyboard: focusing the message and its actions shows a visible focus ring', async ({ page, lang, theme }) => {
  test.skip(lang !== 'en' || theme !== 'light', 'keyboard reachability does not depend on language or theme')
  await openWarRoom(page)

  const target = targetMessage(page)
  await target.focus()
  await expect(target).toBeFocused()
  const ring = await target.evaluate((el) => getComputedStyle(el).boxShadow)
  expect(ring).not.toBe('none')

  const replyButton = page.getByTestId('wr-action-reply')
  await expect(replyButton).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(replyButton).toBeFocused()
  const buttonRing = await replyButton.evaluate((el) => getComputedStyle(el).boxShadow)
  expect(buttonRing).not.toBe('none')

  await page.keyboard.press('Enter')
  await expect(page.getByTestId('wr-reply-chip')).toBeVisible()
  await expect(input(page)).toBeFocused()
})

test('replying to an older message while scrolled up: the jump-to-latest float clears the chip’s ×', async ({ page, theme }, info) => {
  test.skip(theme === 'dark', 'the overlap is a layout bug, independent of theme; the light project covers both languages')
  await openWarRoom(page)

  // QA repro: scroll the feed well up into the seeded history, past "Check 480" (Historian),
  // then Reply from there. .wr-float (the jump-to-latest button) must grow with the composer,
  // which the ReplyChip makes taller, instead of landing on the chip's × at a fixed offset.
  await feedOf(page).evaluate((el) => el.scrollBy({ top: -1500 }))
  const older = feedOf(page).locator('[data-msg-id]').filter({ hasText: 'Check 480:' })
  await expect(older).toBeVisible()
  const latest = page.getByTestId('wr-latest')
  await expect(latest).toBeVisible()

  await older.hover()
  await older.getByTestId('wr-action-reply').click()
  const cancel = page.getByTestId('wr-reply-cancel')
  await expect(cancel).toBeVisible()

  // .wr-float's `bottom` transitions to its new, taller-composer offset (150ms); poll past that
  // so this checks the settled position, not a mid-animation frame.
  await expect
    .poll(async () => {
      const [cancelBox, latestBox] = [await cancel.boundingBox(), await latest.boundingBox()]
      return cancelBox && latestBox ? overlaps(cancelBox, latestBox) : null
    })
    .toBe(false)

  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-chip-scrolled-up.png` })

  await cancel.click()
})

test('Arabic: the chip and the quote mirror correctly', async ({ page, lang }, info) => {
  test.skip(lang !== 'ar', 'this is the Arabic mirroring check')
  await openWarRoom(page)
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')

  const target = targetMessage(page)
  await target.hover()
  await target.getByTestId('wr-action-reply').click()

  const chip = page.getByTestId('wr-reply-chip')
  await expect(chip).toBeVisible()
  // The chip's icon, title and × sit in reading order; the whole row mirrors to the left-reading
  // Arabic layout, so the × (cancel) ends up at the row's inline end, visually on the left.
  const [chipBox, cancelBox] = [await chip.boundingBox(), await page.getByTestId('wr-reply-cancel').boundingBox()]
  expect(cancelBox!.x).toBeLessThan(chipBox!.x + chipBox!.width / 2)

  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-reply-chip.png` })

  const text = `موافق ${Date.now()}`
  await postedReplyTo(page, text)
  const mine = feedOf(page).locator('[data-shape="owner"]', { hasText: text })
  const quote = mine.locator('.msg-quote')
  await expect(quote).toBeVisible()
  // The quote's corner-up-left icon mirrors in RTL (design v4 War Room §9): its box sits at the
  // quote's inline start, which is the right edge in Arabic.
  const [quoteBox, iconBox] = [await quote.boundingBox(), await quote.locator('svg').boundingBox()]
  expect(iconBox!.x).toBeGreaterThan(quoteBox!.x + quoteBox!.width / 2)

  await page.waitForTimeout(300)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-reply-quote.png` })
})
