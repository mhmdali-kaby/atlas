import { expect, test } from '@playwright/test'

type FirstFrame = { htmlClass: string; scheme: string; rootChildren: number } | null
type Probe = { firstFrame: FirstFrame; atMount: string | null }

/**
 * Records the page's state at the first animation frame that has a <body>
 * (the earliest moment anything can be painted) and at the moment React first
 * puts content into #root. Runs before any page script.
 */
function installProbe(theme: string | null) {
  if (theme) window.localStorage.setItem('atlas.theme', theme)
  else window.localStorage.removeItem('atlas.theme')
  const probe: Probe = { firstFrame: null, atMount: null }
  ;(window as unknown as { __probe: Probe }).__probe = probe

  const onFrame = () => {
    if (!document.body) return requestAnimationFrame(onFrame)
    probe.firstFrame = {
      htmlClass: document.documentElement.className,
      scheme: getComputedStyle(document.documentElement).colorScheme,
      rootChildren: document.getElementById('root')?.childElementCount ?? -1,
    }
  }
  requestAnimationFrame(onFrame)

  new MutationObserver((_, observer) => {
    const root = document.getElementById('root')
    if (root && root.childElementCount > 0) {
      probe.atMount = document.documentElement.className
      observer.disconnect()
    }
  }).observe(document, { childList: true, subtree: true })
}

const isDark = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.documentElement.classList.contains('dark'))

async function readProbe(page: import('@playwright/test').Page): Promise<Probe> {
  await page.waitForFunction(() => {
    const p = (window as unknown as { __probe?: Probe }).__probe
    return Boolean(p?.firstFrame && p.atMount !== null)
  })
  return page.evaluate(() => (window as unknown as { __probe: Probe }).__probe)
}

test('atlas.theme=dark: <html> is dark before React mounts and at first paint', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.addInitScript(installProbe, 'dark')
  await page.goto('/')

  const probe = await readProbe(page)
  expect(probe.firstFrame?.htmlClass.split(' ')).toContain('dark')
  expect(probe.firstFrame?.scheme).toBe('dark')
  expect(probe.firstFrame?.rootChildren).toBe(0) // React had not mounted yet
  expect(probe.atMount?.split(' ')).toContain('dark')

  await expect.poll(() => isDark(page)).toBe(true)
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(0, 0, 0)')
})

test('atlas.theme=light wins over a dark OS', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(installProbe, 'light')
  await page.goto('/')

  const probe = await readProbe(page)
  expect(probe.firstFrame?.htmlClass.split(' ')).not.toContain('dark')
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
})

test('no stored choice follows the OS, and keeps following it live', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(installProbe, null)
  await page.goto('/')

  const probe = await readProbe(page)
  expect(probe.firstFrame?.htmlClass.split(' ')).toContain('dark')

  await page.emulateMedia({ colorScheme: 'light' })
  await expect.poll(() => isDark(page)).toBe(false)
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
})
