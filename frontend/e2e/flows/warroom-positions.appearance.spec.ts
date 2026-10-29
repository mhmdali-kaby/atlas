import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-43: the War Room inspector's Open positions section, against the real stack in the four
 * appearance projects (en/ar × light/dark). PortfolioDemoSeeder gives the demo account exactly two
 * open positions (a BTC-USDT-SWAP long, an XAU-USDT-SWAP short); the inspector shares
 * GET /api/portfolio/positions with Portfolio → Positions, so every figure must agree.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-43')

test.use({ viewport: { width: 1440, height: 900 } })

const figOf = (row: Locator) => row.locator('[data-sign] .fig').first()

/** Reads side, size, entry, mark and unrealised PnL off Portfolio → Positions, by instrument. */
async function readPortfolioPositions(page: Page) {
  await page.goto('/portfolio/positions')
  const rows = page.getByTestId('position-row')
  await expect(rows).toHaveCount(2)
  const byInst: Record<string, { side: string; size: string; entry: string; mark: string; upl: string }> = {}
  for (const row of await rows.all()) {
    const inst = (await row.getAttribute('data-inst'))!
    byInst[inst] = {
      side: (await row.locator('[data-col="side"]').innerText()).trim(),
      size: (await row.locator('[data-col="size"] .fig').innerText()).trim(),
      entry: (await row.locator('[data-col="entry"]').innerText()).trim(),
      mark: (await row.locator('[data-col="mark"]').innerText()).trim(),
      upl: (await figOf(row.locator('[data-col="upl"]')).innerText()).trim(),
    }
  }
  return byInst
}

test('the inspector lists the two seeded positions matching Portfolio → Positions', async ({ page }) => {
  const expected = await readPortfolioPositions(page)
  expect(Object.keys(expected).sort()).toEqual(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])

  await page.goto('/warroom')
  const posRows = page.getByTestId('wr-position-row')
  await expect(posRows).toHaveCount(2)
  for (const row of await posRows.all()) {
    const inst = (await row.getAttribute('data-inst'))!
    const want = expected[inst]!
    await expect(row).toContainText(want.side)
    await expect(row).toContainText(want.entry)
    await expect(row).toContainText(want.mark)
    await expect(figOf(row)).toHaveText(want.upl)
    // The size appears inside the "{side} {lever}×, {n} contracts" sentence.
    await expect(row).toContainText(want.size)
  }
})

test('profit/loss carries a sign and a colour together, and screenshots the section', async ({ page }, info) => {
  await page.goto('/warroom')
  const section = page.getByTestId('wr-positions')
  const rows = page.getByTestId('wr-position-row')
  await expect(rows).toHaveCount(2)

  for (const row of await rows.all()) {
    const signed = row.locator('[data-sign]').first()
    const sign = await signed.getAttribute('data-sign')
    expect(['profit', 'loss', 'zero']).toContain(sign)
    if (sign === 'profit') await expect(signed).toHaveClass(/text-profit-ink/)
    if (sign === 'loss') await expect(signed).toHaveClass(/text-loss-ink/)
    // The sign is never colour alone: the figure text itself carries +/− (design §9, §11).
    await expect(figOf(row)).toHaveText(/^[+−]/)
  }

  mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(300)
  await section.screenshot({ path: `${SHOTS}/${info.project.name}-positions.png` })
})

test('clicking a position row opens Portfolio → Positions', async ({ page }) => {
  await page.goto('/warroom')
  const row = page.getByTestId('wr-position-row').first()
  await row.click()
  await expect(page).toHaveURL(/\/portfolio\/positions$/)
  await expect(page.getByTestId('positions-table')).toBeVisible()
})

test('the selected instrument thread’s own position is listed first, highlighted', async ({ page }) => {
  await page.goto('/warroom')
  // WarroomDemoSeeder's real (database-assigned) thread id, reached by clicking the sidebar row.
  const btcThread = page.getByTestId('wr-threads').getByRole('link', { name: 'BTC-USDT-SWAP' })
  await btcThread.click()
  await expect(btcThread).toHaveAttribute('aria-current', 'page')
  const rows = page.getByTestId('wr-position-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.first()).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
  await expect(rows.first()).toHaveClass(/is-here/)
})
