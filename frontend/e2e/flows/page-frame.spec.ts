import { mkdirSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-192 (design v13/v14, Phase 0 §6): every page fills the window beside the sidebar, inset by
 * --page-gutter on both sides — 32 px on desktop, 16 px on phones — with no max-width cap.
 * Runs on the real stack after the onboarding flow, in English and Arabic.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-192')
const LANGS = ['en', 'ar'] as const
const PAGES = [
  '/agents',
  '/portfolio',
  '/portfolio/positions',
  '/portfolio/orders',
  '/portfolio/holdings',
  '/portfolio/cashflows',
  '/settings/connections',
  '/settings/risk',
  '/settings/money',
  '/settings/agents',
  '/settings/data',
  '/settings/sources',
]
const LAST_OWNER_TEXT = 'Hold new entries until CPI at 15:30, then send me the plan.'

type Box = { left: number; right: number; top: number; bottom: number; width: number }

/** The content column (sidebar edge → window edge) and the union of the page's in-flow children. */
async function frame(page: Page) {
  return page.evaluate(() => {
    const box = (el: Element) => {
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }
    }
    const main = document.querySelector('.shell-main')!
    const shellPage = document.querySelector('.shell-page')!
    const kids = [...shellPage.children].filter((el) => {
      const cs = getComputedStyle(el)
      const r = el.getBoundingClientRect()
      return r.width > 0 && cs.position !== 'fixed' && cs.position !== 'absolute' && cs.display !== 'none'
    })
    return {
      column: box(main),
      left: Math.min(...kids.map((k) => k.getBoundingClientRect().left)),
      right: Math.max(...kids.map((k) => k.getBoundingClientRect().right)),
      kids: kids.length,
      docScroll: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }
  })
}

async function settle(page: Page, url: string) {
  await page.goto(url)
  await expect(page.locator('.shell-page')).toBeVisible()
  await page.waitForLoadState('networkidle')
  // the page-in animation translates by 6px vertically only; widths are final once it ends
  await page.waitForTimeout(350)
}

