import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { act } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import { AccountMenu, LanguageButton } from './Prefs'

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

/**
 * ATL-225: the language switch's accessible name must start with the same text it shows on
 * screen — the target language's own name, in its own script — instead of a separate aria-label
 * translating "Switch to …" in the current language (which never shared a script with it).
 */
describe('LanguageButton', () => {
  it('names the button starting with "العربية", its visible label, while in English', () => {
    render(<LanguageButton />)
    const button = screen.getByTestId('language-switch')
    expect(button).toHaveTextContent('العربية')
    expect(button).toHaveAccessibleName(/^العربية/)
  })

  it('names the button starting with "English", its visible label, while in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    render(<LanguageButton />)
    const button = screen.getByTestId('language-switch')
    expect(button).toHaveTextContent('English')
    expect(button).toHaveAccessibleName(/^English/)
  })

  it('still flips the language on click', async () => {
    const user = userEvent.setup()
    render(<LanguageButton />)
    await user.click(screen.getByTestId('language-switch'))
    await waitFor(() => expect(i18n.language).toBe('ar'))
  })
})
