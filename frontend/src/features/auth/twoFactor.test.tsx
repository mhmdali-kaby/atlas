import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { useToasts } from '../../ui'
import { LoginPage } from './LoginPage'
import { RequireAuth } from './RequireAuth'

/* ATL-248: the login's two-factor code step against a fake ATL-246 API. */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const PASSWORD = 'correct horse battery staple'
const GOOD_CODE = '482913'
const GOOD_RECOVERY = 'k3m9q-7xw2p'

type ChallengeAnswer = 'ok' | 'wrong' | 'expired' | 'throttled'

interface Options {
  twoFactor?: boolean
  pending?: boolean
  /** Forces the challenge's answer, whatever is sent. */
  answer?: ChallengeAnswer
  codesLeft?: number
}

function fakeBackend({ twoFactor = true, pending = false, answer, codesLeft = 7 }: Options = {}) {
  const state = { signedIn: false, pending, challenges: [] as Record<string, string>[], logins: 0 }
  const fetch = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace(API_URL, '')
    const method = (init.method ?? 'GET').toUpperCase()
    const reply = (status: number, body?: unknown, headers: Record<string, string> = {}) =>
      new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
    if (path === '/sanctum/csrf-cookie') return reply(204)
    if (path === '/api/auth/status') return reply(200, { owner_exists: true, two_factor_pending: state.pending })
    if (path === '/api/auth/me') return state.signedIn ? reply(200, { data: OWNER }) : reply(401, { message: 'Unauthenticated.' })
    if (path === '/api/me/two-factor') return reply(200, { data: { enabled: true, confirmed_at: '2026-11-06T09:12:03Z', recovery_codes_left: codesLeft } })
    if (path === '/api/auth/login' && method === 'POST') {
      state.logins += 1
      const body = JSON.parse(String(init.body)) as { email: string; password: string }
      if (body.email !== OWNER.email || body.password !== PASSWORD) {
        const msg = 'These credentials do not match our records.'
        return reply(422, { message: msg, errors: { email: [msg] } })
      }
      if (!twoFactor) {
        state.signedIn = true
        return reply(200, { data: OWNER })
      }
      state.pending = true
      return reply(200, { two_factor: true })
    }
    if (path === '/api/auth/two-factor-challenge' && method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, string>
      state.challenges.push(body)
      const field = 'recovery_code' in body ? 'recovery_code' : 'code'
      const right = body.code === GOOD_CODE || body.recovery_code === GOOD_RECOVERY
      const outcome: ChallengeAnswer = answer ?? (!state.pending ? 'expired' : right ? 'ok' : 'wrong')
      if (outcome === 'throttled') return reply(429, { message: 'Too Many Attempts.' }, { 'Retry-After': '30' })
      if (outcome === 'expired') {
        state.pending = false
        const msg = 'Your sign-in has expired. Enter your email and password again.'
        return reply(422, { message: msg, error: 'challenge_expired', errors: { [field]: [msg] } })
      }
      if (outcome === 'wrong') {
        const msg = 'The code is incorrect or has already been used.'
        return reply(422, { message: msg, error: 'code_wrong', errors: { [field]: [msg] } })
      }
      state.pending = false
      state.signedIn = true
      return reply(200, { data: OWNER })
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
      element: <RequireAuth>{null}</RequireAuth>,
    },
    {
      path: '/portfolio',
      element: (
        <RequireAuth>
          <h1>Portfolio page</h1>
        </RequireAuth>
      ),
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

async function signInWithPassword(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Email'), OWNER.email)
  await user.type(screen.getByLabelText('Password'), `${PASSWORD}{Enter}`)
}

beforeEach(async () => {
  resetCsrfForTests()
  useToasts.setState({ toasts: [] })
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

const challengeBodies = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls
    .filter(([u]) => String(u).endsWith('/api/auth/two-factor-challenge'))
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, string>)

describe('two-factor code step', () => {
  it('after a right password asks for the code, and a valid code lands where a normal sign-in does', async () => {
    const { fetch } = fakeBackend()
    const router = renderApp('/portfolio')
    const user = userEvent.setup()
    await signInWithPassword(user)

    const field = await screen.findByLabelText('6-digit code')
    expect(screen.getByText('Enter the 6-digit code from your authenticator app.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(field).toHaveAttribute('autocomplete', 'one-time-code')
    expect(field).toHaveAttribute('inputmode', 'numeric')
    expect(field).toHaveAttribute('dir', 'ltr')
    await waitFor(() => expect(field).toHaveFocus())

    // Six digits auto-submit; a pasted "482 913" loses its space.
    await user.paste('482 913')
    expect(await screen.findByRole('heading', { name: 'Portfolio page' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/portfolio')
    expect(challengeBodies(fetch)).toEqual([{ code: GOOD_CODE }])
  })

  it('a wrong code shows the error, clears the cells and keeps the code step', async () => {
    const { fetch } = fakeBackend()
    renderApp('/login')
    const user = userEvent.setup()
    await signInWithPassword(user)
    const field = await screen.findByLabelText('6-digit code')
    await user.type(field, '111111')

    expect(await screen.findByRole('alert')).toHaveTextContent('That code didn’t work. Type the code your app shows now.')
    expect(field).toHaveValue('')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    await waitFor(() => expect(field).toHaveFocus())
    expect(challengeBodies(fetch)).toEqual([{ code: '111111' }])
  })

  it('"Use a recovery code instead" swaps the field and posts recovery_code, then says how many are left', async () => {
    const { fetch } = fakeBackend({ codesLeft: 2 })
    const router = renderApp('/portfolio')
    const user = userEvent.setup()
    await signInWithPassword(user)
    await screen.findByLabelText('6-digit code')

    await user.click(screen.getByRole('button', { name: 'Use a recovery code instead' }))
    expect(screen.queryByLabelText('6-digit code')).not.toBeInTheDocument()
    expect(screen.getByText('Enter one of the recovery codes you saved.')).toBeInTheDocument()
    const recovery = screen.getByLabelText('Recovery code')
    expect(recovery).toHaveAttribute('dir', 'ltr')
    await user.type(recovery, `${GOOD_RECOVERY}{Enter}`)

    expect(await screen.findByRole('heading', { name: 'Portfolio page' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/portfolio')
    expect(challengeBodies(fetch)).toEqual([{ recovery_code: GOOD_RECOVERY }])
    await waitFor(() =>
      expect(useToasts.getState().toasts).toEqual([
        expect.objectContaining({ tone: 'warning', title: 'Signed in with a recovery code', body: '2 of 8 codes left. Make new ones in Settings → General.' }),
      ]),
    )
  })

  it('a wrong recovery code shows its own message and keeps the recovery field', async () => {
    fakeBackend()
    renderApp('/login')
    const user = userEvent.setup()
    await signInWithPassword(user)
    await user.click(await screen.findByRole('button', { name: 'Use a recovery code instead' }))
    await user.type(screen.getByLabelText('Recovery code'), 'aaaaa-bbbbb{Enter}')
    expect(await screen.findByText('That recovery code didn’t work. It may have been used already.')).toBeInTheDocument()
    expect(screen.getByLabelText('Recovery code')).toHaveValue('aaaaa-bbbbb')
  })

  it('an expired challenge returns to the password step with the expiry message', async () => {
    fakeBackend({ answer: 'expired' })
    renderApp('/login')
    const user = userEvent.setup()
    await signInWithPassword(user)
    await user.type(await screen.findByLabelText('6-digit code'), GOOD_CODE)

    expect(await screen.findByText('Sign-in timed out')).toBeInTheDocument()
    expect(screen.getByText('The code has to follow your password within 5 minutes. Enter your password again.')).toBeInTheDocument()
    expect(screen.queryByLabelText('6-digit code')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveValue(OWNER.email)
    expect(screen.getByLabelText('Password')).toHaveValue('')
    await waitFor(() => expect(screen.getByLabelText('Password')).toHaveFocus())
  })

  it('a 429 shows the wait message and disables the field', async () => {
    fakeBackend({ answer: 'throttled' })
    renderApp('/login')
    const user = userEvent.setup()
    await signInWithPassword(user)
    const field = await screen.findByLabelText('6-digit code')
    await user.type(field, GOOD_CODE)

    expect(await screen.findByText('Too many tries. Try again in 30 seconds.')).toBeInTheDocument()
    expect(field).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Verify' })).toBeDisabled()
  })

  it('a reload while a challenge is pending opens straight on the code step', async () => {
    const { fetch } = fakeBackend({ pending: true })
    renderApp('/login')
    expect(await screen.findByLabelText('6-digit code')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(fetch.mock.calls.some(([u]) => String(u).endsWith('/api/auth/login'))).toBe(false)
  })

  it('"Back to password" returns to the password step', async () => {
    fakeBackend()
    renderApp('/login')
    const user = userEvent.setup()
    await signInWithPassword(user)
    await screen.findByLabelText('6-digit code')
    await user.click(screen.getByRole('button', { name: 'Back to password' }))
    expect(screen.getByLabelText('Password')).toHaveValue('')
    expect(screen.getByLabelText('Email')).toHaveValue(OWNER.email)
  })

  it('an owner without 2FA still signs in with the password alone', async () => {
    const { fetch } = fakeBackend({ twoFactor: false })
    renderApp('/portfolio')
    const user = userEvent.setup()
    await signInWithPassword(user)
    expect(await screen.findByRole('heading', { name: 'Portfolio page' })).toBeInTheDocument()
    expect(challengeBodies(fetch)).toEqual([])
  })

  it('in Arabic the step is right-to-left while the code cells and recovery field stay left-to-right', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend({ pending: true })
    renderApp('/login')
    const field = await screen.findByLabelText('رمز من 6 أرقام')
    expect(field).toHaveAttribute('dir', 'ltr')
    expect(screen.getByText('أدخل الرمز المكوّن من 6 أرقام من تطبيق المصادقة.')).toBeInTheDocument()
    expect(screen.getByText('تتغيّر الرموز كل 30 ثانية.')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'استخدم رمز استرداد بدلًا من ذلك' }))
    expect(screen.getByLabelText('رمز الاسترداد')).toHaveAttribute('dir', 'ltr')
    await user.click(screen.getByRole('button', { name: 'فقدت هاتفك ورموزك؟' }))
    expect(screen.getByText('./atlas reset-two-factor').closest('[dir]')).toHaveAttribute('dir', 'ltr')
  })
})
