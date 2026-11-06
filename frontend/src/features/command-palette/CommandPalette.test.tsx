import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { KillSwitchButton } from '../kill-switch/KillSwitch'
import { useKillSheets } from '../kill-switch/sheetStore'
import { CommandPalette } from './CommandPalette'
import { PaletteButton } from './PaletteDoors'
import { entryHooks, matchRank, paletteModules, staticEntries, unpack } from './registry'
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

/* GET /api/instruments?q= (ATL-250): the server matches inst_id or base currency, case-insensitive. */
const INSTRUMENTS = [
  { inst_id: 'XAU-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAU', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'live', blocked: false },
  { inst_id: 'XAUT-USDT-SWAP', inst_type: 'SWAP', asset_class: 'commodity', base_ccy: 'XAUT', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'preopen', blocked: false },
  { inst_id: 'BTC-USDT-SWAP', inst_type: 'SWAP', asset_class: 'crypto', base_ccy: 'BTC', quote_ccy: 'USDT', settle_ccy: 'USDT', state: 'live', blocked: false },
  { inst_id: 'BTC-USDT', inst_type: 'SPOT', asset_class: 'crypto', base_ccy: 'BTC', quote_ccy: 'USDT', settle_ccy: null, state: 'live', blocked: false },
]
let instrumentsFail = false
const instrumentCalls = () => calls.filter((c) => c.path.startsWith('/api/instruments'))

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
      if (path.startsWith('/api/instruments?')) {
        if (instrumentsFail) return reply(500, { message: 'Server Error' })
        const q = (new URLSearchParams(path.split('?')[1]).get('q') ?? '').toLowerCase()
        return reply(200, { data: INSTRUMENTS.filter((i) => i.inst_id.toLowerCase().includes(q) || (i.base_ccy ?? '').toLowerCase().includes(q)) })
      }
      if (path === '/api/cycles' && method === 'POST') return reply(202, { data: { requested: true } })
      if (path === '/api/portfolio/orders') return reply(200, { data: { env: 'demo', orders: [], algo_orders: [] } })
      if (path.startsWith('/api/portfolio/positions')) return reply(200, { data: { taken_at: RUNNING.as_of, items: [] } })
      return reply(404, { message: 'Not found' })
    }),
  )
}

