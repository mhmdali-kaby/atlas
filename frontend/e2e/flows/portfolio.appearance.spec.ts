import type { Page } from '@playwright/test'
import arShell from '../../src/i18n/locales/ar/shell.json' with { type: 'json' }
import enShell from '../../src/i18n/locales/en/shell.json' with { type: 'json' }
import { SHOTS } from '../support/stack'
import { expect, test } from './appearance'

/**
 * The Portfolio pages in the four appearance projects (ltr-light, ltr-dark, rtl-light, rtl-dark)
 * on the real stack (ATL-25). Language and theme are seeded before first paint by ./appearance.
 */
const SHELL = { en: enShell, ar: arShell }
const TILES = ['net-deposits', 'total-pnl', 'today-pnl', 'unrealised', 'fees', 'funding']
const PAGES = [
  { path: '/portfolio', name: 'overview', ready: 'hero-equity' },
  { path: '/portfolio/positions', name: 'positions', ready: 'positions-summary' },
  { path: '/portfolio/holdings', name: 'holdings', ready: 'holdings-summary' },
  // PortfolioDemoSeeder has no deposits or withdrawals, so Cash flows shows its empty state.
  { path: '/portfolio/cashflows', name: 'cashflows', ready: null },
] as const

/**
 * Elements under <main> with a non-zero left margin and a zero right margin under RTL that come from
 * a physical `ml-*`, which should have been `ms-*`. A logical `me-*` also resolves to the left in
 * RTL, so each candidate is told apart by flipping its own direction to LTR: a logical margin moves
 * to the right, a physical one stays on the left.
 */
async function physicalLeftMargins(page: Page): Promise<string[]> {
  return page.locator('main *').evaluateAll((nodes) =>
    nodes
      .filter((n) => {
        const s = getComputedStyle(n)
        if (s.direction !== 'rtl' || s.marginLeft === '0px' || s.marginRight !== '0px') return false
        const el = n as HTMLElement
        const before = el.style.direction
        el.style.direction = 'ltr'
        const stays = getComputedStyle(el).marginLeft !== '0px'
        el.style.direction = before
        return stays
      })
      .map((n) => `<${n.tagName.toLowerCase()} class="${n.getAttribute('class') ?? ''}"> margin-left ${getComputedStyle(n).marginLeft}`),
  )
}

async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector('main')
    return {
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      main: main ? main.scrollWidth - main.clientWidth : 0,
    }
  })
}

test('Portfolio Overview: direction, theme, risk footer and tiles', async ({ page, lang, theme }, info) => {
  await page.goto('/portfolio')
  const html = page.locator('html')
  await expect(html).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
  await expect(html).toHaveAttribute('lang', lang)
  if (theme === 'dark') await expect(html).toHaveClass(/(^|\s)dark(\s|$)/)
  else await expect(html).not.toHaveClass(/(^|\s)dark(\s|$)/)

  const footer = page.getByRole('contentinfo')
  await expect(footer).toContainText(SHELL[lang].footer.risk)
  await expect(footer).toContainText(SHELL[lang].env.demo)

  await expect(page.getByTestId('hero-equity')).toBeVisible()
  for (const key of TILES) {
    const tile = page.getByTestId(`tile-${key}`)
    await expect(tile, `tile ${key}`).toBeVisible()
    await expect(tile, `tile ${key} has a figure`).toHaveText(/\d/)
  }
  // Tiles read from the reading start: Net deposits is left of Funding in LTR, right of it in RTL.
  const [first, last] = [await page.getByTestId('tile-net-deposits').boundingBox(), await page.getByTestId('tile-funding').boundingBox()]
  expect(lang === 'ar' ? first!.x > last!.x : first!.x < last!.x).toBe(true)

  await page.waitForTimeout(500) // the chart and tiles settle before the picture
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-overview.png`, fullPage: true })
})

for (const p of PAGES) {
  test(`Portfolio ${p.name}: no horizontal overflow and no physical left margins under RTL`, async ({ page, lang }, info) => {
    await page.goto(p.path)
    await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')
    if (p.ready) await expect(page.getByTestId(p.ready)).toBeVisible()
    await expect(page.locator('main')).toBeVisible()
    await expect(page.locator('[data-testid$="-loading"]')).toHaveCount(0)

    const overflow = await horizontalOverflow(page)
    expect(overflow.document, 'page scrolls sideways (px)').toBeLessThanOrEqual(1)
    expect(overflow.main, 'main scrolls sideways (px)').toBeLessThanOrEqual(1)

    if (lang === 'ar') expect(await physicalLeftMargins(page), 'elements with a physical left margin under RTL').toEqual([])

    if (p.name !== 'overview') await page.screenshot({ path: `${SHOTS}/${info.project.name}-${p.name}.png`, fullPage: true })
  })
}
