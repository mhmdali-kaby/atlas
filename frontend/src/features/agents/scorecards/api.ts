import { keepPreviousData } from '@tanstack/react-query'
import { useApiQuery } from '../../../lib/api'
import type { TradingEnv } from '../../../app/shell/tradingEnv'

/*
 * ATL-200's GET /api/agents/scores?days=7|30|90 (docs/handoff/ATL-200-journal-scores-api.md):
 * every roster agent with an n-weighted summary and its daily agent_scores rows for the last
 * `days` Baghdad days ending today. `days[].data` is each scorer's own object (ATL-110, ATL-195,
 * ATL-196, ATL-197, ATL-198), passed through unchanged; the card reads its keys per agent.
 */

export type ScoreDays = 7 | 30 | 90
export const SCORE_DAYS: readonly ScoreDays[] = [7, 30, 90]

/** A scorer value: JSON numbers from Python, decimal strings for the reliability weight. */
export type ScoreValue = number | string | null | undefined

export interface ScoreDay {
  /** Baghdad day, YYYY-MM-DD. */
  day: string
  n: number
  hit_rate: string | null
  brier_score: string | null
  data: Record<string, unknown>
}

export interface AgentScores {
  agent: string
  name: string
  summary: {
    n: number
    days_scored: number
    hit_rate: string | null
    brier_score: string | null
    reliability_weight: string | null
    reliability_weight_day: string | null
  }
  days: ScoreDay[]
  /** ATL-204: the P&L of the trades the agent led, when the API sends it. */
  pnl_attributed_usd?: string | null
}

export interface ScoresReport {
  data: AgentScores[]
  meta: { env: TradingEnv; days: ScoreDays; from: string; to: string; timezone: string }
}

export const scoresKey = (env: TradingEnv, days: ScoreDays) => ['agents', 'scores', env, days] as const

/** The scores for one period. The previous period stays on screen (dimmed) while the next loads. */
export function useAgentScores(env: TradingEnv, days: ScoreDays) {
  return useApiQuery<ScoresReport>(scoresKey(env, days), `/api/agents/scores?days=${days}`, {
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}
