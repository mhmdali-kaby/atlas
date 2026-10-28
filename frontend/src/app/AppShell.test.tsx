import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Briefcase, Plug } from 'lucide-react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RequireAuth } from '../features/auth/RequireAuth'
import { LoginPage } from '../features/auth/LoginPage'
import i18n from '../i18n'
import { LANGUAGE_STORAGE_KEY, readStoredLanguage } from '../i18n/language'
import { API_URL, resetCsrfForTests } from '../lib/api'
import { THEME_STORAGE_KEY } from '../theme/theme'
import { initTheme, useTheme } from '../theme/useTheme'
import { AppShell } from './AppShell'
import { activeNavEntry, collectNavEntries, type NavEntry } from './feature'
import { RiskFooter } from './shell/RiskFooter'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }

const NAV: NavEntry[] = [
  { path: '/portfolio', labelKey: 'shell:test.overview', order: 10, section: 'portfolio', icon: Briefcase },
  { path: '/portfolio/positions', labelKey: 'shell:test.positions', order: 20, section: 'portfolio' },
  { path: '/settings/connections', labelKey: 'shell:test.connections', order: 100, section: 'settings', icon: Plug },
]

i18n.addResourceBundle('en', 'shell', { test: { overview: 'Overview', positions: 'Positions', connections: 'Connections' } }, true, false)
i18n.addResourceBundle('ar', 'shell', { test: { overview: 'نظرة عامة', positions: 'المراكز', connections: 'الاتصالات' } }, true, false)

type Listener = (event: MediaQueryListEvent) => void
function mockSystemDark(initial: boolean) {
  let matches = initial
  const listeners = new Set<Listener>()
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return query.includes('prefers-color-scheme') ? matches : false
    },
    media: query,
    addEventListener: (_: string, l: Listener) => listeners.add(l),
    removeEventListener: (_: string, l: Listener) => listeners.delete(l),
  })) as unknown as typeof window.matchMedia
  return {
    set(next: boolean) {
      matches = next
      for (const l of listeners) l({ matches: next } as MediaQueryListEvent)
    },
  }
}

