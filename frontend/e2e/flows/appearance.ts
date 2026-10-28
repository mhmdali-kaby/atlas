import { test as base, expect } from '@playwright/test'

/**
 * The appearance matrix's options (ATL-25). Atlas keeps language and theme in localStorage
 * (`atlas.lang`, `atlas.theme`) and applies them from a class on <html>, so colorScheme emulation
 * alone would render a light page: both are seeded before first paint with addInitScript.
 */
export type AppearanceOptions = { lang: 'en' | 'ar'; theme: 'light' | 'dark' }

export const test = base.extend<AppearanceOptions>({
  lang: ['en', { option: true }],
  theme: ['light', { option: true }],
  page: async ({ page, lang, theme }, provide) => {
    await page.addInitScript(
      ([l, th]) => {
        localStorage.setItem('atlas.lang', l)
        localStorage.setItem('atlas.theme', th)
      },
      [lang, theme] as const,
    )
    await page.emulateMedia({ colorScheme: theme })
    await provide(page)
  },
})

export { expect }
