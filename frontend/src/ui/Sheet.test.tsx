import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button } from './Button'
import { Sheet, SheetClose } from './Sheet'
import { TextField } from './TextField'

function Harness() {
  return (
    <>
      <button type="button">Before</button>
      <Sheet
        trigger={<Button>Remove connection</Button>}
        title="Remove this connection?"
        description="Atlas stops syncing this account."
        actions={
          <>
            <SheetClose>
              <Button>Cancel</Button>
            </SheetClose>
            <Button variant="destructive">Remove</Button>
          </>
        }
      >
        <TextField label="Type the label to confirm" />
      </Sheet>
      <button type="button">After</button>
    </>
  )
}

describe('Sheet', () => {
  it('traps focus, closes on Esc and returns focus to the trigger', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Remove connection' })

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: 'Remove this connection?' })
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement))

    // Tab many more times than there are controls: focus never leaves the sheet.
    for (let i = 0; i < 8; i += 1) {
      await user.tab()
      expect(dialog).toContainElement(document.activeElement as HTMLElement)
    }
    await user.tab({ shift: true })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    expect(screen.getByRole('button', { name: 'Before', hidden: true })).not.toHaveFocus()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('closes from a SheetClose action and returns focus too', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Remove connection' })
    await user.click(trigger)
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})
