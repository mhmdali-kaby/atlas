import path from 'node:path'
import type { Page } from '@playwright/test'
import arPortfolio from '../../src/i18n/locales/ar/portfolio.json' with { type: 'json' }
import enPortfolio from '../../src/i18n/locales/en/portfolio.json' with { type: 'json' }
import arPortfolioOrders from '../../src/i18n/locales/ar/portfolio-orders.json' with { type: 'json' }
import enPortfolioOrders from '../../src/i18n/locales/en/portfolio-orders.json' with { type: 'json' }
import arPortfolioHistory from '../../src/i18n/locales/ar/portfolio-history.json' with { type: 'json' }
import enPortfolioHistory from '../../src/i18n/locales/en/portfolio-history.json' with { type: 'json' }
import arPortfolioPerformance from '../../src/i18n/locales/ar/portfolio-performance.json' with { type: 'json' }
import enPortfolioPerformance from '../../src/i18n/locales/en/portfolio-performance.json' with { type: 'json' }
import arMarkets from '../../src/i18n/locales/ar/settings-markets.json' with { type: 'json' }
import enMarkets from '../../src/i18n/locales/en/settings-markets.json' with { type: 'json' }
import arSettings from '../../src/i18n/locales/ar/settings.json' with { type: 'json' }
import enSettings from '../../src/i18n/locales/en/settings.json' with { type: 'json' }
import arAgents from '../../src/i18n/locales/ar/settings-agents.json' with { type: 'json' }
import enAgents from '../../src/i18n/locales/en/settings-agents.json' with { type: 'json' }
import arData from '../../src/i18n/locales/ar/settings-data.json' with { type: 'json' }
import enData from '../../src/i18n/locales/en/settings-data.json' with { type: 'json' }
import arSources from '../../src/i18n/locales/ar/settings-data-sources.json' with { type: 'json' }
import enSources from '../../src/i18n/locales/en/settings-data-sources.json' with { type: 'json' }
import arMoney from '../../src/i18n/locales/ar/settings-money.json' with { type: 'json' }
import enMoney from '../../src/i18n/locales/en/settings-money.json' with { type: 'json' }
import arShell from '../../src/i18n/locales/ar/shell.json' with { type: 'json' }
import enShell from '../../src/i18n/locales/en/shell.json' with { type: 'json' }
import arScorecards from '../../src/i18n/locales/ar/scorecards.json' with { type: 'json' }
import enScorecards from '../../src/i18n/locales/en/scorecards.json' with { type: 'json' }
import arGeneral from '../../src/i18n/locales/ar/settings-general.json' with { type: 'json' }
import enGeneral from '../../src/i18n/locales/en/settings-general.json' with { type: 'json' }
import arAutonomy from '../../src/i18n/locales/ar/settings-autonomy.json' with { type: 'json' }
import enAutonomy from '../../src/i18n/locales/en/settings-autonomy.json' with { type: 'json' }
import arBackup from '../../src/i18n/locales/ar/settings-backup.json' with { type: 'json' }
import enBackup from '../../src/i18n/locales/en/settings-backup.json' with { type: 'json' }
import arNotifications from '../../src/i18n/locales/ar/settings-notifications.json' with { type: 'json' }
import enNotifications from '../../src/i18n/locales/en/settings-notifications.json' with { type: 'json' }
import arSchedule from '../../src/i18n/locales/ar/settings-schedule.json' with { type: 'json' }
import enSchedule from '../../src/i18n/locales/en/settings-schedule.json' with { type: 'json' }
import arEnvironment from '../../src/i18n/locales/ar/settings-environment.json' with { type: 'json' }
import enEnvironment from '../../src/i18n/locales/en/settings-environment.json' with { type: 'json' }
import { EVIDENCE_DIR } from '../support/stack'
import { expect, test } from './appearance'

/**
 * ATL-41: the sidebar shows one area at a time under a War Room / Portfolio / Settings switcher,
 * in the four appearance projects on the real stack, at the design's 1440×900.
 */
const SHOTS = path.join(EVIDENCE_DIR, 'ATL-41')
const T = {
  en: { shell: enShell, portfolio: enPortfolio, portfolioOrders: enPortfolioOrders, portfolioHistory: enPortfolioHistory, portfolioPerformance: enPortfolioPerformance, settings: enSettings, money: enMoney, markets: enMarkets, agents: enAgents, data: enData, sources: enSources, scorecards: enScorecards, general: enGeneral, autonomy: enAutonomy, backup: enBackup, notifications: enNotifications, schedule: enSchedule, environment: enEnvironment },
  ar: { shell: arShell, portfolio: arPortfolio, portfolioOrders: arPortfolioOrders, portfolioHistory: arPortfolioHistory, portfolioPerformance: arPortfolioPerformance, settings: arSettings, money: arMoney, markets: arMarkets, agents: arAgents, data: arData, sources: arSources, scorecards: arScorecards, general: arGeneral, autonomy: arAutonomy, backup: arBackup, notifications: arNotifications, schedule: arSchedule, environment: arEnvironment },
}

test.use({ viewport: { width: 1440, height: 900 } })

const sidebarOf = (page: Page, lang: 'en' | 'ar') => page.getByRole('complementary', { name: T[lang].shell.nav.label })
const switcherOf = (page: Page, lang: 'en' | 'ar') => sidebarOf(page, lang).getByRole('navigation', { name: T[lang].shell.nav.areas })

