import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { KillSwitchButton } from '../kill-switch/KillSwitch'
import { useKillSheets } from '../kill-switch/sheetStore'
import { CommandPalette } from './CommandPalette'
import { PaletteButton } from './PaletteDoors'
import { entryHooks, matchRank, paletteModules, staticEntries } from './registry'
import { usePaletteStore } from './store'
import type { PaletteEntry } from './types'

/* ATL-249: the ⌘K palette (decision 265 (2), design v22 §6). */

const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Al-Kaabi', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const RUNNING = {
  env: 'demo',
  as_of: '2026-11-07T08:00:00Z',
  trading: { paused: false, reason: null, paused_at: null, accepting_new_orders: true },
  breakers: [],
}

let calls: { method: string; path: string }[] = []

function fakeBackend() {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      calls.push({ method, path })
      const reply = (status: number, body?: unknown) =>
        new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/auth/me') return reply(200, { data: OWNER })
      if (path === '/api/system/health') return reply(200, { data: RUNNING })
      if (path === '/api/cycles' && method === 'POST') return reply(202, { data: { requested: true } })
      if (path === '/api/portfolio/orders') return reply(200, { data: { env: 'demo', orders: [], algo_orders: [] } })
      if (path.startsWith('/api/portfolio/positions')) return reply(200, { data: { taken_at: RUNNING.as_of, items: [] } })
      return reply(404, { message: 'Not found' })
    }),
  )
}

function Where() {
  const { pathname } = useLocation()
  return <p data-testid="where">{pathname}</p>
}

function renderShell(path = '/portfolio') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (
    <>
      <button type="button">Before</button>
      <PaletteButton />
      <KillSwitchButton />
      <Where />
      <CommandPalette />
    </>
  )
  const router = createMemoryRouter([{ path: '*', element: page }], { initialEntries: [path] })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

beforeAll(() => {
  // jsdom has no layout: cmdk scrolls the selected row into view.
  Element.prototype.scrollIntoView = vi.fn()
  // …and sizes its list with a ResizeObserver.
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  )
})

beforeEach(async () => {
  resetCsrfForTests()
  fakeBackend()
  await i18n.changeLanguage('en')
})

afterEach(() => {
  act(() => {
    usePaletteStore.setState({ open: false })
    useKillSheets.setState({ kill: false, resume: false })
  })
  vi.mocked(fetch).mockReset()
})

const palette = () => screen.queryByTestId('command-palette')

describe('opening and closing', () => {
  it.each([['{Meta>}k{/Meta}'], ['{Control>}k{/Control}']])('%s opens it, Esc closes it, and focus returns', async (keys) => {
    const user = userEvent.setup()
    renderShell()
    const before = screen.getByRole('button', { name: 'Before' })
    before.focus()
    await user.keyboard(keys)
    expect(await screen.findByTestId('command-palette')).toBeInTheDocument()
    expect(screen.getByRole('combobox')).toHaveFocus()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(palette()).not.toBeInTheDocument())
    expect(before).toHaveFocus()
  })

  it('⌘K again closes it, and the sidebar capsule opens it', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.click(screen.getByTestId('palette-open'))
    expect(await screen.findByTestId('command-palette')).toBeInTheDocument()
    await user.keyboard('{Meta>}k{/Meta}')
    await waitFor(() => expect(palette()).not.toBeInTheDocument())
    expect(screen.getByTestId('palette-open')).toHaveFocus()
  })
})

