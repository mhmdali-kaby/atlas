import { keepPreviousData } from '@tanstack/react-query'
import { useApiQuery } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * Portfolio → Performance (ATL-175) over ATL-174's API (docs/handoff/ATL-174-portfolio-performance-api.md):
 * GET /api/portfolio/performance?env=&range=1M|3M|YTD|ALL. Every metric is {value, sample_size, reason};
 * value is a decimal string or null, and reason says why it is null. Weekday and hour are Baghdad time.
 */

export const PERF_RANGES = ['1M', '3M', 'YTD', 'ALL'] as const
export type PerfRange = (typeof PERF_RANGES)[number]

export type NullReason =
  | 'no_trades'
  | 'no_wins'
  | 'no_losses'
  | 'no_r'
  | 'insufficient_data'
  | 'zero_volatility'
  | 'no_downside'

export interface Metric {
  value: string | null
  sample_size: number
  reason: NullReason | null
}

export interface Drawdown {
  usd: string | null
  pct: string | null
  peak_usd: string | null
  trough_usd: string | null
  peak_at: string | null
  trough_at: string | null
  sample_size: number
  reason: NullReason | null
}

export interface Bucket {
  pnl_usd: string
  trades: number
  wins: number
  losses: number
}

export interface HeatCell extends Bucket {
  /** ISO weekday, 1 = Monday … 7 = Sunday, Baghdad time. */
  weekday: number
  /** 0..23, Baghdad time. */
  hour: number
}

export interface Performance {
  env: TradingEnv
  range: PerfRange
  from: string | null
  to: string
  timezone: string
  trades: {
    total: number
    wins: number
    losses: number
    flats: number
    excluded_non_usd: number
    gross_profit_usd: string
    gross_loss_usd: string
    net_pnl_usd: string
  }
  win_rate_pct: Metric
  avg_win_usd: Metric
  avg_loss_usd: Metric
  profit_factor: Metric
  expectancy_r: Metric
  max_drawdown: Drawdown
  sharpe: Metric
  sortino: Metric
  breakdowns: {
    by_instrument: Array<Bucket & { inst_id: string }>
    by_asset_class: Array<Bucket & { asset_class: string }>
    by_agent: Array<Bucket & { agent: string | null }>
    by_weekday: Array<Bucket & { weekday: number }>
    by_hour: Array<Bucket & { hour: number }>
    /** Only non-empty cells. */
    heatmap: HeatCell[]
  }
}

export function performancePath(env: TradingEnv, range: PerfRange): string {
  return `/api/portfolio/performance?${new URLSearchParams({ env, range }).toString()}`
}

export function usePerformance(env: TradingEnv, range: PerfRange) {
  return useApiQuery<{ data: Performance }>(['portfolio', env, 'performance', range], performancePath(env, range), {
    placeholderData: keepPreviousData,
  })
}