test('on /portfolio the switcher selects Portfolio and lists only its pages; Settings switches without a reload', async ({ page, lang }, info) => {
  const t = T[lang]
  await page.goto('/portfolio')
  await expect(page.getByTestId('hero-equity')).toBeVisible()
  const sidebar = sidebarOf(page, lang)
  const switcher = switcherOf(page, lang)

  await expect(switcher.getByRole('link', { name: t.shell.area.portfolio })).toHaveAttribute('aria-current', 'true')
  await expect(switcher.getByRole('link', { name: t.shell.area.settings })).not.toHaveAttribute('aria-current', 'true')
  const portfolioList = sidebar.getByRole('navigation', { name: t.shell.area.portfolio })
  await expect(portfolioList.getByRole('link')).toHaveText([
    t.portfolio.nav.overview,
    t.portfolio.nav.positions,
    t.portfolioOrders.nav,
    t.portfolio.nav.holdings,
    t.portfolio.nav.cashflows,
    t.portfolioHistory.nav,
    t.portfolioPerformance.nav,
    t.scorecards.nav,
  ])
  await expect(sidebar.getByRole('link', { name: t.settings.nav.risk })).toHaveCount(0)

  // Mirrored in Arabic: the sidebar hugs the right edge, and each area's icon sits at its start (right).
  const box = (await sidebar.boundingBox())!
  if (lang === 'ar') expect(box.x).toBeGreaterThan(1440 - 400)
  else expect(box.x).toBeLessThan(100)
  const area = switcher.getByRole('link', { name: t.shell.area.portfolio })
  const [icon, label] = [(await area.locator('svg').boundingBox())!, (await area.locator('span').boundingBox())!]
  expect(lang === 'ar' ? icon.x > label.x : icon.x < label.x).toBe(true)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-portfolio.png`, animations: 'disabled' })

  await page.evaluate(() => ((window as unknown as { atlasNoReload: boolean }).atlasNoReload = true))
  await switcher.getByRole('link', { name: t.shell.area.settings }).click()
  // Settings opens on its first page, General (patch 35, ATL-238).
  await expect(page).toHaveURL(/\/settings\/general$/)
  await expect(switcher.getByRole('link', { name: t.shell.area.settings })).toHaveAttribute('aria-current', 'true')
  const settingsList = sidebar.getByRole('navigation', { name: t.shell.area.settings })
  await expect(settingsList.getByRole('link')).toHaveText([
    t.general.nav,
    t.settings.nav.connections,
    t.settings.nav.risk,
    t.money.nav,
    t.markets.nav,
    t.autonomy.nav,
    t.agents.nav,
    t.data.nav.data,
    t.sources.nav,
    t.backup.nav,
    t.notifications.nav,
    t.schedule.nav,
    t.environment.nav,
  ])
  await expect(sidebar.getByRole('link', { name: t.portfolio.nav.overview })).toHaveCount(0)
  expect(await page.evaluate(() => (window as unknown as { atlasNoReload?: boolean }).atlasNoReload)).toBe(true)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-settings.png` })
})

test('the switcher works by keyboard alone with a visible focus ring', async ({ page, lang }, info) => {
  const t = T[lang]
  await page.goto('/portfolio')
  await expect(page.getByTestId('hero-equity')).toBeVisible()
  const switcher = switcherOf(page, lang)
  const portfolio = switcher.getByRole('link', { name: t.shell.area.portfolio })
  const settings = switcher.getByRole('link', { name: t.shell.area.settings })
  const inSwitcher = () => page.evaluate(() => !!document.activeElement?.closest('[data-testid="area-switch"]'))

  // Tab until focus lands in the switcher: one stop, on the current area. The page's own stops
  // come first in Tab order, then the sidebar's search capsule (ATL-249), then the switcher.
  for (let i = 0; i < 40 && !(await inSwitcher()); i++) await page.keyboard.press('Tab')
  await expect(portfolio).toBeFocused()
  const ring = await portfolio.evaluate((el) => getComputedStyle(el).boxShadow)
  expect(ring, 'focus ring').toMatch(/3px/)
  await page.screenshot({ path: `${SHOTS}/${info.project.name}-focus.png`, clip: { x: lang === 'ar' ? 1440 - 280 : 0, y: 0, width: 280, height: 260 } })

  // Portfolio, Journal, Backtester, Settings: Settings is the last area, so End reaches it and
  // ArrowDown wraps from it to the first (War Room).
  await page.keyboard.press('ArrowDown')
  await expect(switcher.getByRole('link', { name: t.shell.area.journal })).toBeFocused()
  await page.keyboard.press('End')
  await expect(settings).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/settings\/general$/)
  await expect(settings).toHaveAttribute('aria-current', 'true')

  // Back by Space: Settings is now the switcher's Tab stop.
  await page.keyboard.press('Shift+Tab')
  for (let i = 0; i < 40 && !(await inSwitcher()); i++) await page.keyboard.press('Tab')
  await expect(settings).toBeFocused()
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowUp')
  await expect(portfolio).toBeFocused()
  await page.keyboard.press('Space')
  await expect(page).toHaveURL(/\/portfolio$/)
  await expect(portfolio).toHaveAttribute('aria-current', 'true')
})
