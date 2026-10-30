import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useApiQuery } from '../../lib/api'
import { getEcho } from '../../lib/echo'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * ATL-113's Agents API (docs/handoff/ATL-113-agents-api.md): GET /api/agents lists the nine roster
 * agents with state, today's Baghdad-day usage, budget, prompt version and last run;
 * GET /api/agents/claude-status runs the claude CLI checks on every call, so it is fetched on page
 * open and on "Check again" only, never polled.
 */

export type AgentState = 'idle' | 'thinking' | 'waiting' | 'degraded' | 'rate_limited' | 'paused' | 'disabled'
export type RunStatus = 'running' | 'ok' | 'error' | 'degraded' | 'rate_limited' | 'over_budget'
export type ModelTier = 'strong' | 'standard' | 'routine'

export interface AgentRow {
  slug: string
  name: string
  handle: string
  enabled: boolean
  model: ModelTier | string
  model_name: string | null
  state: AgentState | string
  state_since: string | null
  current_task: string | null
  today: {
    tokens: number
    tokens_in: number
    tokens_out: number
    turns: number
    runs: number
    /** A 6-place decimal string: the SDK's figure, notional only (decision 242). */
    cost_usd_notional: string
  }
  budget: { daily_tokens: number; daily_turns: number }
  prompt: { sequence: number; sha: string; sha256?: string; created_at: string } | null
  last_run: {
    id?: string
    status: RunStatus | string
    trigger: string
    started_at: string
    ended_at: string | null
    prompt_version?: number | null
  } | null
}

export interface AgentsReport {
  data: AgentRow[]
  meta: {
    env: TradingEnv
    day: string
    timezone: string
    day_start: string
    day_end: string
    settings_version: number
  }
}

export interface ClaudeCheck {
  name: 'claude_cli' | 'claude_cli_login' | 'claude_api_key' | string
  ok: boolean
  status: 'pass' | 'fail' | 'warn' | 'skip' | string
  detail: string
}

export interface ClaudeStatus {
  ok: boolean
  installed: boolean
  logged_in: boolean
  api_key_env_clear: boolean
  login_command: string
  checks: ClaudeCheck[]
  checked_at: string
}

export const agentsKey = (env: TradingEnv) => ['agents', env] as const
export const CLAUDE_KEY = ['agents', 'claude-status'] as const

/** A fallback poll in case a state event is missed (the bridge drops nothing it stores, but Reverb may be down). */
export const POLL_MS = 60_000

export function useAgents(env: TradingEnv) {
  return useApiQuery<AgentsReport>(agentsKey(env), `/api/agents?env=${env}`, {
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  })
}

export function useClaudeStatus() {
  return useApiQuery<{ data: ClaudeStatus }>(CLAUDE_KEY, '/api/agents/claude-status', {
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })
}

/**
 * Refetches the agents list on every `system.agent_state` (a run starts or ends, an agent is
 * enabled or disabled; ATL-83/ATL-114) and on `system.settings_changed` (budgets and models), both
 * broadcast on private-system by atlas:bridge.
 */
export function useAgentStateEvents(env: TradingEnv) {
  const qc = useQueryClient()
  useEffect(() => {
    let channel: ReturnType<ReturnType<typeof getEcho>['private']>
    try {
      channel = getEcho().private('system')
    } catch {
      return undefined
    }
    const refresh = (e: { env?: string } | undefined) => {
      if (e?.env && e.env !== env) return
      void qc.invalidateQueries({ queryKey: agentsKey(env) })
    }
    channel.listen('.system.agent_state', refresh)
    channel.listen('.system.settings_changed', refresh)
    return () => {
      channel.stopListening('.system.agent_state', refresh)
      channel.stopListening('.system.settings_changed', refresh)
    }
  }, [qc, env])
}
