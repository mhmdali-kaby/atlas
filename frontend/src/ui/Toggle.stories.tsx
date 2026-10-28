import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, waitFor, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'
import { Toggle } from './Toggle'

function Toggles() {
  const c = useCopy()
  return (
    <div className="grid gap-4 p-8">
      {[
        { label: c('Off', 'إيقاف'), props: {} },
        { label: c('On', 'تشغيل'), props: { defaultChecked: true } },
        { label: c('Disabled off', 'معطّل، إيقاف'), props: { disabled: true } },
        { label: c('Disabled on', 'معطّل، تشغيل'), props: { disabled: true, defaultChecked: true } },
      ].map(({ label, props }) => (
        <label key={label} className="flex items-center gap-3 text-callout">
          <Toggle aria-label={label} {...props} />
          {label}
        </label>
      ))}
    </div>
  )
}

const meta = {
  title: 'Controls/Toggle',
  component: Toggles,
} satisfies Meta<typeof Toggles>

export default meta
type Story = StoryObj<typeof meta>

/** Off, on (accent), and both disabled. Click and Space toggle. */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const off = canvas.getByRole('switch', { name: 'Off' })
    await expect(off).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(off)
    await expect(off).toHaveAttribute('aria-checked', 'true')
    await expect(canvas.getByRole('switch', { name: 'Disabled on' })).toBeDisabled()
  },
}

export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    await expectFocusRing(canvasElement.querySelector('[role="switch"]'))
  },
}

export const Dark: Story = { globals: DARK }

/** The knob mirrors: on sits at the inline end, which is the left in RTL. */
export const RightToLeft: Story = {
  globals: RTL,
  play: async ({ canvasElement }) => {
    const on = within(canvasElement).getByRole('switch', { name: 'تشغيل' })
    const off = within(canvasElement).getByRole('switch', { name: 'إيقاف' })
    const knobOffset = (sw: HTMLElement) => {
      const knob = sw.querySelector('span')!.getBoundingClientRect()
      const track = sw.getBoundingClientRect()
      return { start: track.right - knob.right, end: knob.left - track.left }
    }
    await waitFor(() => {
      expect(Math.round(knobOffset(on).end)).toBe(2)
      expect(Math.round(knobOffset(off).start)).toBe(2)
    })
  },
}

export const DarkRightToLeft: Story = { globals: DARK_RTL }
