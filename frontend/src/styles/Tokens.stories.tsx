import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect } from 'storybook/test'
import { HERO_STYLE, PRIMITIVE_TOKENS, RADII, SEMANTIC_GROUPS, TEXT_STYLES } from './tokenCatalogue'
import { TokensPage } from './TokensPage'

const meta = {
  title: 'Foundations/Tokens',
  component: TokensPage,
} satisfies Meta<typeof TokensPage>

export default meta
type Story = StoryObj<typeof meta>

/** The §15 values, as the browser must resolve them, per theme. */
const EXPECTED_PRIMITIVES = {
  light: { '--sys-bg': 'rgb(255, 255, 255)', '--sys-label': 'rgb(0, 0, 0)', '--sys-blue': 'rgb(0, 122, 255)' },
  dark: { '--sys-bg': 'rgb(0, 0, 0)', '--sys-label': 'rgb(255, 255, 255)', '--sys-blue': 'rgb(10, 132, 255)' },
} as const

function resolvedColour(scope: Element, token: string): string {
  const probe = document.createElement('div')
  probe.style.color = `var(${token})`
  scope.appendChild(probe)
  const colour = getComputedStyle(probe).color
  probe.remove()
  return colour
}

export const Tokens: Story = {
  play: async ({ canvasElement }) => {
    const allColourTokens = [...PRIMITIVE_TOKENS.tokens, ...SEMANTIC_GROUPS.flatMap((g) => g.tokens)]

    for (const theme of ['light', 'dark'] as const) {
      const sheet = canvasElement.querySelector(`[data-theme-sheet="${theme}"]`)
      await expect(sheet).not.toBeNull()
      if (!sheet) return

      // Every colour token is shown, by name, with a resolved value.
      for (const token of allColourTokens) {
        const swatch = sheet.querySelector(`[data-token="${token}"]`)
        await expect(swatch, `${theme}: ${token}`).not.toBeNull()
        await expect(swatch?.textContent).toContain(token)
        await expect(swatch?.querySelector('[data-token-value]')?.textContent?.length ?? 0).toBeGreaterThan(0)
      }

      for (const [token, rgb] of Object.entries(EXPECTED_PRIMITIVES[theme])) {
        await expect(resolvedColour(sheet, token)).toBe(rgb)
      }

      // The four radii and the ten §15 text styles (plus the hero).
      for (const radius of RADII) {
        const box = sheet.querySelector(`[data-radius="${radius.name}"] > div`)
        await expect(box).not.toBeNull()
        if (box) await expect(getComputedStyle(box).borderTopLeftRadius).toBe(
          { control: '10px', card: '12px', panel: '16px', sheet: '20px' }[radius.name],
        )
      }
      await expect(TEXT_STYLES).toHaveLength(10)
      for (const style of [...TEXT_STYLES, HERO_STYLE]) {
        await expect(sheet.querySelector(`[data-text-style="${style.name}"]`)).not.toBeNull()
      }
    }

    // Spot-check the type scale in px (16px root).
    const light = canvasElement.querySelector('[data-theme-sheet="light"]')
    const sample = (name: string) => {
      const el = light?.querySelector(`[data-text-style="${name}"] > span`)
      return el ? getComputedStyle(el) : null
    }
    await expect(sample('large-title')?.fontSize).toBe('34px')
    await expect(sample('large-title')?.lineHeight).toBe('41px')
    await expect(sample('large-title')?.fontWeight).toBe('600')
    await expect(sample('body')?.fontSize).toBe('17px')
    await expect(sample('caption')?.fontSize).toBe('12px')
    await expect(sample('caption')?.lineHeight).toBe('16px')
  },
}
