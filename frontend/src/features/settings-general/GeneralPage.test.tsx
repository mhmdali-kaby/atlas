import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import i18n from '../../i18n'
import { API_URL } from '../../lib/api'
import { DEFAULT_OWNER_TZ, setOwnerZone } from '../../lib/ownerZone'
import { useTheme } from '../../theme/useTheme'
import { formatTime } from '../warroom/time'
import { GeneralPage } from './GeneralPage'

/* ATL-227: Settings → General against a faked ATL-226 /api/me/preferences. */

const strip = (s: string | null | undefined) => (s ?? '').replace(/[⁦-⁩]/g, '')
// 28 Oct 2026, 14:05 UTC: 17:05 in Baghdad, 14:05 in London.
const NOW = Date.UTC(2026, 9, 28, 14, 5, 0)

type Reply = { status: number; body?: unknown } | 'network'

function backend(prefs = { name: 'Mohammed Ali', locale: 'en', timezone: 'Asia/Baghdad', theme: 'system' }) {
  const state = {
    prefs: { ...prefs } as Record<string, string>,
    patches: [] as { body: Record<string, string>; lang: string | null }[],
    next: null as Reply | null,
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, body?: unknown) =>
        new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/me') return reply(200, { data: { id: 'u1', name: state.prefs.name, email: 'owner@atlas.test', locale: 'en', theme: 'system' } })
      if (path === '/api/me/preferences' && method === 'GET') return reply(200, { data: state.prefs })
      if (path === '/api/me/preferences' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as Record<string, string>
        state.patches.push({ body, lang: new Headers(init.headers).get('Accept-Language') })
        const r = state.next
        state.next = null
        if (r === 'network') throw new TypeError('Failed to fetch')
        if (r) return reply(r.status, r.body)
        state.prefs = { ...state.prefs, ...body }
        return reply(200, { data: state.prefs })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GeneralPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(async () => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  setOwnerZone(DEFAULT_OWNER_TZ)
  useTheme.getState().setChoice('system')
  await act(() => i18n.changeLanguage('en'))
})

describe('Settings → General', () => {
  it('shows the saved name, language, time zone and theme', async () => {
    backend({ name: 'Mohammed Ali', locale: 'en', timezone: 'Europe/London', theme: 'dark' })
    renderPage()
    expect(await screen.findByLabelText('Name')).toHaveValue('Mohammed Ali')
    expect(screen.getByTestId('gn-mono')).toHaveTextContent('MA')
    expect(within(screen.getByTestId('gn-lang')).getByRole('radio', { name: 'English' })).toBeChecked()
    expect(screen.getByTestId('zone-button')).toHaveTextContent('London')
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('owner@atlas.test')).toBeInTheDocument()
  })

  it('switching to Arabic flips the whole app to RTL at once and saves only the locale', async () => {
    const state = backend()
    const user = userEvent.setup()
    renderPage()
    await user.click(within(await screen.findByTestId('gn-lang')).getByRole('radio', { name: 'العربية' }))
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.lang).toBe('ar')
    await waitFor(() => expect(state.patches).toEqual([{ body: { locale: 'ar' }, lang: 'en' }]))
    expect(await screen.findByRole('heading', { name: 'عام' })).toBeInTheDocument()
  })

  it('a refused save (422) puts the theme back and shows the server message beside it', async () => {
    const state = backend()
    state.next = { status: 422, body: { message: 'The selected appearance is invalid.', errors: { theme: ['The selected appearance is invalid.'] } } }
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('radio', { name: 'Dark' }))
    expect(await screen.findByTestId('gn-err-theme')).toHaveTextContent('The selected appearance is invalid.')
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'true')
    expect(useTheme.getState().choice).toBe('system')
  })

  it('an unanswered save puts the time zone back and says so', async () => {
    const state = backend()
    state.next = 'network'
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByTestId('zone-button'))
    await user.type(screen.getByTestId('zone-search'), 'London')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(state.patches).toEqual([{ body: { timezone: 'Europe/London' }, lang: 'en' }]))
    expect(await screen.findByTestId('gn-err-timezone')).toHaveTextContent('Couldn’t save. Atlas’s server didn’t answer, so it’s back to Baghdad.')
    expect(screen.getByTestId('zone-button')).toHaveTextContent('Baghdad')
    expect(formatTime(NOW, 'en')).toBe('17:05')
  })

  it('a refused name shows the server message and goes back to the saved name', async () => {
    const state = backend()
    state.next = { status: 422, body: { message: 'x', errors: { name: ['The name must not be greater than 80 characters.'] } } }
    const user = userEvent.setup()
    renderPage()
    const field = await screen.findByLabelText('Name')
    await user.clear(field)
    await user.type(field, 'Someone Else{Enter}')
    expect(await screen.findByTestId('gn-err-name')).toHaveTextContent('The name must not be greater than 80 characters.')
    expect(field).toHaveValue('Mohammed Ali')
  })

  it('an empty name is not sent', async () => {
    const state = backend()
    const user = userEvent.setup()
    renderPage()
    const field = await screen.findByLabelText('Name')
    await user.clear(field)
    await user.keyboard('{Enter}')
    expect(await screen.findByTestId('gn-err-name')).toHaveTextContent('Enter your name.')
    expect(state.patches).toEqual([])
  })

  it('choosing Europe/London moves every displayed time to London time', async () => {
    const state = backend()
    const user = userEvent.setup()
    renderPage()
    expect(strip((await screen.findByTestId('gn-preview')).textContent)).toContain('17:05')
    await user.click(screen.getByTestId('zone-button'))
    await user.type(screen.getByTestId('zone-search'), 'lond')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(state.patches).toEqual([{ body: { timezone: 'Europe/London' }, lang: 'en' }]))
    expect(screen.getByTestId('zone-button')).toHaveTextContent('London')
    // the rendered preview timestamp, through the War Room's formatter
    await waitFor(() => expect(strip(screen.getByTestId('gn-preview').textContent)).toContain('14:05'))
    expect(formatTime(NOW, 'en')).toBe('14:05')
    expect(strip(screen.getByTestId('gn-team-cap').textContent)).toBe(
      'The team keeps Baghdad time. London is 3 hours behind, so the watchlist reaches you at 05:00.',
    )
  })
})
