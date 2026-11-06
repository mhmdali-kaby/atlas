import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL } from '../../lib/api'
import { TwoFactorSection } from './TwoFactorSection'

/* ATL-247: Settings → General → Sign-in against a faked ATL-245 /api/me/two-factor. */

const SECRET = 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
const CODES = ['k7m2p-x9q4t', 'r3vhw-8n2ca', 'p6tz9-m4kxe', 'b2wq7-h5rnd', 'y8cf3-t6jmv', 'e4nk2-q9xsp', 'h9dr6-w3bzu', 'u5gm8-c2tfy']
const QR = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
const PASSWORD = 'correct horse'
const GOOD_CODE = '482913'

type Call = { method: string; path: string; body: Record<string, string> | null }

function backend(initial: { enabled: boolean; left?: number } = { enabled: false }) {
  const state = {
    status: {
      enabled: initial.enabled,
      confirmed_at: initial.enabled ? '2026-10-21T08:15:00Z' : null,
      recovery_codes_left: initial.enabled ? (initial.left ?? 8) : 0,
    },
    started: false,
    calls: [] as Call[],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const body = init.body ? (JSON.parse(String(init.body)) as Record<string, string>) : null
      state.calls.push({ method, path, body })
      const reply = (status: number, b?: unknown) =>
        new Response(b === undefined ? null : JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
      const wrongPw = () => reply(422, { message: 'The password is incorrect.', errors: { password: ['The password is incorrect.'] } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/me/two-factor' && method === 'GET') return reply(200, { data: state.status })
      if (path === '/api/me/two-factor/start') {
        if (body?.password !== PASSWORD) return wrongPw()
        state.started = true
        return reply(200, { data: { qr_svg: QR, secret: SECRET } })
      }
      if (path === '/api/me/two-factor/confirm') {
        if (!state.started) return reply(409, { message: 'Start first.', error: 'not_started' })
        if (body?.code !== GOOD_CODE)
          return reply(422, { message: 'The code is wrong.', errors: { code: ['That code didn’t match the one in your app.'] } })
        state.status = { enabled: true, confirmed_at: '2026-11-07T10:00:00Z', recovery_codes_left: 8 }
        return reply(200, { data: { ...state.status, recovery_codes: CODES } })
      }
      if (path === '/api/me/two-factor' && method === 'DELETE') {
        if (body?.password !== PASSWORD) return wrongPw()
        state.status = { enabled: false, confirmed_at: null, recovery_codes_left: 0 }
        return reply(200, { data: state.status })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderSection(lang: 'en' | 'ar' = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TwoFactorSection email="owner@atlas.test" lang={lang} />
    </QueryClientProvider>,
  )
}

async function toScanStep(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Turn on…' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('Step 1 of 3')).toBeInTheDocument()
  await user.type(within(dialog).getByLabelText('Your Atlas password'), PASSWORD)
  await user.click(within(dialog).getByRole('button', { name: 'Continue' }))
  await screen.findByTestId('tf-qr')
  return dialog
}

afterEach(async () => {
  vi.unstubAllGlobals()
  await act(() => i18n.changeLanguage('en'))
})

describe('Settings → General → Two-factor sign-in', () => {
  it('turns on: password, then a 6-digit code, shows the 8 codes, and closing leaves it On', async () => {
    const api = backend()
    const user = userEvent.setup()
    renderSection()
    expect(await screen.findByText('Off. Atlas asks only for your password.')).toBeInTheDocument()

    await toScanStep(user)
    expect(screen.getByText('Step 2 of 3')).toBeInTheDocument()
    expect(screen.getByTestId('tf-secret')).toHaveTextContent('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP')
    expect(screen.getByTestId('tf-qr')).toHaveAttribute('src', expect.stringContaining('data:image/svg+xml'))

    // Six digits submit by themselves.
    await user.type(screen.getByLabelText('6-digit code'), GOOD_CODE)
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('Save your recovery codes')).toBeInTheDocument()
    expect(within(alert).getByText('Step 3 of 3')).toBeInTheDocument()
    const codes = within(screen.getByTestId('tf-codes')).getAllByRole('listitem')
    expect(codes).toHaveLength(8)
    CODES.forEach((c) => expect(within(alert).getByText(c)).toBeInTheDocument())

    const order = api.calls.filter((c) => c.method === 'POST' && c.path.startsWith('/api/me/two-factor')).map((c) => c.path)
    expect(order).toEqual(['/api/me/two-factor/start', '/api/me/two-factor/confirm'])
    expect(api.calls.find((c) => c.path === '/api/me/two-factor/start')?.body).toEqual({ password: PASSWORD })
    expect(api.calls.find((c) => c.path === '/api/me/two-factor/confirm')?.body).toEqual({ code: GOOD_CODE })

    // Esc does not close the codes step; only "I saved them" does.
    await user.keyboard('{Escape}')
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: 'I saved them' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())

    const on = await screen.findByTestId('tf-on')
    expect(within(on).getByTestId('tf-badge')).toHaveTextContent('On')
    expect(within(on).getByTestId('tf-left')).toHaveTextContent('8 of 8 left.')
    expect(within(on).getByRole('img', { name: '8 of 8 recovery codes left' })).toBeInTheDocument()
    expect(screen.queryByText(CODES[0]!)).not.toBeInTheDocument()
  })

  it('a wrong code shows the API’s error under the field and keeps the QR on screen', async () => {
    backend()
    const user = userEvent.setup()
    renderSection()
    await toScanStep(user)

    await user.type(screen.getByLabelText('6-digit code'), '111111')
    expect(await screen.findByText('That code didn’t match the one in your app.')).toBeInTheDocument()
    expect(screen.getByLabelText('6-digit code')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('6-digit code')).toHaveValue('')
    expect(screen.getByTestId('tf-qr')).toBeInTheDocument()
    expect(screen.getByText('Step 2 of 3')).toBeInTheDocument()
    expect(screen.queryByTestId('tf-codes')).not.toBeInTheDocument()
  })

  it('a wrong password on Turn on stays on step 1 with the error under the field', async () => {
    backend()
    const user = userEvent.setup()
    renderSection()
    await user.click(await screen.findByRole('button', { name: 'Turn on…' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Your Atlas password'), 'nope')
    await user.click(within(dialog).getByRole('button', { name: 'Continue' }))
    expect(await within(dialog).findByText('The password is incorrect.')).toBeInTheDocument()
    expect(screen.queryByTestId('tf-qr')).not.toBeInTheDocument()
  })

  it('Turn off needs the password; a wrong one keeps it on, the right one returns to Off', async () => {
    const api = backend({ enabled: true, left: 6 })
    const user = userEvent.setup()
    renderSection()
    const on = await screen.findByTestId('tf-on')
    expect(within(on).getByTestId('tf-left')).toHaveTextContent('6 of 8 left.')

    await user.click(within(on).getByRole('button', { name: 'Turn off…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Turn off two-factor sign-in?')).toBeInTheDocument()
    const go = within(dialog).getByRole('button', { name: 'Turn off' })
    expect(go).toBeDisabled()

    await user.type(within(dialog).getByLabelText('Your Atlas password'), 'wrong')
    await user.click(go)
    expect(await within(dialog).findByText('The password is incorrect.')).toBeInTheDocument()
    expect(screen.getByTestId('tf-on')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    const pw = within(dialog).getByLabelText('Your Atlas password')
    await user.clear(pw)
    await user.type(pw, PASSWORD)
    await user.click(within(dialog).getByRole('button', { name: 'Turn off' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByTestId('tf-off')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Turn on…' })).toBeInTheDocument()
    const del = api.calls.filter((c) => c.method === 'DELETE')
    expect(del.map((c) => c.body)).toEqual([{ password: 'wrong' }, { password: PASSWORD }])
  })

  it('warns when 2 or fewer codes are left', async () => {
    backend({ enabled: true, left: 2 })
    renderSection()
    expect(await screen.findByText(/Only 2 recovery codes left/)).toBeInTheDocument()
    expect(screen.getByTestId('tf-marks')).toHaveClass('is-low')
  })

  it('in Arabic the section is right to left with Arabic labels; the secret and codes stay left to right', async () => {
    await act(() => i18n.changeLanguage('ar'))
    backend()
    const user = userEvent.setup()
    renderSection('ar')
    const section = await screen.findByTestId('tf-section')
    expect(document.documentElement.dir).toBe('rtl')
    expect(section.closest('[dir]')).toHaveAttribute('dir', 'rtl')
    expect(await within(section).findByText('التحقق بخطوتين')).toBeInTheDocument()
    expect(within(section).getByText('تسجيل الدخول')).toBeInTheDocument()

    await user.click(within(section).getByRole('button', { name: 'تفعيل…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('الخطوة 1 من 3')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText('كلمة مرور أطلس'), PASSWORD)
    await user.click(within(dialog).getByRole('button', { name: 'متابعة' }))
    const secret = await screen.findByTestId('tf-secret')
    expect(secret).toHaveAttribute('dir', 'ltr')
    expect(screen.getByText('امسح هذا الرمز بتطبيق المصادقة')).toBeInTheDocument()
    const field = screen.getByLabelText('رمز من 6 أرقام')
    expect(field).toHaveAttribute('dir', 'ltr')

    await user.type(field, GOOD_CODE)
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('احفظ رموز الاسترداد')).toBeInTheDocument()
    const codes = within(screen.getByTestId('tf-codes')).getAllByRole('listitem')
    expect(codes).toHaveLength(8)
    codes.forEach((li) => expect(li.querySelector('code')).toHaveAttribute('dir', 'ltr'))
    expect(within(alert).getByRole('button', { name: 'حفظتُها' })).toBeInTheDocument()
  })
})