for (const lang of LANGS) {
  test.describe(`${lang}`, () => {
    test.use({ lang })

    for (const width of [1440, 1920]) {
      test(`every Agents, Portfolio and Settings page sits 32px from the sidebar and the window edge at ${width}`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        for (const url of PAGES) {
          await settle(page, url)
          const f = await frame(page)
          expect(f.kids, url).toBeGreaterThan(0)
          // The column runs from the sidebar to the window edge, so its two gaps are the gutters.
          if (lang === 'ar') expect(Math.round(f.column.left), `${url} column reaches the window edge`).toBe(0)
          else expect(Math.round(f.column.right), `${url} column reaches the window edge`).toBe(width)
          expect(Math.abs(f.left - f.column.left - 32), `${url} start gap ${f.left - f.column.left}`).toBeLessThanOrEqual(1)
          expect(Math.abs(f.column.right - f.right - 32), `${url} end gap ${f.column.right - f.right}`).toBeLessThanOrEqual(1)
          if (lang === 'ar') expect(Math.abs(f.left - 32), `${url} window-edge gap (RTL)`).toBeLessThanOrEqual(1)
          else expect(Math.abs(width - f.right - 32), `${url} window-edge gap`).toBeLessThanOrEqual(1)
        }
      })
    }

    test('every Agents, Portfolio and Settings page has a 16px gutter at 390 and never scrolls sideways', async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 })
      for (const url of PAGES) {
        await settle(page, url)
        const f = await frame(page)
        expect(Math.abs(f.left - 16), `${url} start gap ${f.left}`).toBeLessThanOrEqual(1)
        expect(Math.abs(390 - f.right - 16), `${url} end gap ${390 - f.right}`).toBeLessThanOrEqual(1)
        expect(f.docScroll, `${url} document.scrollWidth`).toBeLessThanOrEqual(f.viewport)
        const inner = await page.locator('.shell-content').evaluate((el) => el.scrollWidth - el.clientWidth)
        expect(inner, `${url} content scrolls sideways`).toBeLessThanOrEqual(0)
      }
    })

    for (const width of [1440, 1920]) {
      test(`the War Room feed sits 32px from the sidebar and the docked inspector at ${width}; the composer fills it`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.goto('/warroom')
        const feed = page.getByTestId('wr-feed')
        await expect(feed.getByText(LAST_OWNER_TEXT)).toBeVisible()
        await expect(page.getByTestId('wr-inspector')).toBeVisible()
        await page.waitForTimeout(350)
        const m = await page.evaluate(() => {
          const box = (el: Element | null): Box | null => {
            if (!el) return null
            const r = el.getBoundingClientRect()
            return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width }
          }
          const visible = (sel: string) =>
            [...document.querySelectorAll(sel)].filter((el) => {
              const r = el.getBoundingClientRect()
              return r.width > 0 && r.bottom > 0 && r.top < window.innerHeight
            })
          return {
            column: box(document.querySelector('.shell-main'))!,
            inspector: box(document.querySelector('[data-testid="wr-inspector"]'))!,
            composer: box(document.querySelector('.composer'))!,
            avatars: visible('.msg-gutter .medal').map(box) as Box[],
            texts: visible('.msg:not(.is-owner) .msg-main').map(box) as Box[],
            cards: visible('.msg .kind-card').map(box) as Box[],
            bubble: box(visible('.msg.is-owner .bubble').at(-1) ?? null)!,
          }
        })
        const rtl = lang === 'ar'
        const startOf = (b: Box) => (rtl ? m.column.right - b.right : b.left - m.column.left)
        const feedEnd = rtl ? m.inspector.right : m.inspector.left
        const endGap = (b: Box) => (rtl ? b.left - feedEnd : feedEnd - b.right)
        const space = rtl ? m.column.right - m.inspector.right : m.inspector.left - m.column.left

        expect(m.avatars.length).toBeGreaterThan(0)
        for (const a of m.avatars) expect(Math.abs(startOf(a) - 32), `avatar start gap ${startOf(a)}`).toBeLessThanOrEqual(1)
        for (const tx of m.texts) expect(Math.abs(startOf(tx) - 32 - 44), `message text start ${startOf(tx)}`).toBeLessThanOrEqual(1)
        for (const c of m.cards) expect(Math.abs(startOf(c) - 32 - 44), `card start ${startOf(c)}`).toBeLessThanOrEqual(1)
        // The owner bubble's end edge is the feed's end edge, one gutter before the inspector.
        expect(m.bubble).not.toBeNull()
        expect(Math.abs(endGap(m.bubble) - 32), `owner bubble end gap ${endGap(m.bubble)}`).toBeLessThanOrEqual(1)
        expect(Math.abs(m.composer.width - (space - 64)), `composer ${m.composer.width} vs ${space - 64}`).toBeLessThanOrEqual(2)
        expect(Math.abs(startOf(m.composer) - 32), 'composer start gap').toBeLessThanOrEqual(1)

        if (width === 1440) {
          mkdirSync(SHOTS, { recursive: true })
          await page.screenshot({ path: path.join(SHOTS, `warroom-${lang}-${width}.png`) })
        }
      })
    }

    test('onboarding spans the window at 1440 (title beside the form) and stacks at 390 with no label over a circle', async ({ page }) => {
      // The e2e owner is onboarded by now; pretend the setup is unfinished so /onboarding stays open.
      await page.route('**/api/settings?env=demo', async (route) => {
        if (route.request().method() !== 'GET') return route.fallback()
        const res = await route.fetch()
        const json = await res.json()
        json.data.settings.onboarding_completed = 0
        await route.fulfill({ response: res, json })
      })
      mkdirSync(SHOTS, { recursive: true })

      await page.setViewportSize({ width: 1440, height: 900 })
      await page.goto('/onboarding?step=risk')
      const head = page.locator('.ob-head')
      const main = page.locator('.ob-main')
      await expect(head).toBeVisible()
      await expect(main).toBeVisible()
      await page.waitForTimeout(400)
      const stepBar = (await page.getByRole('navigation').filter({ has: page.locator('ol') }).first().boundingBox())!
      const actionBar = (await page.getByTestId('ob-next').locator('xpath=../..').boundingBox())!
      for (const bar of [stepBar, actionBar]) {
        expect(Math.round(bar.x)).toBe(0)
        expect(Math.round(bar.width)).toBe(1440)
      }
      const h = (await head.boundingBox())!
      const f = (await main.boundingBox())!
      // side by side: no horizontal overlap, the title on the start side, both rows overlapping
      if (lang === 'ar') expect(h.x).toBeGreaterThanOrEqual(f.x + f.width)
      else expect(f.x).toBeGreaterThanOrEqual(h.x + h.width)
      expect(f.y).toBeLessThan(h.y + h.height)
      // the gutter: title block and form start/end 32px from the window edges
      const lo = Math.min(h.x, f.x)
      const hi = Math.max(h.x + h.width, f.x + f.width)
      expect(Math.abs(lo - 32)).toBeLessThanOrEqual(1)
      expect(Math.abs(1440 - hi - 32)).toBeLessThanOrEqual(1)
      await page.screenshot({ path: path.join(SHOTS, `onboarding-${lang}-1440.png`) })

      for (const width of [390, 375, 360]) {
        await page.setViewportSize({ width, height: 844 })
        await page.waitForTimeout(300)
        const hh = (await head.boundingBox())!
        const ff = (await main.boundingBox())!
        expect(ff.y, `${width}: form below the title`).toBeGreaterThanOrEqual(hh.y + hh.height)
        // The one visible step label never runs into another step's circle.
        const steps = page.locator('nav ol > li')
        const n = await steps.count()
        const label = steps.locator('[aria-current="step"] > span:nth-child(2)')
        const lb = (await label.boundingBox())!
        for (let i = 0; i < n; i += 1) {
          const c = (await steps.nth(i).locator('span[aria-hidden="true"]').first().boundingBox())!
          // distance between the label and the circle along the row (negative = overlap); the
          // label's own circle sits 8px from it, every other circle at least as far (design: ≥ 8px)
          const apart = Math.max(c.x - (lb.x + lb.width), lb.x - (c.x + c.width))
          expect(apart, `${width}: step label vs circle ${i + 1}`).toBeGreaterThanOrEqual(7.5)
        }
        const scroll = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
        expect(scroll, `${width}: document scrolls sideways`).toBeLessThanOrEqual(0)
        if (width === 390) await page.screenshot({ path: path.join(SHOTS, `onboarding-${lang}-390.png`) })
      }
    })
  })
}
