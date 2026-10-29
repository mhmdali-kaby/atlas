import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { Bot, Layers } from 'lucide-react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_URL, resetCsrfForTests } from '../lib/api'
import { AppShell } from './AppShell'
import { activeArea, areaHome, buildShellRegistry, type FeatureModule, type SidebarSectionProps } from './feature'
import { features, shellRegistry } from './registry'

/*
 * ATL-41: a feature joins an area and adds sidebar sections from its own routes.tsx alone.
 * These fakes stand in for a new feature folder; nothing shared is edited to add them.
 */
function FakeAgents({ onNavigate }: SidebarSectionProps) {
  return (
    <button type="button" data-nav-item="" onClick={onNavigate}>
      Fake agent row
    </button>
  )
}

const fakeWarRoom: FeatureModule = {
  area: 'warroom',
  routes: [{ path: 'warroom', element: <p>Fake war room</p> }],
  sidebar: { id: 'fake-agents', order: 10, labelKey: 'shell:area.warroom', component: FakeAgents },
}
const fakePortfolioExtra: FeatureModule = {
  area: 'portfolio',
  routes: [],
  nav: { path: '/portfolio/extra', labelKey: 'portfolio:nav.extra', order: 15, icon: Layers },
}

describe('shell registry', () => {
  it('the real features register War Room, Portfolio and Settings, each with its own pages', () => {
    expect(features.length).toBeGreaterThan(0)
    expect(shellRegistry.areas.map((a) => a.id)).toEqual(['warroom', 'portfolio', 'settings'])
    const paths = (area: string) => shellRegistry.navEntries.filter((e) => e.area === area).map((e) => e.path)
    expect(paths('portfolio')).toEqual(['/portfolio', '/portfolio/positions', '/portfolio/holdings', '/portfolio/cashflows'])
    expect(paths('settings')).toEqual(['/settings/connections', '/settings/risk'])
  })

  it('adds a fake feature\'s area and sections without touching any other module', () => {
    // The real War Room feature is left out, so the fake one is the only War Room contribution.
    const others = features.filter((f) => f.area !== 'warroom')
    const base = buildShellRegistry(others)
    const withFakes = buildShellRegistry([...others, fakeWarRoom, fakePortfolioExtra])

    expect(withFakes.areas.map((a) => a.id)).toEqual(['warroom', ...base.areas.map((a) => a.id)])
    const warroom = withFakes.sections.filter((s) => s.area === 'warroom')
    expect(warroom).toHaveLength(1)
    expect(warroom[0]).toMatchObject({ kind: 'custom', order: 10 })

    // A link joins its area's list in order; other areas are unchanged.
    const portfolio = withFakes.navEntries.filter((e) => e.area === 'portfolio').map((e) => e.path)
    expect(portfolio.slice(0, 2)).toEqual(['/portfolio', '/portfolio/extra'])
    expect(withFakes.navEntries.filter((e) => e.area === 'settings')).toEqual(base.navEntries.filter((e) => e.area === 'settings'))
  })

  it('hides areas nobody contributes to', () => {
    const reg = buildShellRegistry([fakePortfolioExtra])
    expect(reg.areas.map((a) => a.id)).toEqual(['portfolio'])
  })

  it('refuses nav or sidebar sections without an area', () => {
    expect(() => buildShellRegistry([{ routes: [], nav: { path: '/x', labelKey: 'x', order: 1, icon: Bot } }])).toThrow(/area/)
    expect(() => buildShellRegistry([{ routes: [], sidebar: { id: 'x', order: 1, component: FakeAgents } }])).toThrow(/area/)
    expect(buildShellRegistry([{ routes: [] }]).areas).toEqual([])
  })

  it('finds the area from a sidebar link, else from the area base path', () => {
    const reg = buildShellRegistry([...features, fakeWarRoom])
    expect(activeArea(reg, '/portfolio/positions')?.id).toBe('portfolio')
    expect(activeArea(reg, '/settings/risk')?.id).toBe('settings')
    expect(activeArea(reg, '/warroom/threads/abc')?.id).toBe('warroom')
    expect(activeArea(reg, '/warroomx')).toBeUndefined()
    expect(activeArea(reg, '/nowhere')).toBeUndefined()
    expect(areaHome(reg, 'settings')).toBe('/settings/connections')
    expect(areaHome(reg, 'warroom')).toBe('/warroom')
  })
})

describe('shell with a fake War Room feature', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('draws the War Room switcher entry and only its own section on /warroom', async () => {
    resetCsrfForTests()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input).replace(API_URL, '')
        const reply = (status: number, body?: unknown) =>
          new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
        if (path === '/api/auth/me') return reply(200, { data: { id: '1', name: 'Owner', email: 'o@atlas.test', locale: 'en', theme: 'system' } })
        return reply(404, {})
      }),
    )
    const shell = buildShellRegistry([...features, fakeWarRoom])
    const router = createMemoryRouter(
      [{ path: '/', element: <AppShell shell={shell} />, children: [{ path: 'warroom', element: <p>Fake war room</p> }] }],
      { initialEntries: ['/warroom'] },
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    const sidebar = await screen.findByRole('complementary', { name: 'Sidebar' })
    const areas = within(sidebar).getByRole('navigation', { name: 'Areas' })
    expect(within(areas).getAllByRole('link').map((a) => a.textContent)).toEqual(['War Room', 'Portfolio', 'Settings'])
    expect(within(areas).getByRole('link', { name: 'War Room' })).toHaveAttribute('aria-current', 'true')

    const list = within(sidebar).getByRole('navigation', { name: 'War Room' })
    expect(within(list).getByRole('button', { name: 'Fake agent row' })).toBeInTheDocument()
    expect(within(list).queryAllByRole('link')).toHaveLength(0)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('War Room')
  })
})
