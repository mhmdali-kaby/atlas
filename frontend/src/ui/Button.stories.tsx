import type { Meta, StoryObj } from '@storybook/react-vite'
import { Plug, RefreshCw, Trash2 } from 'lucide-react'
import { expect, userEvent, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { Button, type ButtonVariant } from './Button'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'

const VARIANTS: ButtonVariant[] = ['primary', 'secondary', 'destructive', 'plain', 'plain-destructive']

function Gallery() {
  const c = useCopy()
  return (
    <div className="grid gap-6 p-8">
      {VARIANTS.map((variant) => (
        <div key={variant} className="flex flex-wrap items-center gap-3" data-variant={variant}>
          <span className="w-36 text-footnote text-label-secondary">{variant}</span>
          <Button variant={variant}>{c('Save changes', 'حفظ التغييرات')}</Button>
          <Button variant={variant} icon={<RefreshCw />}>
            {c('Test connection', 'اختبار الاتصال')}
          </Button>
          <Button variant={variant} loading>
            {c('Testing…', 'جارٍ الاختبار…')}
          </Button>
          <Button variant={variant} disabled>
            {c('Disabled', 'معطّل')}
          </Button>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <span className="w-36 text-footnote text-label-secondary">sizes</span>
        <Button variant="primary" size="sm" icon={<Plug />}>
          {c('Small', 'صغير')}
        </Button>
        <Button variant="primary" size="md">
          {c('Medium', 'متوسط')}
        </Button>
        <Button variant="primary" size="lg">
          {c('Large', 'كبير')}
        </Button>
        <Button variant="destructive" icon={<Trash2 />}>
          {c('Remove connection', 'إزالة الاتصال')}
        </Button>
      </div>
    </div>
  )
}

const meta = {
  title: 'Controls/Button',
  component: Gallery,
} satisfies Meta<typeof Gallery>

export default meta
type Story = StoryObj<typeof meta>

/** Every variant: default, with icon, loading (spinner, label kept) and disabled. Hover steps the fill. */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const loading = canvas.getAllByRole('button', { busy: true })
    await expect(loading).toHaveLength(VARIANTS.length)
    for (const b of canvas.getAllByRole('button', { name: /Disabled/ })) {
      await expect(b).toBeDisabled()
      await expect(getComputedStyle(b).opacity).toBe('0.4')
    }
  },
}

/** Keyboard focus shows the 3px accent ring at 40%. */
export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    await expectFocusRing(canvasElement.querySelector('[data-variant="primary"] button'))
  },
}

export const Dark: Story = { globals: DARK }
export const RightToLeft: Story = { globals: RTL }
export const DarkRightToLeft: Story = { globals: DARK_RTL }
