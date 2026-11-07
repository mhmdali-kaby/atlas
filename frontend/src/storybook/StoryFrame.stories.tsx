import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect } from 'storybook/test'

/*
 * Checks that the Storybook toolbar (theme and direction globals) drives the
 * tokens: each story sets the globals the toolbar would, then reads computed styles.
 */
function Specimen() {
  return (
    <div className="flex flex-col gap-2 p-8">
      <p className="text-title-3">Total equity</p>
      <p className="text-body text-label-secondary">Automated trading can lose money.</p>
    </div>
  )
}

const meta = {
  title: 'Foundations/Theme and direction',
  component: Specimen,
} satisfies Meta<typeof Specimen>

export default meta
type Story = StoryObj<typeof meta>

const root = (canvas: HTMLElement) => canvas.querySelector<HTMLElement>('[data-story-root]')

export const Dark: Story = {
  globals: { theme: 'dark', direction: 'ltr' },
  play: async ({ canvasElement }) => {
    const el = root(canvasElement)
    await expect(el).not.toBeNull()
    if (!el) return
    await expect(document.documentElement.classList.contains('dark')).toBe(true)
    await expect(getComputedStyle(el).backgroundColor).toBe('rgb(0, 0, 0)')
    await expect(getComputedStyle(el).color).toBe('rgb(255, 255, 255)')
    await expect(getComputedStyle(document.documentElement).colorScheme).toBe('dark')
  },
}

export const Light: Story = {
  globals: { theme: 'light', direction: 'ltr' },
  play: async ({ canvasElement }) => {
    const el = root(canvasElement)
    await expect(el).not.toBeNull()
    if (!el) return
    await expect(document.documentElement.classList.contains('dark')).toBe(false)
    await expect(getComputedStyle(el).backgroundColor).toBe('rgb(255, 255, 255)')
    await expect(getComputedStyle(el).color).toBe('rgb(0, 0, 0)')
    await expect(getComputedStyle(document.documentElement).colorScheme).toBe('light')
  },
}

export const RightToLeft: Story = {
  globals: { theme: 'light', direction: 'rtl' },
  play: async ({ canvasElement }) => {
    const el = root(canvasElement)
    await expect(el).not.toBeNull()
    if (!el) return
    await expect(el.getAttribute('dir')).toBe('rtl')
    await expect(el.getAttribute('lang')).toBe('ar')
    await expect(getComputedStyle(el).direction).toBe('rtl')
    await expect(getComputedStyle(el).fontFamily).toMatch(/^"SF Arabic", "IBM Plex Sans Arabic"/)
    // Arabic line heights: body 17 / 26.
    const body = el.querySelector('.text-body')
    await expect(body ? getComputedStyle(body).lineHeight : '').toBe('26px')
  },
}

export const LeftToRight: Story = {
  globals: { theme: 'light', direction: 'ltr' },
  play: async ({ canvasElement }) => {
    const el = root(canvasElement)
    await expect(el).not.toBeNull()
    if (!el) return
    await expect(el.getAttribute('dir')).toBe('ltr')
    await expect(getComputedStyle(el).fontFamily).toMatch(/^-apple-system, .*"SF Pro Text"/)
    await expect(getComputedStyle(el).fontFamily).not.toContain('SF Arabic')
    const body = el.querySelector('.text-body')
    await expect(body ? getComputedStyle(body).lineHeight : '').toBe('22px')
  },
}
