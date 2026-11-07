import type { Meta, StoryObj } from '@storybook/react-vite'
import { ChevronRight, Clock, KeyRound, Shield } from 'lucide-react'
import { expect, userEvent, within } from 'storybook/test'
import { useCopy } from '../storybook/copy'
import { Card } from './Card'
import { GroupedList, Row } from './GroupedList'
import { DARK, DARK_RTL, expectFocusRing, RTL } from './storyHelpers'
import { Toggle } from './Toggle'

function Lists() {
  const c = useCopy()
  return (
    <div className="grid max-w-[720px] gap-8 bg-background-grouped p-8">
      <GroupedList title={c('OKX demo', 'OKX التجريبي')} note={c('Keys are stored encrypted.', 'تُخزَّن المفاتيح مشفّرة.')}>
        <Row label={c('Label', 'التسمية')}>OKX demo</Row>
        <Row label={c('API key', 'مفتاح API')}>
          <span className="fig">•••• 7Q2F</span>
        </Row>
        <Row label={c('Position mode', 'وضع المراكز')} sub={c('Read from OKX', 'مقروء من OKX')}>
          {c('Net', 'صافي')}
        </Row>
      </GroupedList>

      <GroupedList title={c('With icons', 'مع أيقونات')}>
        <Row icon={<KeyRound />} label={c('Replace keys', 'استبدال المفاتيح')} onClick={() => undefined}>
          <ChevronRight aria-hidden="true" className="size-4 rtl:-scale-x-100" strokeWidth={1.5} />
        </Row>
        <Row icon={<Shield />} label={c('Withdraw off', 'السحب متوقف')} sub={c('Checked at the last test', 'تم التحقق في آخر اختبار')} />
        <Row icon={<Clock />} label={c('Sync automatically', 'مزامنة تلقائية')} labelId="row-sync">
          <Toggle aria-labelledby="row-sync" defaultChecked />
        </Row>
        <Row icon={<Clock />} label={c('Live trading', 'التداول الحقيقي')} sub={c('Enabled in Phase 4', 'يُفعَّل في المرحلة 4')} disabled />
      </GroupedList>

      <Card>
        <h3 className="text-headline">{c('Card', 'بطاقة')}</h3>
        <p className="mt-1 text-subheadline text-label-secondary">
          {c('Radius 12, padding 20, on the tertiary background.', 'نصف قطر 12، وحشوة 20، على الخلفية الثالثة.')}
        </p>
      </Card>
    </div>
  )
}

const meta = {
  title: 'Controls/GroupedList, Row and Card',
  component: Lists,
} satisfies Meta<typeof Lists>

export default meta
type Story = StoryObj<typeof meta>

/** Plain rows, a button row (hover fills), a row with a Toggle, a disabled row; and a Card. */
export const States: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement)
    await expect(canvas.getAllByRole('list')).toHaveLength(2)
    await expect(canvas.getByRole('switch', { name: 'Sync automatically' })).toBeChecked()
    const disabled = canvas.getByText('Live trading').closest('[role="listitem"]')!
    await expect(getComputedStyle(disabled).opacity).toBe('0.4')
  },
}

export const FocusVisible: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.tab()
    await expectFocusRing(canvasElement.querySelector('[role="listitem"] button'))
  },
}

export const Dark: Story = { globals: DARK }

/** Separators are inset from the inline start, so in RTL they leave the gap on the right. */
export const RightToLeft: Story = {
  globals: RTL,
  play: async ({ canvasElement }) => {
    const second = canvasElement.querySelectorAll('[role="listitem"]')[1]!
    const before = getComputedStyle(second, '::before')
    await expect(before.insetInlineStart).toBe('16px')
    await expect(before.right).toBe('16px')
  },
}

export const DarkRightToLeft: Story = { globals: DARK_RTL }
