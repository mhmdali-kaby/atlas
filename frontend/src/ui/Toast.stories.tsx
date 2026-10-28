import type { Meta, StoryObj } from '@storybook/react-vite'
import { useEffect } from 'react'
import { expect, userEvent, waitFor, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { Button } from './Button'
import { DARK, DARK_RTL, RTL, expectFocusRing } from './storyHelpers'
import { Toaster } from './Toast'
import { toast, useToasts } from './toastStore'

function ToastDemo({ preset = false }: { preset?: boolean }) {
  const c = useCopy()
  useEffect(() => {
    useToasts.setState({ toasts: [] })
    if (!preset) return
    toast({ title: c('Saved', 'تم الحفظ'), body: c('Risk limits updated to version 4.', 'حُدّثت حدود المخاطر إلى الإصدار 4.') })
    toast({ title: c('Last synced 6 minutes ago', 'آخر مزامنة قبل 6 دقائق'), tone: 'warning' })
    toast({ title: c('Test connection failed', 'فشل اختبار الاتصال'), body: c('OKX did not answer.', 'لم تستجب OKX.'), tone: 'error' })
    toast({ title: c('Copied', 'تم النسخ'), tone: 'info' })
  }, [preset, c])
  return (
    <div className="p-8">
      <Button variant="primary" onClick={() => toast({ title: c('Saved', 'تم الحفظ') })}>
        {c('Save changes', 'حفظ التغييرات')}
      </Button>
      <Toaster />
    </div>
  )
}

const meta = {
  title: 'Controls/Toast',
  component: ToastDemo,
} satisfies Meta<typeof ToastDemo>

export default meta
type Story = StoryObj<typeof meta>

/** Click to show a toast at the top end; it dismisses itself after 4.2s or on its close button. */
export const Trigger: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole('button', { name: 'Save changes' }))
    const body = within(document.body)
    const saved = await body.findByText('Saved', { selector: 'li *' })
    const card = saved.closest('li')!
    await expect(card.getBoundingClientRect().right).toBeGreaterThan(window.innerWidth - 40)
    await userEvent.click(body.getByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(body.queryByText('Saved', { selector: 'li *' })).toBeNull())
  },
}

/** Tab from the trigger reaches the toast, which shows the 3px accent focus ring (design §8). */
async function playFocusVisible(canvasElement: HTMLElement, trigger: string, title: string) {
  await userEvent.click(within(canvasElement).getByRole('button', { name: trigger }))
  const card = (await within(document.body).findByText(title, { selector: 'li *' })).closest('li')
  await userEvent.tab()
  await waitFor(() => expect(card).toBe(document.activeElement))
  await expect(card!.matches(':focus-visible')).toBe(true)
  await expectFocusRing(card)
}

export const FocusVisible: Story = {
  play: ({ canvasElement }) => playFocusVisible(canvasElement, 'Save changes', 'Saved'),
}

export const FocusVisibleRightToLeft: Story = {
  globals: RTL,
  play: ({ canvasElement }) => playFocusVisible(canvasElement, 'حفظ التغييرات', 'تم الحفظ'),
}

/** Success, warning, error and info, stacked. */
export const Tones: Story = { args: { preset: true } }
export const Dark: Story = { args: { preset: true }, globals: DARK }

/** In RTL the stack sits at the top left (the inline end). */
export const RightToLeft: Story = {
  args: { preset: true },
  globals: RTL,
  play: async () => {
    const card = (await within(document.body).findByText('تم الحفظ', { selector: 'li *' })).closest('li')!
    await waitFor(() => expect(card.getBoundingClientRect().left).toBeLessThan(40))
  },
}

export const DarkRightToLeft: Story = { args: { preset: true }, globals: DARK_RTL }