describe('finding pages', () => {
  it('"risk" then Enter goes to Settings → Risk', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('risk')
    expect(screen.getByRole('option', { selected: true })).toHaveTextContent('Risk')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/settings/risk'))
    expect(palette()).not.toBeInTheDocument()
  })

  it('finds Portfolio by its Arabic label in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    const user = userEvent.setup()
    renderShell('/warroom')
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('المحفظة')
    const options = screen.getAllByRole('option')
    expect(options.length).toBeGreaterThan(0)
    // Every hit is a Portfolio page; the first is its Overview, drawn in Arabic.
    expect(options[0]).toHaveAttribute('data-testid', 'palette-item-page.portfolio.overview')
    expect(options[0]).toHaveTextContent('نظرة عامة')
    expect(screen.getByTestId('palette-item-page.portfolio.positions')).toBeInTheDocument()
    expect(screen.queryByTestId('palette-item-page.settings.risk')).not.toBeInTheDocument()
  })

  it('matches labels in both languages whichever is showing', () => {
    expect(matchRank(['Risk', 'المخاطر'], '', 'المخاطر')).toBe(0)
    expect(matchRank(['Risk', 'المخاطر'], '', 'risk')).toBe(0)
    // Hamza and teh marbuta spellings fold together.
    expect(matchRank(['General', 'الإعدادات'], '', 'الاعدادات')).toBe(0)
    expect(matchRank(['Overview', 'نظرة عامة'], 'محفظه', 'المحفظه')).toBe(-1)
    expect(matchRank(['Cash flows'], '', 'flo')).toBe(1)
    expect(matchRank(['Cash flows'], '', 'zzqx')).toBe(-1)
  })

  it('says so when nothing matches', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('zzqx')
    expect(screen.getByText('No results for “zzqx”')).toBeInTheDocument()
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })
})

describe('actions', () => {
  it('"Stop" opens the kill sheet and sends no kill request', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('stop')
    expect(screen.getByRole('option', { selected: true })).toHaveAttribute('data-testid', 'palette-item-action.stop')
    await user.keyboard('{Enter}')
    expect(await screen.findByTestId('kill-view-confirm')).toBeInTheDocument()
    expect(palette()).not.toBeInTheDocument()
    expect(calls.filter((c) => c.path === '/api/system/kill')).toHaveLength(0)
  })

  it('"Run a cycle now" calls the cycle API once', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('cycle')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/cycles')).toHaveLength(1))
    await new Promise((r) => setTimeout(r, 50))
    expect(calls.filter((c) => c.path === '/api/cycles')).toHaveLength(1)
  })

  it('offers Pause while trading runs, never Resume', async () => {
    const user = userEvent.setup()
    renderShell()
    await waitFor(() => expect(calls.some((c) => c.path === '/api/system/health')).toBe(true))
    await user.keyboard('{Meta>}k{/Meta}')
    expect(await screen.findByTestId('palette-item-action.pause')).toBeInTheDocument()
    expect(screen.queryByTestId('palette-item-action.resume')).not.toBeInTheDocument()
  })
})

describe('every palette.ts entry', () => {
  function liveEntries(health: typeof RUNNING): PaletteEntry[] {
    const client = new QueryClient()
    client.setQueryData(['system', 'health'], { data: health })
    const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const { result } = renderHook(() => entryHooks.flatMap((provide) => provide('')), { wrapper })
    return result.current
  }

  it('is found by glob in every feature the task names', () => {
    expect(paletteModules.length).toBeGreaterThanOrEqual(18)
    const ids = staticEntries.map((e) => e.id)
    for (const id of ['page.portfolio.positions', 'page.settings.risk', 'page.warroom', 'page.agents', 'page.journal', 'page.backtester', 'page.settings.environment']) {
      expect(ids).toContain(id)
    }
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('has an English and an Arabic label (and sub-line)', () => {
    const paused = { ...RUNNING, trading: { ...RUNNING.trading, paused: true } } as unknown as typeof RUNNING
    const all = [...staticEntries, ...liveEntries(RUNNING), ...liveEntries(paused)]
    expect(all.map((e) => e.id)).toEqual(expect.arrayContaining(['action.pause', 'action.resume', 'action.stop', 'action.cycle']))
    const missing: string[] = []
    for (const e of all) {
      for (const key of [e.labelKey, ...(e.subKey ? [e.subKey] : [])]) {
        for (const lng of ['en', 'ar']) {
          const [ns, ...rest] = key.split(':')
          if (!i18n.exists(rest.join(':'), { ns, lng, fallbackLng: false })) missing.push(`${e.id} ${lng} ${key}`)
        }
      }
      if (!e.to && !e.run) missing.push(`${e.id}: neither to nor run`)
    }
    expect(missing).toEqual([])
  })
})
