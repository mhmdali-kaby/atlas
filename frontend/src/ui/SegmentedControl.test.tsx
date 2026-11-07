import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { SegmentedControl } from './SegmentedControl'

function Harness() {
  const [v, setV] = useState('1M')
  return (
    <SegmentedControl
      aria-label="Range"
      value={v}
      onValueChange={setV}
      options={[
        { value: '1W', label: '1W' },
        { value: '1M', label: '1M' },
        { value: '3M', label: '3M' },
      ]}
    />
  )
}

describe('SegmentedControl', () => {
  it('is a radiogroup whose arrow keys move and select', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    expect(screen.getByRole('radiogroup', { name: 'Range' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '1M' })).toBeChecked()
    await user.tab()
    expect(screen.getByRole('radio', { name: '1M' })).toHaveFocus()
    await user.keyboard('{ArrowRight>}')
    await waitFor(() => expect(screen.getByRole('radio', { name: '3M' })).toHaveFocus())
    await waitFor(() => expect(screen.getByRole('radio', { name: '3M' })).toBeChecked())
    await user.keyboard('{/ArrowRight}')
  })
})
