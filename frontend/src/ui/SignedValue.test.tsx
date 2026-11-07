import { render, screen } from '@testing-library/react'
import i18n from '../i18n'
import { SignedValue } from './SignedValue'

const MINUS = '−'

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('SignedValue', () => {
  it('shows a loss with an icon, a minus sign and words, not colour alone', () => {
    const { container } = render(<SignedValue value="-1234.5" unit="USD" />)
    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveAttribute('data-sign', 'loss')
    expect(root.querySelector('svg[data-sign-icon]')).not.toBeNull()
    expect(screen.getByText(`${MINUS}$1,234.50`)).toBeInTheDocument()
    expect(screen.getByText('loss')).toHaveClass('sr-only')
    expect(root).toHaveClass('text-loss-ink')
  })

  it('shows a gain with an icon and "+"', () => {
    const { container } = render(<SignedValue value="2104.18" unit="USD" />)
    expect(container.querySelector('svg[data-sign-icon]')).not.toBeNull()
    expect(screen.getByText('+$2,104.18')).toBeInTheDocument()
    expect(screen.getByText('gain')).toBeInTheDocument()
  })

  it('gives zero no sign and no icon', () => {
    const { container } = render(<SignedValue value="0" unit="percent" />)
    expect(container.querySelector('svg')).toBeNull()
    expect(screen.getByText('0.00%')).toBeInTheDocument()
  })

  it('renders null as a dash with no sign or icon, and reads out its reason', () => {
    const { container } = render(<SignedValue value={null} unit="USD" nullTip="No starting equity yet" />)
    expect(container.querySelector('svg')).toBeNull()
    const dash = screen.getByText('—')
    expect(dash).toHaveAttribute('aria-label', 'No starting equity yet')
    expect(dash).toHaveAttribute('tabindex', '0')
  })

  it('keeps Western digits and the leading sign in Arabic, with Arabic screen-reader words', async () => {
    await i18n.changeLanguage('ar')
    render(<SignedValue value="-1234.5" unit="USD" />)
    expect(screen.getByText(`${MINUS}$1,234.50`)).toBeInTheDocument()
    expect(screen.getByText('خسارة')).toBeInTheDocument()
  })
})
