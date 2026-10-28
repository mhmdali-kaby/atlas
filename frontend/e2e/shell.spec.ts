import { expect, test, type Page } from '@playwright/test'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const SHOTS = '../../evidence/ATL-17'

/** A fake Laravel on the API origin: a session that sign-out ends. */
async function fakeApi(page: Page, signedIn = true) {
  const state = { signedIn }
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const json = (status: number, body?: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' },
        body: body === undefined ? '' : JSON.stringify(body),
      })
    if (method === 'OPTIONS') {
      return route.fulfill({
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': route.request().headers()['origin'] ?? '*',
          'Access-Control-Allow-Credentials': 'true',
          'Access-Control-Allow-Headers': '*',
          'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE',
        },
      })
    }
    if (url.pathname === '/sanctum/csrf-cookie') return json(204)
    if (url.pathname === '/api/auth/status') return json(200, { owner_exists: true })
    if (url.pathname === '/api/auth/me') return state.signedIn ? json(200, { data: OWNER }) : json(401, { message: 'Unauthenticated.' })
    if (url.pathname === '/api/auth/logout') {
      state.signedIn = false
      return json(204)
    }
    return json(404, { message: 'Not found' })
  })
  return state
}

const footer = (page: Page) => page.getByRole('contentinfo')
const isDark = (page: Page) => page.evaluate(() => document.documentElement.classList.contains('dark'))

test('the risk footer is on the login page and the shell, in English and Arabic', async ({ page }) => {
  await fakeApi(page, false)
  await page.goto('/login')
  await expect(footer(page)).toHaveText('Automated trading can lose money. Demo/Live: Demo')
  await page.screenshot({ path: `${SHOTS}/login-en.png` })

  await page.getByRole('button', { name: 'Switch to Arabic' }).click()
  await expect(footer(page)).toHaveText('قد يؤدي التداول الآلي إلى خسارة المال. تجريبي/حقيقي: تجريبي')
  await page.screenshot({ path: `${SHOTS}/login-ar.png` })
})

test('Arabic flips <html> to rtl at once, puts the sidebar on the right, and survives a reload', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await fakeApi(page)
  await page.goto('/')
  const sidebar = page.getByRole('complementary', { name: 'Sidebar' })
  await expect(sidebar).toBeVisible()
  await expect(footer(page)).toContainText('Demo/Live: Demo')
  expect((await sidebar.boundingBox())!.x).toBeLessThan(100)
  await page.screenshot({ path: `${SHOTS}/shell-en-light.png` })

  await page.getByRole('button', { name: 'Switch to Arabic' }).click()
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(page.locator('html')).toHaveAttribute('lang', 'ar')
  const rtlSidebar = page.getByRole('complementary', { name: 'الشريط الجانبي' })
  await expect.poll(async () => (await rtlSidebar.boundingBox())!.x).toBeGreaterThan(900)

  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
  await expect(rtlSidebar).toBeVisible()
  expect((await rtlSidebar.boundingBox())!.x).toBeGreaterThan(900)
  await expect(footer(page)).toContainText('تجريبي/حقيقي: تجريبي')
  await page.screenshot({ path: `${SHOTS}/shell-ar-light.png` })
})

test('Dark, Light and System apply at once and survive a reload; System follows the OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await fakeApi(page)
  await page.goto('/')
  const choose = async (name: string) => {
    await page.getByRole('button', { name: 'Appearance' }).click()
    await page.getByRole('menuitemradio', { name }).click()
  }

  await choose('Dark')
  await expect.poll(() => isDark(page)).toBe(true)
  await page.reload()
  await expect.poll(() => isDark(page)).toBe(true)
  await page.screenshot({ path: `${SHOTS}/shell-en-dark.png` })

  await page.getByRole('button', { name: 'Appearance' }).click()
  await page.screenshot({ path: `${SHOTS}/theme-menu-dark.png` })
  await page.keyboard.press('Escape')

  await choose('Light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(() => isDark(page)).toBe(false)
  await page.reload()
  await expect.poll(() => isDark(page)).toBe(false)

  await choose('System')
  await expect.poll(() => isDark(page)).toBe(true)
  await page.emulateMedia({ colorScheme: 'light' })
  await expect.poll(() => isDark(page)).toBe(false)
  await page.reload()
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect.poll(() => isDark(page)).toBe(true)
})

test('Sign out returns to /login and Back does not show the shell', async ({ page }) => {
  await fakeApi(page)
  // Two protected pages in history, so Back has somewhere protected to go.
  await page.goto('/portfolio')
  await expect(page.getByRole('complementary', { name: 'Sidebar' })).toBeVisible()
  await page.evaluate(() => {
    window.history.pushState({}, '', '/portfolio/positions')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page).toHaveURL(/\/portfolio\/positions$/)
  await page.getByRole('button', { name: 'Account' }).click()
  await page.screenshot({ path: `${SHOTS}/account-menu.png` })
  await page.getByRole('menuitem', { name: 'Sign out' }).click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByText('Signed out', { exact: true })).toBeVisible()

  await page.goBack()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('complementary', { name: 'Sidebar' })).toHaveCount(0)
})
