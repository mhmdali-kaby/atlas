import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from './appearance'

/**
 * ATL-225: an unscoped axe run for label-content-name-mismatch used to find two violations on
 * every page: the War Room sidebar's AgentList rows (aria-label "<name>, Offline" dropped the
 * visible role text) and the toolbar's language switch (aria-label translated "Switch to …" in
 * the current language, while the visible text is the target language's own name in its own
 * script). Both are fixed without a separate aria-label, so this checks the rule stays clear
 * with no include/exclude scope at all, on three representative pages, at phone and desktop
 * width, in both languages (the appearance projects also cover both themes).
 */
const PAGES = ['/warroom', '/portfolio', '/settings'] as const
const SIZES = [
  ['390px', { width: 390, height: 844 }],
  ['1280px', { width: 1280, height: 900 }],
] as const

async function settle(page: Page, url: string) {
  await page.goto(url)
  await expect(page.locator('.shell-page')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(350)
}

for (const url of PAGES) {
  for (const [label, size] of SIZES) {
    test(`${url} at ${label}: axe reports no label-content-name-mismatch`, async ({ page }) => {
      await page.setViewportSize(size)
      await settle(page, url)
      const results = await new AxeBuilder({ page }).withRules(['label-content-name-mismatch']).analyze()
      expect(results.violations.flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(' ')} ${n.html}`))).toEqual([])
    })
  }
}
