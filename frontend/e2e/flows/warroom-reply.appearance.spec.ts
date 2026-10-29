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
