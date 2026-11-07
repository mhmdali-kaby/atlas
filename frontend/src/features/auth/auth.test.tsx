import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../app/AppShell'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { LoginPage } from './LoginPage'
import { RequireAuth } from './RequireAuth'
import { IDLE_LOCK_MS } from './session'

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Owner', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const PASSWORD = 'correct horse battery staple'

/** A fake Laravel: Sanctum session + the ATL-2 auth endpoints. */
function fakeBackend({ ownerExists = true, signedIn = false } = {}) {
  const state = { signedIn, logouts: 0 }
  const fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(API_URL, '')
    const method = (init.method ?? 'GET').toUpperCase()
    const reply = (status: number, body?: unknown) =>
      new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    if (path === '/sanctum/csrf-cookie') return reply(204)
    if (path === '/api/auth/status') return reply(200, { owner_exists: ownerExists })
    if (path === '/api/auth/me') return state.signedIn ? reply(200, { data: OWNER }) : reply(401, { message: 'Unauthenticated.' })
    if (path === '/api/auth/login' && method === 'POST') {
      const body = JSON.parse(String(init.body)) as { email: string; password: string }
      if (body.email === OWNER.email && body.password === PASSWORD) {
        state.signedIn = true
        return reply(200, { data: OWNER })
      }
      const msg = 'These credentials do not match our records.'
      return reply(422, { message: msg, errors: { email: [msg] } })
    }
    if (path === '/api/auth/logout' && method === 'POST') {
      state.signedIn = false
      state.logouts += 1
      return reply(204)
    }
    return reply(404, { message: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return { state, fetch }
}

function renderApp(initialPath: string) {
  const routes: RouteObject[] = [
    { path: '/login', element: <LoginPage /> },
    {
      path: '/',
      element: (
        <RequireAuth>
          <AppShell />
        </RequireAuth>
      ),
      children: [{ path: 'portfolio', element: <h1>Portfolio page</h1> }],
    },
  ]
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(async () => {
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

describe('sign-in', () => {
  it('redirects /portfolio to /login while signed out, then returns to /portfolio after sign-in', async () => {
    const { fetch } = fakeBackend()
    const router = renderApp('/portfolio')

    await screen.findByRole('button', { name: 'Sign in' })
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toBe('?next=%2Fportfolio')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), OWNER.email)
    await user.type(screen.getByLabelText('Password'), `${PASSWORD}{Enter}`)

    expect(await screen.findByRole('heading', { name: 'Portfolio page' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/portfolio')
    const paths = fetch.mock.calls.map(([u]) => String(u).replace(API_URL, ''))
    expect(paths.indexOf('/sanctum/csrf-cookie')).toBeLessThan(paths.indexOf('/api/auth/login'))
  })

  it('shows the wrong-password error under the password field and keeps the email', async () => {
    fakeBackend()
    renderApp('/login')
    const user = userEvent.setup()
    const email = await screen.findByLabelText('Email')
    await user.type(email, OWNER.email)
    await user.type(screen.getByLabelText('Password'), 'not the password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    const password = screen.getByLabelText('Password')
    await waitFor(() => expect(password).toHaveAttribute('aria-invalid', 'true'))
    expect(password).toHaveAccessibleDescription('The email or password is wrong.')
    expect(password).toHaveValue('')
    expect(screen.getByLabelText('Email')).toHaveValue(OWNER.email)
    expect(password).toHaveFocus()
  })

  it('asks for the email and password before calling the server', async () => {
    const { fetch } = fakeBackend()
    renderApp('/login')
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Sign in' }))
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Enter your email.')
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription('Enter your password.')
    expect(fetch.mock.calls.some(([u]) => String(u).endsWith('/api/auth/login'))).toBe(false)
  })

  it('shows the create-owner instructions when no owner exists', async () => {
    fakeBackend({ ownerExists: false })
    renderApp('/login')
    expect(await screen.findByText('./atlas create-owner')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument()
  })

  it('goes back to /login when any request returns 401', async () => {
    const { state } = fakeBackend({ signedIn: true })
    const router = renderApp('/portfolio')
    await screen.findByRole('heading', { name: 'Portfolio page' })
    state.signedIn = false
    const { api } = await import('../../lib/api')
    await act(async () => {
      await api.get('/api/auth/me').catch(() => undefined)
    })
    await screen.findByRole('button', { name: 'Sign in' })
    expect(router.state.location.pathname).toBe('/login')
  })
})

describe('idle lock', () => {
  it('signs out after 15 minutes without input and shows the idle-lock message', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { state } = fakeBackend({ signedIn: true })
    const router = renderApp('/portfolio')
    await screen.findByRole('heading', { name: 'Portfolio page' })

    // Input restarts the clock.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_LOCK_MS - 60_000)
    })
    fireEvent.keyDown(window, { key: 'a' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_LOCK_MS - 60_000)
    })
    expect(state.logouts).toBe(0)
    expect(screen.getByRole('heading', { name: 'Portfolio page' })).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    await screen.findByText('Locked after 15 minutes idle')
    expect(state.logouts).toBe(1)
    expect(state.signedIn).toBe(false)
    expect(router.state.location.pathname).toBe('/login')
    expect(new URLSearchParams(router.state.location.search).get('reason')).toBe('idle')
    expect(new URLSearchParams(router.state.location.search).get('next')).toBe('/portfolio')
  })
})

describe('Arabic', () => {
  it('renders the login page fully in Arabic, right to left', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderApp('/login?reason=idle')
    await screen.findByRole('button', { name: 'تسجيل الدخول' })
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.lang).toBe('ar')

    const text = document.body.textContent ?? ''
    const labels = [...document.body.querySelectorAll('[aria-label],[title]')]
      .map((el) => `${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`)
      .join(' ')
    // Only the server address, the shell command and the language switch's "English"
    // (each language names itself, design: Shell) are Latin by design.
    const latin = `${text} ${labels}`.replace(/localhost:8000|\.\/atlas (up|create-owner)|English/g, '').match(/[A-Za-z]{2,}/g)
    expect(latin).toBeNull()
    expect(screen.getByText('قُفل أطلس بعد 15 دقيقة من الخمول')).toBeInTheDocument()
    expect(screen.getByLabelText('البريد الإلكتروني')).toBeInTheDocument()
  })
})
