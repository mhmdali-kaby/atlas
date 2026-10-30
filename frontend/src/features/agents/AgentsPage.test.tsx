import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import type { AgentRow } from './api'
import { AgentsPage } from './AgentsPage'

type Listener = (e: unknown) => void
const listeners = new Map<string, Set<Listener>>()

vi.mock('../../lib/echo', () => ({
  getEcho: () => ({
    private: () => ({
      listen(event: string, cb: Listener) {
        if (!listeners.has(event)) listeners.set(event, new Set())
        listeners.get(event)!.add(cb)
      },
      stopListening(event: string, cb: Listener) {
        listeners.get(event)?.delete(cb)
      },
    }),
  }),
}))

function agent(slug: string, over: Partial<AgentRow> = {}): AgentRow {
  return {
    slug,
    name: slug,
    handle: slug,
    enabled: true,
    model: 'strong',
    model_name: 'opus',
    state: 'idle',
    state_since: '2026-10-30T08:00:00Z',
    current_task: null,
    today: { tokens: 100, tokens_in: 80, tokens_out: 20, turns: 2, runs: 1, cost_usd_notional: '0.010000' },
    budget: { daily_tokens: 1000, daily_turns: 300 },
    prompt: { sequence: 3, sha: 'abcdef012345', created_at: '2026-10-29T08:00:00Z' },
    last_run: { status: 'ok', trigger: 'mention', started_at: '2026-10-30T08:00:00Z', ended_at: '2026-10-30T08:01:00Z' },
    ...over,
  }
}

const SLUGS = ['orchestrator', 'market_scout', 'news_intelligence', 'historian', 'technical_analyst', 'derivatives_sentiment', 'strategist', 'devils_advocate', 'portfolio_manager']

function fakeBackend(lists: AgentRow[][], claude: Record<string, unknown> = {}) {
  const seen = { agents: 0 }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(API_URL, '')
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path.startsWith('/api/agents?')) {
        const list = lists[Math.min(seen.agents, lists.length - 1)]!
        seen.agents += 1
        return reply(200, { data: list, meta: { env: 'demo', day: '2026-10-30', timezone: 'Asia/Baghdad', day_start: '', day_end: '', settings_version: 1 } })
      }
      if (path === '/api/agents/claude-status') {
        return reply(200, {
          data: {
            ok: true,
            installed: true,
            logged_in: true,
            api_key_env_clear: true,
            login_command: 'claude login',
            checks: [],
            checked_at: '2026-10-30T08:00:00Z',
            ...claude,
          },
        })
      }
      return reply(404)
    }),
  )
  return seen
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(async () => {
  resetCsrfForTests()
  listeners.clear()
  await i18n.changeLanguage('en')
})
afterEach(() => vi.unstubAllGlobals())

describe('AgentsPage', () => {
  it('renders every agent with its state word and a warning meter at 85%', async () => {
    fakeBackend([SLUGS.map((s) => (s === 'historian' ? agent(s, { today: { ...agent(s).today, tokens: 850 } }) : agent(s)))])
    renderPage()
    const rows = await screen.findAllByTestId('ag-row')
    expect(rows).toHaveLength(9)
    const historian = rows.find((r) => r.dataset.slug === 'historian')!
    expect(within(historian).getByTestId('ag-meter-tokens')).toHaveAttribute('data-warn', 'true')
    expect(within(historian).getByTestId('ag-state')).toHaveTextContent('Idle')
    expect(within(historian).getByTestId('ag-prompt')).toHaveTextContent('v3')
  })

  it('refetches on a system.agent_state event for its env and announces the change', async () => {
    const before = SLUGS.map((s) => agent(s))
    const after = SLUGS.map((s) => (s === 'historian' ? agent(s, { state: 'thinking' }) : agent(s)))
    const seen = fakeBackend([before, after])
    renderPage()
    await screen.findAllByTestId('ag-row')
    expect(seen.agents).toBe(1)

    // Another env's event is ignored.
    act(() => listeners.get('.system.agent_state')!.forEach((cb) => cb({ env: 'live', payload: { agent: 'historian' } })))
    expect(seen.agents).toBe(1)

    act(() => listeners.get('.system.agent_state')!.forEach((cb) => cb({ env: 'demo', payload: { agent: 'historian', state: 'thinking' } })))
    await waitFor(() => expect(seen.agents).toBe(2))
    const historian = (await screen.findAllByTestId('ag-row')).find((r) => r.dataset.slug === 'historian')!
    await waitFor(() => expect(within(historian).getByTestId('ag-state')).toHaveTextContent('Thinking'))
    expect(screen.getByTestId('ag-live')).toHaveTextContent('Historian, Thinking')
  })

  it('shows the empty state when the API has no agents', async () => {
    fakeBackend([[]])
    renderPage()
    expect(await screen.findByTestId('ag-empty')).toHaveTextContent('No agents yet')
  })

  // ATL-122: the HTTP status endpoint sends logged_in: null when it could not confirm sign-in
  // without probing the CLI; the panel must read that as "unknown", not "signed out".
  it('renders a null logged_in as unknown, not signed out', async () => {
    fakeBackend([SLUGS.map((s) => agent(s))], { ok: false, logged_in: null })
    renderPage()
    const panel = await screen.findByTestId('ag-claude')
    expect(panel).toHaveAttribute('data-view', 'login_unknown')
    expect(panel).toHaveTextContent("Couldn’t confirm Claude is signed in")
  })
})
