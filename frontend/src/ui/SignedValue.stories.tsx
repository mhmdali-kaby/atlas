import type { Meta, StoryObj } from '@storybook/react-vite'
import { expect, userEvent, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { SignedValue } from './SignedValue'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'

function Values() {
  const c = useCopy()
  const rows: Array<[string, string | null, 'USD' | 'percent', string?]> = [
    [c('Gain', 'ربح'), '2104.18', 'USD'],
    [c('Loss', 'خسارة'), '-1234.5', 'USD'],
    [c('Zero', 'صفر'), '0', 'USD'],
    [c('Percent gain', 'نسبة ربح'), '0.76', 'percent'],
    [c('Percent loss', 'نسبة خسارة'), '-1.2', 'percent'],
    [c('No value', 'لا قيمة'), null, 'USD'],
    [
      c('No value, with reason', 'لا قيمة، مع السبب'),
      null,
      'percent',
      c(
        'Atlas shows a percentage once it has a starting equity above zero to measure from.',
        'يعرض أطلس النسبة بعد أن يتوفر رصيد ابتدائي أكبر من صفر للقياس منه.',
      ),
    ],
  ]
  return (
    <table className="m-8 text-subheadline">
      <tbody>
        {rows.map(([label, value, unit, tip]) => (
          <tr key={label}>
            <th scope="row" className="py-2 pe-8 text-start font-normal text-label-secondary">
              {label}
            </th>
            <td className="py-2 text-end">
              <SignedValue value={value} unit={unit} nullTip={tip} />
            </td>
          </tr>
        ))}
        <tr>
          <th scope="row" className="py-2 pe-8 text-start font-normal text-label-secondary">
            {c('Title size', 'حجم العنوان')}
          </th>
          <td className="py-2 text-end text-title-3">
            <SignedValue value="-214.07" unit="USD" />
          </td>
        </tr>
      </tbody>
    </table>
  )
}

const meta = {
  title: 'Controls/SignedValue',
  component: Values,
} satisfies Meta<typeof Values>

export default meta
type Story = StoryObj<typeof meta>

const MINUS = '−'

/** Profit green with "+" and an up arrow, loss red with "−" and a down arrow, zero plain, null "—". */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const loss = canvasElement.querySelector('[data-sign="loss"]')!
    await expect(loss.querySelector('svg')).not.toBeNull()
    await expect(loss.textContent).toContain(`${MINUS}$1,234.50`)
    await expect(getComputedStyle(loss).color).toBe('rgb(215, 0, 21)')
    const gain = canvasElement.querySelector('[data-sign="profit"]')!
    await expect(gain.textContent).toContain('+$2,104.18')
    await expect(getComputedStyle(gain).color).toBe('rgb(31, 127, 55)')
    const zero = canvasElement.querySelector('[data-sign="zero"]')!
    await expect(zero.querySelector('svg')).toBeNull()
  },
}

/** A "—" with a reason is focusable and reads the reason out. */
export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    const dash = within(canvasElement).getByRole('note')
    await expectFocusRing(dash)
  },
}

export const Dark: Story = {
  globals: DARK,
  play: async ({ canvasElement }) => {
    await expect(getComputedStyle(canvasElement.querySelector('[data-sign="loss"]')!).color).toBe('rgb(255, 105, 97)')
  },
}

/** Western digits and a leading sign in Arabic; arrows mirror. */
export const RightToLeft: Story = {
  globals: RTL,
  play: async ({ canvasElement }) => {
    const loss = canvasElement.querySelector('[data-sign="loss"]')!
    await expect(loss.textContent).toContain(`${MINUS}$1,234.50`)
    await expect(getComputedStyle(loss.querySelector('svg')!).scale).toBe('-1 1')
  },
}

export const DarkRightToLeft: Story = { globals: DARK_RTL }
