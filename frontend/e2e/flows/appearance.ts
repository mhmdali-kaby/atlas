import { test as base, expect, type Page } from '@playwright/test'

/**
 * The appearance matrix's options (ATL-25). Atlas keeps language and theme in localStorage
 * (`atlas.lang`, `atlas.theme`) and applies them from a class on <html>, so colorScheme emulation
 * alone would render a light page: both are seeded before first paint with addInitScript.
 *
 * Since ATL-227 the signed-in SPA applies the owner's saved preferences at boot
 * (GET /api/me/preferences). The projects share one real backend in parallel, so each page answers
 * that GET itself with its own lang and theme; a PATCH still reaches Laravel.
 */
export type AppearanceOptions = { lang: 'en' | 'ar'; theme: 'light' | 'dark' }

export const test = base.extend<AppearanceOptions>({
  lang: ['en', { option: true }],
  theme: ['light', { option: true }],
  page: async ({ page, lang, theme }, provide) => {
    await seedAppearance(page, lang, theme)
    await provide(page)
  },
})

/** Seeds language and theme before first paint, and answers the boot preferences GET with them. */
export async function seedAppearance(page: Page, lang: 'en' | 'ar', theme: 'light' | 'dark') {
  await page.addInitScript(
    ([l, th]) => {
      localStorage.setItem('atlas.lang', l)
      localStorage.setItem('atlas.theme', th)
    },
    [lang, theme] as const,
  )
  await page.route(/\/api\/me\/preferences$/, (route) => {
    const req = route.request()
    if (req.method() !== 'GET') return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' },
      body: JSON.stringify({ data: { name: 'Atlas Owner', locale: lang, timezone: 'Asia/Baghdad', theme } }),
    })
  })
  await page.emulateMedia({ colorScheme: theme })
}

export { expect }
