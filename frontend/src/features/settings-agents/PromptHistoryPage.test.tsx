import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { routes } from '../../app/router'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { diffRows, hunks } from './prompts'

/*
 * ATL-253: Settings → Agents → Prompt history on ATL-251's API (view, compare, pin, unpin).
 */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale: 'en', theme: 'system' }

const V1 = ['You are one member of "Atlas".', 'ROLE: Historian.', '- State the sample.', '  evidence is weak.', '- Give distributions.'].join('\n')
const V2 = ['You are one member of "Atlas".', 'ROLE: Historian.', '- State the sample.', '  evidence is weak, and your verdict can be at most "weakly supports".', '- Give distributions.', '- Warn when the stop is inside normal noise.'].join('\n')

interface Backend {
  pinned: number | null
  puts: unknown[]
}

function fakeBackend(opts: { versions?: 'two' | 'none'; putStatus?: number } = {}): Backend {
  const state: Backend = { pinned: null, puts: [] }
  const rows = () =>
    opts.versions === 'none'
      ? []
      : [
          { version: 2, hash12: 'b1c2d3e4f5a6', created_at: '2026-10-27T07:00:00Z', runs_count: 31, is_current_file: true, is_pinned: state.pinned === 2 },
          { version: 1, hash12: '58e1a7c3d940', created_at: '2026-10-24T08:00:00Z', runs_count: 22, is_current_file: false, is_pinned: state.pinned === 1 },
        ]
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/status') return reply(200, { owner_exists: true })
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/connections') return reply(200, { data: [] })
      if (path.startsWith('/api/settings/history')) return reply(200, { data: [] })
      if (path === '/api/agents/historian/prompts') return reply(200, { data: rows(), meta: { agent: 'historian', env: 'demo', pinned_version: state.pinned } })
      if (path === '/api/agents/historian/prompts/1') return reply(200, { data: { version: 1, hash12: '58e1a7c3d940', body: V1 } })
      if (path === '/api/agents/historian/prompts/2') return reply(200, { data: { version: 2, hash12: 'b1c2d3e4f5a6', body: V2 } })
      if (path === '/api/agents/historian/prompt-pin' && method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { version: number | null }
        state.puts.push(body)
        if (opts.putStatus) return reply(opts.putStatus, { message: 'Conflict', current_version: 8 })
        state.pinned = body.version
        return reply(200, { data: { agent: 'historian', env: 'demo', pinned_version: body.version, settings_version: 8 } })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderAt(url: string) {
  const router = createMemoryRouter(routes, { initialEntries: [url] })
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
  localStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('dir')
  await i18n.changeLanguage('en')
})

describe('diff rows (jsdiff)', () => {
  it('numbers old and new lines and folds long unchanged runs', () => {
    const rows = diffRows(V1, V2)
    expect(rows.filter((r) => r.kind === 'add').map((r) => r.new)).toEqual([4, 6])
    expect(rows.filter((r) => r.kind === 'rem').map((r) => r.old)).toEqual([4])
    const long = Array.from({ length: 20 }, (_, i) => `line ${i}`)
    const blocks = hunks(diffRows(long.join('\n'), [...long, 'new'].join('\n')))
    expect(blocks[0]).toMatchObject({ type: 'fold' })
    expect(blocks[0]?.lines).toHaveLength(17)
  })
})

describe('Prompt history (ATL-253)', () => {
  it('compares two versions: the added line is marked "+" and the removed line "−"', async () => {
    fakeBackend()
    const user = userEvent.setup()
    renderAt('/settings/agents/prompts?agent=historian&v=2')
    expect(await screen.findByRole('heading', { name: 'Historian’s prompt' })).toBeInTheDocument()
    await user.click(await screen.findByRole('radio', { name: 'Compare' }))
    const diff = await screen.findByTestId('prompt-diff')
    const added = within(diff).getByText('- Warn when the stop is inside normal noise.').closest('li')
    const removed = within(diff).getByText('evidence is weak.', { exact: false, selector: '.pmt-l' }).closest('li')
    expect(added).toHaveAttribute('data-kind', 'add')
    expect(within(added as HTMLElement).getByTestId('diff-sign')).toHaveTextContent('+')
    expect(removed).toHaveAttribute('data-kind', 'rem')
    expect(within(removed as HTMLElement).getByTestId('diff-sign')).toHaveTextContent('−')
    expect(within(added as HTMLElement).getByText('Added:')).toHaveClass('sr-only')
    expect(screen.getByTestId('diff-counts')).toHaveTextContent('2 lines added')
    expect(screen.getByTestId('diff-counts')).toHaveTextContent('1 line removed')
    expect(screen.getByTestId('compare-from')).toHaveValue('1')
  })

  it('"Use version 1…" asks first, then pins version 1 and says it is pinned', async () => {
    const be = fakeBackend()
    const user = userEvent.setup()
    renderAt('/settings/agents/prompts?agent=historian')
    expect(await screen.findByTestId('prompt-status')).toHaveTextContent('Historian uses its prompt file, version 2.')
    await user.click(screen.getByTestId('rail-v1'))
    await user.click(await screen.findByRole('button', { name: 'Use version 1…' }))
    const sheet = await screen.findByRole('dialog', { name: 'Use version 1 for Historian?' })
    expect(be.puts).toEqual([])
    await user.click(within(sheet).getByRole('button', { name: 'Use version 1' }))
    await waitFor(() => expect(be.puts).toEqual([{ version: 1 }]))
    await waitFor(() => expect(screen.getByTestId('prompt-status')).toHaveTextContent('Historian uses version 1, pinned. Its prompt file is at version 2.'))
    expect(within(screen.getByTestId('rail-v1')).getByText('Pinned')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows the 409 message in the sheet and keeps it open', async () => {
    fakeBackend({ putStatus: 409 })
    const user = userEvent.setup()
    renderAt('/settings/agents/prompts?agent=historian&v=1')
    await user.click(await screen.findByRole('button', { name: 'Use version 1…' }))
    const sheet = await screen.findByRole('dialog')
    await user.click(within(sheet).getByRole('button', { name: 'Use version 1' }))
    expect(await within(sheet).findByRole('alert')).toHaveTextContent('Settings changed in another window.')
  })

  it('"Use the prompt file again" unpins (version null) and shows the file version in use', async () => {
    const be = fakeBackend()
    be.pinned = 1
    const user = userEvent.setup()
    renderAt('/settings/agents/prompts?agent=historian')
    const status = await screen.findByTestId('prompt-status')
    expect(status).toHaveTextContent('Historian uses version 1, pinned.')
    await user.click(within(status).getByRole('button', { name: 'Use the prompt file again' }))
    await waitFor(() => expect(be.puts).toEqual([{ version: null }]))
    await waitFor(() => expect(screen.getByTestId('prompt-status')).toHaveTextContent('Historian uses its prompt file, version 2.'))
    expect(within(screen.getByTestId('rail-v2')).getByText('In use')).toBeInTheDocument()
  })

  it('shows the Settings → Agents row as pinned', async () => {
    const be = fakeBackend()
    be.pinned = 1
    vi.stubGlobal(
      'fetch',
      ((orig: typeof fetch) => async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input).replace(API_URL, '')
        if (path === '/api/settings?env=demo')
          return new Response(JSON.stringify({ data: { id: 's1', env: 'demo', version: 4, settings: { onboarding_completed: 1 }, presets: {}, updated_at: '2026-10-28T09:00:00Z' } }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        if (path === '/api/agents?env=demo') return new Response(JSON.stringify({ data: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        if (/^\/api\/agents\/\w+\/prompts$/.test(path) && path !== '/api/agents/historian/prompts')
          return new Response(JSON.stringify({ data: [], meta: { agent: 'x', env: 'demo', pinned_version: null } }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        return orig(input, init)
      })(globalThis.fetch),
    )
    renderAt('/settings/agents')
    const row = await screen.findByTestId('prompt-row-historian')
    await waitFor(() => expect(row).toHaveTextContent('Pinned to version 1. The prompt file is at version 2.'))
    expect(within(row).getByRole('link', { name: /History…/ })).toHaveAttribute('href', '/settings/agents/prompts?agent=historian')
  })

  it('shows the empty state for an agent with no stored versions', async () => {
    fakeBackend({ versions: 'none' })
    renderAt('/settings/agents/prompts?agent=historian')
    const empty = await screen.findByTestId('prompts-empty')
    expect(empty).toHaveTextContent('No versions saved yet')
  })

  it('in Arabic the page is right-to-left and the prompt text stays left-to-right', async () => {
    fakeBackend()
    await i18n.changeLanguage('ar')
    renderAt('/settings/agents/prompts?agent=historian')
    const text = await screen.findByTestId('prompt-text')
    expect(document.documentElement).toHaveAttribute('dir', 'rtl')
    expect(text).toHaveAttribute('dir', 'ltr')
    expect(text).toHaveAttribute('lang', 'en')
    expect(text).toHaveTextContent('ROLE: Historian.')
    expect(screen.getByTestId('prompt-status')).toHaveTextContent('ملف موجّهه')
  })

  it('in Arabic an agent with no versions shows the Arabic empty state', async () => {
    fakeBackend({ versions: 'none' })
    await i18n.changeLanguage('ar')
    renderAt('/settings/agents/prompts?agent=historian')
    expect(await screen.findByTestId('prompts-empty')).toHaveTextContent('لا إصدارات محفوظة بعد')
    expect(document.documentElement).toHaveAttribute('dir', 'rtl')
  })
})
