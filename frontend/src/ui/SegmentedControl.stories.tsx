import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'
import { expect, userEvent, waitFor, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { SegmentedControl } from './SegmentedControl'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'

type Range = '1D' | '1W' | '1M' | '3M' | 'YTD' | 'All'

function Segments() {
  const c = useCopy()
  const [range, setRange] = useState<Range>('1M')
  const ranges: Range[] = ['1D', '1W', '1M', '3M', 'YTD', 'All']
  return (
    <div className="grid justify-items-start gap-6 p-8">
      <SegmentedControl
        aria-label={c('Range', 'النطاق')}
        value={range}
        onValueChange={setRange}
        options={ranges.map((r) => ({ value: r, label: r === 'All' ? c('All', 'الكل') : r }))}
      />
      <SegmentedControl
        aria-label={c('Theme', 'المظهر')}
        defaultValue="system"
        options={[
          { value: 'system', label: c('System', 'النظام') },
          { value: 'light', label: c('Light', 'فاتح') },
          { value: 'dark', label: c('Dark', 'داكن'), disabled: true },
        ]}
      />
      <SegmentedControl
        aria-label={c('Disabled', 'معطّل')}
        defaultValue="a"
        disabled
        options={[
          { value: 'a', label: c('Isolated', 'معزول') },
          { value: 'b', label: c('Cross', 'متقاطع') },
        ]}
      />
    </div>
  )
}

const meta = {
  title: 'Controls/SegmentedControl',
  component: Segments,
} satisfies Meta<typeof Segments>

export default meta
type Story = StoryObj<typeof meta>

/** Selected thumb, a disabled segment, a disabled control. Arrow keys move and select. */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const group = canvas.getByRole('radiogroup', { name: 'Range' })
    await expect(within(group).getByRole('radio', { name: '1M' })).toBeChecked()
    within(group).getByRole('radio', { name: '1M' }).focus()
    // Radix selects on focus while the arrow is held, as in a real keypress.
    await userEvent.keyboard('{ArrowRight>}')
    await waitFor(() => expect(within(group).getByRole('radio', { name: '3M' })).toBeChecked())
    await userEvent.keyboard('{/ArrowRight}')
    await expect(group.querySelectorAll('[data-segment-thumb]')).toHaveLength(1)
    await expect(canvas.getByRole('radio', { name: 'Dark' })).toBeDisabled()
  },
}

export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    await expectFocusRing(canvasElement.querySelector('[role="radio"][data-state="checked"]'))
  },
}

export const Dark: Story = { globals: DARK }

/** In RTL the segments run right to left, and ArrowLeft moves to the next one. */
export const RightToLeft: Story = {
  globals: RTL,
  play: async ({ canvasElement }) => {
    const group = within(canvasElement).getByRole('radiogroup', { name: 'النطاق' })
    const first = within(group).getByRole('radio', { name: '1D' }).getBoundingClientRect()
    const last = within(group).getByRole('radio', { name: 'الكل' }).getBoundingClientRect()
    await expect(first.left).toBeGreaterThan(last.left)
    within(group).getByRole('radio', { name: '1M' }).focus()
    // Radix selects on focus while the arrow is held, as in a real keypress.
    await userEvent.keyboard('{ArrowLeft>}')
    await waitFor(() => expect(within(group).getByRole('radio', { name: '3M' })).toBeChecked())
    await userEvent.keyboard('{/ArrowLeft}')
  },
}

export const DarkRightToLeft: Story = { globals: DARK_RTL }
