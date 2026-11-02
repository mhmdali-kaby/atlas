import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { act } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import { AccountMenu } from './Prefs'

/**
 * ATL-223: the account button's accessible name must contain the visible initials, so axe's
 * label-content-name-mismatch no longer flags it (it used to be aria-label="Account"/"الحساب" alone).
 */
afterEach(async () => {
  await act(() => i18n.changeLanguage('en'))
})

describe('AccountMenu', () => {
  it('names the button with its visible initials and the word "Account"', () => {
    render(<AccountMenu name="Mohammed Ali" email="owner@atlas.test" onSignOut={() => {}} />)
    const button = screen.getByRole('button')
    expect(button).toHaveAccessibleName(/^MA\b.*Account/)
  })

  it('names the button with its visible initials and the word "الحساب" in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    render(<AccountMenu name="Mohammed Ali" email="owner@atlas.test" onSignOut={() => {}} />)
    const button = screen.getByRole('button')
    const name = button.getAttribute('aria-label') ?? ''
    expect(name).toContain('MA')
    expect(name).toContain('الحساب')
  })

  it('still opens the account menu on click', async () => {
    const user = userEvent.setup()
    render(<AccountMenu name="Mohammed Ali" email="owner@atlas.test" onSignOut={() => {}} />)
    await user.click(screen.getByRole('button'))
    expect(await screen.findByText('owner@atlas.test')).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument()
  })
})
