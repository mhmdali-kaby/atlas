import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render as rtlRender, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { act, type ReactElement } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { AccountMenu, LanguageButton, MoreMenu } from './Prefs'

/** The toolbar controls write preferences (ATL-227) and the account menu navigates: both need providers. */
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  })
}

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
 * ATL-224: More (⋯) holds, in order, the page's own toolbar actions, language, appearance and the
 * account, and each item runs exactly what its toolbar control runs.
 */
describe('MoreMenu', () => {
  function Page({ onReplay, onInspector, pressed }: { onReplay: () => void; onInspector: () => void; pressed: boolean }) {
    return (
      <div data-slot="toolbar-actions">
        <button type="button" data-menu-label="Replay" onClick={onReplay}>
          <svg data-testid="replay-svg" />
        </button>
        <button type="button" data-menu-label="Show or hide the inspector" aria-pressed={pressed} onClick={onInspector}>
          <svg />
        </button>
      </div>
    )
  }

  it('lists the palette, the page actions, language, appearance and the account, in that order', async () => {
    const user = userEvent.setup()
    render(
      <>
        <Page onReplay={() => {}} onInspector={() => {}} pressed />
        <MoreMenu pageTitle="War Room" user={{ name: 'Mohammed Ali', email: 'owner@atlas.test' }} onSignOut={() => {}} />
      </>,
    )
    await user.click(screen.getByRole('button', { name: 'More' }))
    await screen.findByRole('menu')
    const items = Array.from(document.querySelectorAll('[role^="menuitem"]')).map((el) => el.textContent?.trim())
    // ATL-249: the ⌘K palette's door comes first (design v22 §6).
    expect(items).toEqual([expect.stringMatching(/^Search or jump to…(⌘K|Ctrl K)$/), 'Replay', 'Show or hide the inspector', 'العربية, switch language', 'System', 'Light', 'Dark', 'Sign out'])
    expect(screen.getByRole('menuitemcheckbox', { name: 'Show or hide the inspector' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('group', { name: 'War Room' })).toBeInTheDocument()
    expect(screen.getByText('owner@atlas.test')).toBeInTheDocument()
  })

  it('runs the page control when its item is chosen', async () => {
    const user = userEvent.setup()
    const onReplay = vi.fn()
    const onInspector = vi.fn()
    render(
      <>
        <Page onReplay={onReplay} onInspector={onInspector} pressed={false} />
        <MoreMenu pageTitle="War Room" user={undefined} onSignOut={() => {}} />
      </>,
    )
    await user.click(screen.getByRole('button', { name: 'More' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Replay' }))
    expect(onReplay).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'More' }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Show or hide the inspector' }))
    expect(onInspector).toHaveBeenCalledTimes(1)
  })

  it('signs out from the account section', async () => {
    const user = userEvent.setup()
    const onSignOut = vi.fn()
    render(<MoreMenu pageTitle="Positions" user={{ name: 'Mohammed Ali', email: 'owner@atlas.test' }} onSignOut={onSignOut} />)
    await user.click(screen.getByRole('button', { name: 'More' }))
    expect(screen.queryByRole('group', { name: 'Positions' })).toBeNull()
    await user.click(await screen.findByRole('menuitem', { name: 'Sign out' }))
    expect(onSignOut).toHaveBeenCalledTimes(1)
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
