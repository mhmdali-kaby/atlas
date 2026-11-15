import { render, screen } from '@testing-library/react'
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { AGENTS, type AgentStatus } from './agents'
import { statusFromRow } from './agentLiveStatus'
import { AgentList } from './SidebarSections'

const live = vi.hoisted(() => ({ statuses: {} as Record<string, AgentStatus> }))
vi.mock('./agentLiveStatus', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./agentLiveStatus')>()),
  useAgentStatus: () => (slug: string) => live.statuses[slug] ?? 'offline',
}))

/**
 * ATL-225: an AgentList row's accessible name used to be aria-label "<name>, Offline", which
 * dropped the role text the row shows on screen while offline — axe's label-content-name-mismatch
 * flagged it. The fix drops the aria-label and reads the visible name and role, then a hidden
 * span adds the status word on top.
 */
afterEach(async () => {
  live.statuses = {}
  await act(() => i18n.changeLanguage('en'))
})

describe('AgentList live status', () => {
  it('shows a running agent by its state and drops "All offline"', () => {
    live.statuses = { historian: 'thinking', news_intelligence: 'idle' }
    render(<AgentList onNavigate={() => {}} />)
    expect(screen.queryByText('All offline')).not.toBeInTheDocument()
    const historian = screen.getAllByRole('button').find((b) => b.dataset.slug === 'historian')!
    expect(historian).toHaveAttribute('data-status', 'thinking')
    expect(historian).toHaveTextContent('Thinking')
  })

  it('says "All offline" when no agent reports a state', () => {
    render(<AgentList onNavigate={() => {}} />)
    expect(screen.getByText('All offline')).toBeInTheDocument()
  })
})

describe('statusFromRow', () => {
  it('maps the Agents API state to the War Room status', () => {
    expect(statusFromRow(undefined)).toBe('offline')
    expect(statusFromRow({ enabled: false, state: 'idle' })).toBe('offline')
    expect(statusFromRow({ enabled: true, state: 'disabled' })).toBe('offline')
    expect(statusFromRow({ enabled: true, state: 'thinking' })).toBe('thinking')
    expect(statusFromRow({ enabled: true, state: 'idle' })).toBe('idle')
    expect(statusFromRow({ enabled: true, state: 'rate_limited' })).toBe('waiting')
    expect(statusFromRow({ enabled: true, state: 'something_new' })).toBe('offline')
  })
})

describe('AgentList', () => {
  it("names every row with its visible name and role, and includes the status", () => {
    render(<AgentList onNavigate={() => {}} />)
    const rows = screen.getAllByRole('button')
    expect(rows).toHaveLength(AGENTS.length)
    AGENTS.forEach((agent, i) => {
      const row = rows[i]!
      const name = i18n.getFixedT('en', 'warroom')(`agents.${agent.slug}.name`)
      const role = i18n.getFixedT('en', 'warroom')(`agents.${agent.slug}.role`)
      // Starts with the visible name, and includes the visible role text and the status word
      // (hidden, since the role is on screen instead while offline).
      expect(row).toHaveAccessibleName(new RegExp(`^${escapeRegExp(name)}`))
      expect(row).toHaveAccessibleName(new RegExp(escapeRegExp(role)))
      expect(row).toHaveAccessibleName(/Offline/)
    })
  })

  it('does the same in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    render(<AgentList onNavigate={() => {}} />)
    const rows = screen.getAllByRole('button')
    const name = i18n.getFixedT('ar', 'warroom')('agents.orchestrator.name')
    const role = i18n.getFixedT('ar', 'warroom')('agents.orchestrator.role')
    expect(rows[0]).toHaveAccessibleName(new RegExp(`^${escapeRegExp(name)}\\s+${escapeRegExp(role)}`))
    expect(rows[0]).toHaveAccessibleName(new RegExp(i18n.getFixedT('ar', 'warroom')('status.offline')))
  })
})

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
