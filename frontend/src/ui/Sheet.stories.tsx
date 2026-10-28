import type { Meta, StoryObj } from '@storybook/react-vite'
import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { expect, userEvent, waitFor, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { Button } from './Button'
import { Sheet, SheetClose } from './Sheet'
import { DARK, DARK_RTL, RTL } from './storyHelpers'
import { TextField } from './TextField'

function RemoveSheet({ startOpen = false }: { startOpen?: boolean }) {
  const c = useCopy()
  const [typed, setTyped] = useState('')
  const [removing, setRemoving] = useState(false)
  const label = 'OKX demo'
  const mismatch = typed !== '' && typed !== label
  return (
    <div className="p-8">
      <Sheet
        defaultOpen={startOpen}
        trigger={
          <Button variant="plain-destructive" icon={<Trash2 />}>
            {c('Remove connection', 'إزالة الاتصال')}
          </Button>
        }
        icon={<Trash2 strokeWidth={1.5} />}
        tone="destructive"
        title={c('Remove this connection?', 'إزالة هذا الاتصال؟')}
        description={c(
          'Atlas stops syncing this account and deletes its keys. Type the label to confirm.',
          'يتوقف أطلس عن مزامنة هذا الحساب ويحذف مفاتيحه. اكتب التسمية للتأكيد.',
        )}
        actions={
          <>
            <SheetClose>
              <Button>{c('Cancel', 'إلغاء')}</Button>
            </SheetClose>
            <Button
              variant="destructive"
              disabled={typed !== label}
              loading={removing}
              onClick={() => setRemoving(true)}
            >
              {c('Remove connection', 'إزالة الاتصال')}
            </Button>
          </>
        }
      >
        <TextField
          label={c('Connection label', 'تسمية الاتصال')}
          placeholder={label}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          error={mismatch ? c('That is not the label.', 'هذه ليست التسمية.') : undefined}
        />
      </Sheet>
    </div>
  )
}

const meta = {
  title: 'Controls/Sheet',
  component: RemoveSheet,
} satisfies Meta<typeof RemoveSheet>

export default meta
type Story = StoryObj<typeof meta>

/** Closed: the trigger. Opening drops the sheet from under the toolbar. */
export const Closed: Story = {}

/** Open, with the typed-confirmation field (error state while it does not match) and a disabled destructive action. */
export const Open: Story = { args: { startOpen: true } }

/** Focus is trapped; Esc closes; focus returns to the trigger. */
export const KeyboardAndFocus: Story = {
  play: async ({ canvasElement }) => {
    const trigger = within(canvasElement).getByRole('button', { name: 'Remove connection' })
    await userEvent.click(trigger)
    const dialog = await within(document.body).findByRole('dialog', { name: 'Remove this connection?' })
    for (let i = 0; i < 6; i += 1) {
      await userEvent.tab()
      await expect(dialog.contains(document.activeElement)).toBe(true)
    }
    await userEvent.type(within(dialog).getByLabelText('Connection label'), 'wrong')
    await expect(within(dialog).getByText('That is not the label.')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(within(document.body).queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(trigger).toBe(document.activeElement))
  },
}

export const Dark: Story = { args: { startOpen: true }, globals: DARK }
export const RightToLeft: Story = { args: { startOpen: true }, globals: RTL }
export const DarkRightToLeft: Story = { args: { startOpen: true }, globals: DARK_RTL }
