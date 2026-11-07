import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { SecureField } from './SecureField'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'
import { TextField } from './TextField'

function Fields() {
  const c = useCopy()
  return (
    <div className="grid max-w-[420px] gap-6 p-8">
      <TextField label={c('Label', 'التسمية')} placeholder="OKX demo" hint={c('Shown in Connections.', 'يظهر في الاتصالات.')} />
      <TextField label={c('Owner email', 'البريد الإلكتروني')} defaultValue="owner@atlas.local" type="email" />
      <TextField
        label={c('Passphrase', 'عبارة المرور')}
        defaultValue="wrong"
        error={c('OKX rejected this passphrase.', 'رفضت OKX عبارة المرور هذه.')}
      />
      <TextField label={c('Disabled', 'معطّل')} defaultValue="okx-demo" disabled />
      <SecureField label={c('API key', 'مفتاح API')} placeholder="xxxxxxxx-xxxx-xxxx" defaultValue="3f1c9a2e-7b44-4d0e-a1f2-9c7e" />
      <SecureField
        label={c('Secret key', 'المفتاح السري')}
        defaultValue="A1B2C3D4E5F6"
        error={c('OKX rejected this key.', 'رفضت OKX هذا المفتاح.')}
      />
      <SecureField label={c('Passphrase (disabled)', 'عبارة المرور (معطّل)')} defaultValue="secret" disabled />
    </div>
  )
}

const meta = {
  title: 'Controls/TextField and SecureField',
  component: Fields,
} satisfies Meta<typeof Fields>

export default meta
type Story = StoryObj<typeof meta>

/** Default, hint, error (keeps what was typed), disabled; SecureField masked with show/hide. */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    const invalid = canvasElement.querySelectorAll('input[aria-invalid="true"]')
    await expect(invalid).toHaveLength(2)
    for (const input of invalid) {
      await expect(input).toHaveAccessibleDescription(/rejected/)
      await expect((input as HTMLInputElement).value).not.toBe('')
    }
    const key = canvas.getByLabelText('API key')
    await expect(key).toHaveAttribute('type', 'password')
    await userEvent.click(canvas.getAllByRole('button', { name: 'Show' })[0]!)
    await expect(key).toHaveAttribute('type', 'text')
    await userEvent.click(canvas.getByRole('button', { name: 'Hide' }))
    await expect(key).toHaveAttribute('type', 'password')
  },
}

/** Focus turns the border accent and adds the 3px ring. */
export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    await expectFocusRing(canvasElement.querySelector('input'))
  },
}

export const Dark: Story = { globals: DARK }

/** Labels follow the page; keys stay LTR, so the reveal button stays at the right. */
export const RightToLeft: Story = {
  globals: RTL,
  play: async ({ canvasElement }) => {
    const input = within(canvasElement).getByLabelText('مفتاح API')
    const button = within(canvasElement).getAllByRole('button', { name: 'إظهار' })[0]!
    await expect(input.closest('[dir]')).toHaveAttribute('dir', 'ltr')
    await expect(button.getBoundingClientRect().left).toBeGreaterThan(input.getBoundingClientRect().left + 100)
  },
}

export const DarkRightToLeft: Story = { globals: DARK_RTL }
