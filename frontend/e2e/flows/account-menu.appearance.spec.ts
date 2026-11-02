import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from './appearance'

/**
 * ATL-223: at phone width, the shell's account-menu button (toolbar avatar, owner's initials)
 * used to carry aria-label "Account"/"الحساب" alone, which does not contain its visible content
 * (the initials) — axe's label-content-name-mismatch flagged it on every page. The fix makes the
 * accessible name lead with the same initials ("MA, Account" / "MA، الحساب"); this checks the rule
 * clears on three representative pages, in both languages (the appearance projects also cover
 * both themes), on the real stack (ATL-25).
 */
const PAGES = ['/warroom', '/portfolio', '/settings/connections'] as const

async function settle(page: Page, url: string) {
  await page.goto(url)
  await expect(page.locator('.shell-page')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(350)
}

for (const url of PAGES) {
  test(`${url} at 390px: axe reports no label-content-name-mismatch`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await settle(page, url)
    // ATL-224: at 390 px the account moves into More (⋯), the toolbar's last visible control; the
    // account button's initials-first name is unit-tested in Prefs.test.tsx and checked at 1280 below.
    await expect(page.getByTestId('toolbar-more')).toBeVisible()

    // Scoped to the shell's toolbar: a page's own content (e.g. the War Room's reply-quote link,
    // ATL-223 round 2 note) is a different task's finding, not this one's.
    const results = await new AxeBuilder({ page }).include('header.shell-toolbar').withRules(['label-content-name-mismatch']).analyze()
    expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
  })
}

test('the full toolbar at 1280 px names the account button with its initials first', async ({ page }) => {
  await settle(page, '/portfolio')
  const button = page.locator('header.shell-toolbar button').last()
  await expect(button).toBeVisible()
  await expect(button).toHaveAccessibleName(/^[A-Z]{1,2}/)
})