function fakeBackend() {
  const state = { signedIn: true }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, body?: unknown) =>
        new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return state.signedIn ? reply(200, { data: OWNER }) : reply(401, { message: 'Unauthenticated.' })
      if (path === '/api/auth/logout' && method === 'POST') {
        state.signedIn = false
        return reply(204)
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderShell(initialEntries: string[]) {
  const routes: RouteObject[] = [
    { path: '/login', element: <LoginPage /> },
    {
      path: '/',
      element: (
        <RequireAuth>
          <AppShell nav={NAV} />
        </RequireAuth>
      ),
      children: [
        { path: 'portfolio', element: <p>Protected overview</p> },
        { path: 'portfolio/positions', element: <p>Protected positions</p> },
        { path: 'settings/connections', element: <p>Protected connections</p> },
      ],
    },
  ]
  const router = createMemoryRouter(routes, { initialEntries, initialIndex: initialEntries.length - 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

const html = document.documentElement

beforeEach(async () => {
  resetCsrfForTests()
  localStorage.clear()
  mockSystemDark(false)
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
  localStorage.clear()
})

describe('risk footer', () => {
  it('names the environment in English and Arabic', async () => {
    render(<RiskFooter />)
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Automated trading can lose money. Demo/Live: Demo')
    await act(() => i18n.changeLanguage('ar'))
    expect(screen.getByRole('contentinfo')).toHaveTextContent('قد يؤدي التداول الآلي إلى خسارة المال. تجريبي/حقيقي: تجريبي')
  })

  it('is on the login page and on every shell page', async () => {
    fakeBackend()
    const router = renderShell(['/portfolio'])
    expect(await screen.findByText('Protected overview')).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Demo/Live: Demo')

    await act(() => router.navigate('/settings/connections'))
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Demo/Live: Demo')
  })
})

describe('sidebar', () => {
  it('lists the registered entries under Portfolio and Settings and marks the current page', async () => {
    fakeBackend()
    renderShell(['/portfolio/positions'])
    const sidebar = await screen.findByRole('complementary', { name: 'Sidebar' })

    const portfolio = within(sidebar).getByRole('group', { name: 'Portfolio' })
    const settings = within(sidebar).getByRole('group', { name: 'Settings' })
    expect(within(portfolio).getAllByRole('link').map((a) => a.textContent)).toEqual(['Overview', 'Positions'])
    expect(within(settings).getAllByRole('link').map((a) => a.textContent)).toEqual(['Connections'])

    // The longest matching path wins: Positions, not Overview.
    expect(within(sidebar).getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
    expect(within(sidebar).getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Positions')
  })

  it('moves between items with Tab and the arrow keys, and navigates on Enter', async () => {
    fakeBackend()
    const user = userEvent.setup()
    const router = renderShell(['/portfolio'])
    const sidebar = await screen.findByRole('complementary', { name: 'Sidebar' })
    const [overview, positions, connections] = within(sidebar).getAllByRole('link') as [HTMLElement, HTMLElement, HTMLElement]

    overview.focus()
    await user.keyboard('{ArrowDown}')
    expect(positions).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(connections).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(overview).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(connections).toHaveFocus()
    await user.keyboard('{Home}')
    expect(overview).toHaveFocus()
    await user.tab()
    expect(positions).toHaveFocus()

    await user.keyboard('{Enter}')
    await waitFor(() => expect(router.state.location.pathname).toBe('/portfolio/positions'))
    expect(within(sidebar).getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
  })
})

describe('language switch', () => {
  it('flips <html> to rtl/ar at once, remembers it, and flips back', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderShell(['/portfolio'])
    await screen.findByText('Protected overview')

    await user.click(screen.getByRole('button', { name: 'Switch to Arabic' }))
    expect(html.dir).toBe('rtl')
    expect(html.lang).toBe('ar')
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('ar')
    expect(readStoredLanguage()).toBe('ar')
    expect(screen.getByRole('complementary', { name: 'الشريط الجانبي' })).toBeInTheDocument()
    expect(screen.getByRole('contentinfo')).toHaveTextContent('تجريبي/حقيقي: تجريبي')

    await user.click(screen.getByRole('button', { name: 'التبديل إلى الإنجليزية' }))
    expect(html.dir).toBe('ltr')
    expect(html.lang).toBe('en')
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('en')
  })

  it('is on the login page too', async () => {
    fakeBackend().signedIn = false
    const user = userEvent.setup()
    renderShell(['/login'])
    await user.click(await screen.findByRole('button', { name: 'Switch to Arabic' }))
    expect(html.dir).toBe('rtl')
    expect(screen.getByRole('contentinfo')).toHaveTextContent('تجريبي/حقيقي: تجريبي')
  })

  it('treats a missing or unknown stored value as English', () => {
    expect(readStoredLanguage()).toBe('en')
    localStorage.setItem(LANGUAGE_STORAGE_KEY, 'fr')
    expect(readStoredLanguage()).toBe('en')
  })
})

describe('appearance menu', () => {
  it('applies Dark, Light and System at once and remembers them', async () => {
    const os = mockSystemDark(false)
    initTheme()
    fakeBackend()
    const user = userEvent.setup()
    renderShell(['/portfolio'])
    await screen.findByText('Protected overview')

    const choose = async (name: string) => {
      await user.click(screen.getByRole('button', { name: 'Appearance' }))
      await user.click(await screen.findByRole('menuitemradio', { name }))
    }

    await choose('Dark')
    expect(html.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    await choose('Light')
    expect(html.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')

    await choose('System')
    expect(useTheme.getState().choice).toBe('system')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
    act(() => os.set(true))
    expect(html.classList.contains('dark')).toBe(true)
    act(() => os.set(false))
    expect(html.classList.contains('dark')).toBe(false)

    await user.click(screen.getByRole('button', { name: 'Appearance' }))
    expect(await screen.findByRole('menuitemradio', { name: 'System' })).toHaveAttribute('aria-checked', 'true')
  })
})

describe('sign out', () => {
  it('returns to /login, and Back does not show protected content', async () => {
    const backend = fakeBackend()
    const user = userEvent.setup()
    const router = renderShell(['/portfolio', '/portfolio/positions'])
    expect(await screen.findByText('Protected positions')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Account' }))
    expect(await screen.findByText(OWNER.email)).toBeInTheDocument()
    await user.click(screen.getByRole('menuitem', { name: 'Sign out' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe('')
    expect(backend.signedIn).toBe(false)
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()

    await act(() => router.navigate(-1))
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(screen.queryByText('Protected overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Protected positions')).not.toBeInTheDocument()
  })
})

describe('nav helpers', () => {
  it('collects single and multiple entries in order', () => {
    const [a, b, c] = NAV as [NavEntry, NavEntry, NavEntry]
    expect(collectNavEntries([{ routes: [], nav: c }, { routes: [], nav: [b, a] }, { routes: [] }])).toEqual([a, b, c])
  })

  it('picks the longest matching path, never a sibling prefix', () => {
    expect(activeNavEntry(NAV, '/portfolio')?.path).toBe('/portfolio')
    expect(activeNavEntry(NAV, '/portfolio/positions/BTC')?.path).toBe('/portfolio/positions')
    expect(activeNavEntry(NAV, '/portfolios')).toBeUndefined()
    expect(activeNavEntry(NAV, '/')).toBeUndefined()
  })
})

describe('index.html no-flash language', () => {
  const source = (() => {
    const file = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')
    const match = /<script id="atlas-theme-init">([\s\S]*?)<\/script>/.exec(file)
    if (!match?.[1]) throw new Error('index.html lost its atlas-theme-init script')
    return match[1]
  })()

  it.each([
    ['ar', 'ar', 'rtl'],
    ['en', 'en', 'ltr'],
    [null, 'en', 'ltr'],
    ['fr', 'en', 'ltr'],
  ] as const)('stored %s sets lang=%s dir=%s, the same as readStoredLanguage', (stored, lang, dir) => {
    if (stored) localStorage.setItem(LANGUAGE_STORAGE_KEY, stored)
    new Function(source)()
    expect(html.lang).toBe(lang)
    expect(html.dir).toBe(dir)
    expect(readStoredLanguage()).toBe(lang)
  })
})
