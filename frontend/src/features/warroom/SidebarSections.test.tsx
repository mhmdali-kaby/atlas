import { render, screen } from '@testing-library/react'
import { act } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import { AGENTS } from './agents'
import { AgentList } from './SidebarSections'

/**
 * ATL-225: an AgentList row's accessible name used to be aria-label "<name>, Offline", which
 * dropped the role text the row shows on screen while offline — axe's label-content-name-mismatch
 * flagged it. The fix drops the aria-label and reads the visible name and role, then a hidden
 * span adds the status word on top.
 */
afterEach(async () => {
  await act(() => i18n.changeLanguage('en'))
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