function Where() {
  const { pathname, search } = useLocation()
  return (
    <>
      <p data-testid="where">{pathname}</p>
      <p data-testid="where-search">{search}</p>
    </>
  )
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
  instrumentsFail = false
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

describe('Ctrl+K on an Arabic keyboard layout (ATL-257)', () => {
  it('opens on Ctrl + physical K even when the layout types ن', async () => {
    renderShell()
    expect(palette()).not.toBeInTheDocument()
    fireEvent(window, new KeyboardEvent('keydown', { key: 'ن', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true }))
    expect(await screen.findByTestId('command-palette')).toBeInTheDocument()
  })

  it('still opens for {key: "k", code: "KeyK", metaKey: true}', async () => {
    renderShell()
    fireEvent(window, new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', metaKey: true, bubbles: true, cancelable: true }))
    expect(await screen.findByTestId('command-palette')).toBeInTheDocument()
  })

  it('still opens for {key: "k", code: "KeyK", ctrlKey: true}', async () => {
    renderShell()
    fireEvent(window, new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', ctrlKey: true, bubbles: true, cancelable: true }))
    expect(await screen.findByTestId('command-palette')).toBeInTheDocument()
  })

  it('plain k with no modifier does not open it', async () => {
    renderShell()
    fireEvent(window, new KeyboardEvent('keydown', { key: 'k', code: 'KeyK', bubbles: true, cancelable: true }))
    await new Promise((r) => setTimeout(r, 10))
    expect(palette()).not.toBeInTheDocument()
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
    // Once the instrument search has answered with nothing, one empty state for the whole palette.
    expect(await screen.findByText('No results for “zzqx”')).toBeInTheDocument()
    expect(screen.queryByTestId('palette-status-instruments')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })
})

describe('instruments (ATL-250)', () => {
  it('"xau" lists XAU-USDT-SWAP from the API under Instruments, with its asset class and state', async () => {
    // ATL-258: no delay between keys, so a busy machine cannot let the search debounce fire on
    // "xa" between two keystrokes; the one-request assertion below is about the debounce.
    const user = userEvent.setup({ delay: null })
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('xau')
    expect(screen.getByTestId('palette-status-instruments')).toHaveTextContent('Searching instruments…')
    const row = await screen.findByTestId('palette-item-inst.XAU-USDT-SWAP')
    const group = row.closest('[cmdk-group]') as HTMLElement
    expect(within(group).getByText('Instruments')).toBeInTheDocument()
    expect(row).toHaveTextContent('XAU-USDT-SWAP')
    expect(row).toHaveTextContent('Commodity perpetual')
    expect(row).toHaveTextContent('Trading')
    expect(screen.getByTestId('palette-item-inst.XAUT-USDT-SWAP')).toHaveTextContent('Pre-open')
    expect(screen.queryByTestId('palette-item-inst.BTC-USDT-SWAP')).not.toBeInTheDocument()
    expect(screen.queryByTestId('palette-status-instruments')).not.toBeInTheDocument()
    // Debounced: one request for the settled query, not one per key.
    expect(instrumentCalls().map((c) => c.path)).toEqual(['/api/instruments?q=xau&limit=20&state=any&env=demo'])
  })

  it('Enter on an instrument opens the War Room with it picked for the inspector', async () => {
    const user = userEvent.setup()
    renderShell('/portfolio')
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('xau')
    await screen.findByTestId('palette-item-inst.XAU-USDT-SWAP')
    // Nothing above it matches "xau", so the first instrument is selected.
    expect(screen.getByRole('option', { selected: true })).toHaveAttribute('data-testid', 'palette-item-inst.XAU-USDT-SWAP')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/warroom'))
    expect(screen.getByTestId('where-search')).toHaveTextContent('?inst=XAU-USDT-SWAP')
    expect(palette()).not.toBeInTheDocument()
  })

  it('one character sends no request', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('x')
    await new Promise((r) => setTimeout(r, 300))
    expect(instrumentCalls()).toHaveLength(0)
    expect(screen.queryByTestId('palette-status-instruments')).not.toBeInTheDocument()
  })

  it('a query with no instruments shows the "No instruments match" row under the other results', async () => {
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('risk')
    await waitFor(() => expect(screen.getByTestId('palette-status-instruments')).toHaveAttribute('data-kind', 'none'))
    expect(screen.getByTestId('palette-status-instruments')).toHaveTextContent('No instruments match.')
    expect(screen.getByTestId('palette-item-page.settings.risk')).toBeInTheDocument()
  })

  it('a failed search says so and keeps pages and actions', async () => {
    instrumentsFail = true
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('zzqx')
    await waitFor(() => expect(screen.getByTestId('palette-status-instruments')).toHaveAttribute('data-kind', 'error'))
    expect(screen.getByTestId('palette-status-instruments')).toHaveTextContent('Couldn’t search instruments. Pages and actions still work.')
    expect(screen.queryByText('No results for “zzqx”')).not.toBeInTheDocument()
  })

  it('a newer query replaces the older one and aborts its request', async () => {
    const user = userEvent.setup()
    const signals: AbortSignal[] = []
    const base = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation(async (input, init = {}) => {
      if (String(input).includes('/api/instruments?q=xa&')) {
        signals.push(init.signal!)
        return new Promise<Response>(() => {})
      }
      return base(input, init)
    })
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('xa')
    await waitFor(() => expect(signals).toHaveLength(1))
    await user.keyboard('u')
    await screen.findByTestId('palette-item-inst.XAU-USDT-SWAP')
    await waitFor(() => expect(signals[0]!.aborted).toBe(true))
  })

  it('draws the Arabic rows in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    const user = userEvent.setup()
    renderShell()
    await user.keyboard('{Meta>}k{/Meta}')
    await user.keyboard('btc')
    const spot = await screen.findByTestId('palette-item-inst.BTC-USDT')
    expect(spot).toHaveTextContent('عملة مشفّرة فورية')
    expect(screen.getByTestId('palette-item-inst.BTC-USDT-SWAP')).toHaveTextContent('عقد عملة مشفّرة دائم')
    expect(screen.getByText('الأدوات')).toBeInTheDocument()
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
    const { result } = renderHook(() => entryHooks.flatMap((provide) => unpack(provide('')).entries), { wrapper })
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
