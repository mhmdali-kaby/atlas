import { expect, waitFor } from 'storybook/test'

/** The §15 focus ring is a 3px box-shadow (design §8). Waits out the shadow transition. */
export async function expectFocusRing(el: Element | null): Promise<void> {
  await expect(el).not.toBeNull()
  if (!el) return
  await expect(el).toBe(document.activeElement)
  await waitFor(() => expect(getComputedStyle(el).boxShadow).toMatch(/0px 0px 0px 3px/))
}

export const DARK = { theme: 'dark', direction: 'ltr' } as const
export const RTL = { theme: 'light', direction: 'rtl' } as const
export const DARK_RTL = { theme: 'dark', direction: 'rtl' } as const
