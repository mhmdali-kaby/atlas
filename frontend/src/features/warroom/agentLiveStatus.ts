import { useTradingEnv } from '../../app/shell/tradingEnv'
import { useAgents, useAgentStateEvents, type AgentRow } from '../agents/api'
import type { AgentStatus } from './agents'

/*
 * The War Room's agent dots read the same GET /api/agents roster the Agents page shows (ATL-113),
 * refreshed on every system.agent_state, so the two pages never disagree. Until that list arrives
 * (or when it can't load) an agent is "offline".
 */

const SAME: ReadonlySet<string> = new Set<AgentStatus>(['idle', 'thinking', 'waiting', 'degraded', 'paused'])

/** One roster row to the War Room's status word. */
export function statusFromRow(row: Pick<AgentRow, 'enabled' | 'state'> | undefined): AgentStatus {
  if (!row || !row.enabled || row.state === 'disabled') return 'offline'
  if (row.state === 'rate_limited') return 'waiting'
  return SAME.has(row.state) ? (row.state as AgentStatus) : 'offline'
}

/** `(slug) => status` for the active environment. */
export function useAgentStatus(): (slug: string) => AgentStatus {
  const env = useTradingEnv()
  const { data } = useAgents(env)
  useAgentStateEvents(env)
  const rows = new Map((data?.data ?? []).map((row) => [row.slug, row]))
  return (slug) => statusFromRow(rows.get(slug))
}
