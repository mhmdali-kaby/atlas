import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-224 (design v19, compact toolbar): once the shell's toolbar is narrower than 1000 px of its own
 * width, it keeps the title, the approval pill, Stop, the bell and More (⋯); the page's actions
 * (War Room Replay and inspector), language, appearance and the account move into More. Runs in
 * the four appearance projects (English/Arabic × light/dark) on the real stack (ATL-25).
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-224')
const PAGES = ['/warroom', '/portfolio', '/settings/connections'] as const
type Box = { x: number; y: number; width: number; height: number }

async function settle(page: Page, url: string) {
  await page.goto(url)
  await expect(page.locator('.shell-page')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(350)
}

async function boxOf(locator: Locator): Promise<Box> {
  await expect(locator).toBeVisible()
  const box = await locator.boundingBox()
  expect(box).not.toBeNull()
  return box!
}

function intersects(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

function expectInside(box: Box, width: number, height: number, what: string) {
  expect(box.x, `${what} starts inside the viewport`).toBeGreaterThanOrEqual(0)
  expect(box.y, `${what} starts inside the viewport`).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width, `${what} ends inside the viewport`).toBeLessThanOrEqual(width)
  expect(box.y + box.height, `${what} ends inside the viewport`).toBeLessThanOrEqual(height)
}

function expectApart(boxes: Record<string, Box>) {
  const names = Object.keys(boxes)
  for (let i = 0; i < names.length; i++)
    for (let j = i + 1; j < names.length; j++) {
      expect(intersects(boxes[names[i]!]!, boxes[names[j]!]!), `${names[i]} and ${names[j]} overlap`).toBe(false)
    }
}

const toolbar = (page: Page) => page.locator('header.shell-toolbar')
const more = (page: Page) => page.getByTestId('toolbar-more')

for (const url of PAGES) {
  test(`${url} at 390 px: Stop, the bell and More fit; More holds Sign out`, async ({ page, lang, theme }) => {
    const vp = { width: 390, height: 844 }
    await page.setViewportSize(vp)
    await settle(page, url)

    const boxes = {
      stop: await boxOf(page.getByTestId('kill-switch')),
      bell: await boxOf(page.getByTestId('nt-bell')),
      more: await boxOf(more(page)),
    }
    for (const [name, box] of Object.entries(boxes)) expectInside(box, vp.width, vp.height, name)
    expectApart(boxes)
    // The full toolbar's controls have stepped aside into More.
    await expect(page.getByTestId('language-switch')).toBeHidden()
    await expect(page.getByTestId('wr-replay-open')).toBeHidden()
    await expect(page.getByTestId('toolbar-env')).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/390-${url.split('/')[1]}-${lang}-${theme}.png` })

    await more(page).click()
    const menu = page.getByRole('menu')
    await expect(menu).toBeVisible()
    expectInside(await boxOf(menu.getByTestId('sign-out')), vp.width, vp.height, 'Sign out')
    if (url === '/warroom') {
      const replayName = (await page.getByTestId('wr-replay-open').getAttribute('data-menu-label'))!
      expectInside(await boxOf(menu.getByRole('menuitem', { name: replayName })), vp.width, vp.height, 'Replay')
    }
    await page.waitForTimeout(350) // the menu's open animation
    await page.screenshot({ path: `${SHOTS}/390-${url.split('/')[1]}-more-${lang}-${theme}.png` })
    await page.keyboard.press('Escape')
    await expect(menu).toBeHidden()

    // Stop is visible without any menu and opens the kill-switch sheet.
    await page.getByTestId('kill-switch').click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.keyboard.press('Escape')
  })
}

test('at 390 px, Replay chosen from More opens the replay view', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await settle(page, '/warroom')
  const replayName = (await page.getByTestId('wr-replay-open').getAttribute('data-menu-label'))!
  await more(page).click()
  await page.getByRole('menu').getByRole('menuitem', { name: replayName }).click()
  await expect(page).toHaveURL(/\/warroom\/replay\/\d{4}-\d{2}-\d{2}/)
})

test('a 1100 px window with the sidebar and inspector open uses the compact toolbar', async ({ page, lang, theme }) => {
  const vp = { width: 1100, height: 900 }
  await page.setViewportSize(vp)
  await settle(page, '/warroom')
  await expect(page.locator('.shell-app')).toHaveAttribute('data-sidebar', 'shown')
  const bar = await boxOf(toolbar(page))
  expect(bar.width).toBeLessThan(1000)

  // The inspector is a page action, so at this width it is opened from More.
  await more(page).click()
  const inspectorName = (await page.getByTestId('wr-inspector-toggle').getAttribute('data-menu-label'))!
  await page.getByRole('menu').getByRole('menuitemcheckbox', { name: inspectorName }).click()
  await expect(page.locator('#wr-inspector')).toBeVisible()

  await expect(page.getByTestId('wr-replay-open')).toBeHidden()
  const boxes = {
    stop: await boxOf(page.getByTestId('kill-switch')),
    bell: await boxOf(page.getByTestId('nt-bell')),
    more: await boxOf(more(page)),
    title: await boxOf(toolbar(page).locator('h1')),
  }
  for (const [name, box] of Object.entries(boxes)) expectInside(box, vp.width, vp.height, name)
  expectApart(boxes)

  await more(page).click()
  const replayName = (await page.getByTestId('wr-replay-open').getAttribute('data-menu-label'))!
  await expect(page.getByRole('menu').getByRole('menuitem', { name: replayName })).toBeVisible()
  await page.waitForTimeout(350)
  await page.screenshot({ path: `${SHOTS}/1100-warroom-more-${lang}-${theme}.png` })
})

test('at 1280 px the full toolbar is unchanged: Replay and the account are in the toolbar, no More', async ({ page, lang, theme }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await settle(page, '/warroom')
  // With the sidebar open the toolbar is 1280 − 256 = 1024 px: still the full form.
  await expect(page.locator('.shell-app')).toHaveAttribute('data-sidebar', 'shown')
  await expect(more(page)).toBeHidden()
  const boxes = {
    stop: await boxOf(page.getByTestId('kill-switch')),
    replay: await boxOf(page.getByTestId('wr-replay-open')),
    inspector: await boxOf(page.getByTestId('wr-inspector-toggle')),
    bell: await boxOf(page.getByTestId('nt-bell')),
    language: await boxOf(page.getByTestId('language-switch')),
  }
  expectApart(boxes)
  await page.screenshot({ path: `${SHOTS}/1280-warroom-${lang}-${theme}.png` })
})
