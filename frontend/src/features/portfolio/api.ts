import { keepPreviousData } from '@tanstack/react-query'
import { useApiQuery } from '../../lib/api'
import type { TradingEnv } from '../../app/shell/tradingEnv'

/*
 * The ATL-12 Portfolio API contract (docs/handoff/ATL-12-portfolio-api.md).
 * Every amount is a decimal string; timestamps are ISO-8601 UTC.
 */

/** How often the Overview re-reads Postgres (trading_core syncs balances every 60 s). */
export const POLL_MS = 30_000

export const RANGES = ['1D', '1W', '1M', '3M', 'YTD', 'ALL'] as const
export type EquityRange = (typeof RANGES)[number]

export interface PortfolioSummary {
  env: TradingEnv
  total_equity_usd: string | null
  net_deposits_usd: string
  total_pnl_usd: string | null
  total_pnl_pct: string | null
  pnl_since: string | null
  today_pnl_usd: string | null
  unrealised_pnl_usd: string | null
  fees_paid_usd: string
  funding_net_usd: string
  equity_as_of: string | null
  reconciled_at: string | null
  stale: boolean
}

export interface EquityPoint {
  ts: string
  equity_usd: string
}

export interface CashflowMarker {
  id: string
  kind: 'deposit' | 'withdrawal'
  ccy: string
  amount: string
  amount_usd: string | null
  state: string
  tx_id: string | null
  ts: string
}

export interface EquitySeries {
  range: EquityRange
  from: string | null
  to: string
  points: EquityPoint[]
  markers: CashflowMarker[]
}

/** One open position from the newest positions tick (ATL-12). Decimal strings; `size` is in contracts. */
export interface Position {
  id: string
  inst_id: string
  side: 'long' | 'short'
  pos_side: string
  size: string
  leverage: string | null
  entry: string | null
  mark: string | null
  liq_px: string | null
  margin: string | null
  settle_ccy: string | null
  /** null when the settle currency is not USD-like. */
  upl_usd: string | null
  /** OKX uplRatio × 100: return on margin. */
  upl_pct: string | null
  margin_mode: string | null
  opened_at: string | null
  /** Phase 2 (always null until the Executor exists); not shown. */
  stop: null
  take_profits: null
  agent: null
  proposal_id: null
}

export interface PositionsPayload {
  taken_at: string | null
  items: Position[]
}

export interface ConnectionSummary {
  id: string
  exchange: string
  env: TradingEnv
  uid_last4: string | null
  last_verified_at: string | null
}

export const portfolioKeys = {
  summary: (env: TradingEnv) => ['portfolio', env, 'summary'] as const,
  equity: (env: TradingEnv, range: EquityRange) => ['portfolio', env, 'equity', range] as const,
  positions: (env: TradingEnv) => ['portfolio', env, 'positions'] as const,
  connections: ['connections'] as const,
}

export function useSummary(env: TradingEnv) {
  return useApiQuery<{ data: PortfolioSummary }>(portfolioKeys.summary(env), `/api/portfolio/summary?env=${env}`, {
    refetchInterval: POLL_MS,
  })
}

export function useEquity(env: TradingEnv, range: EquityRange) {
  return useApiQuery<{ data: EquitySeries }>(
    portfolioKeys.equity(env, range),
    `/api/portfolio/equity?env=${env}&range=${range}`,
    { refetchInterval: POLL_MS, placeholderData: keepPreviousData },
  )
}

export function usePositions(env: TradingEnv) {
  return useApiQuery<{ data: PositionsPayload }>(portfolioKeys.positions(env), `/api/portfolio/positions?env=${env}`, {
    refetchInterval: POLL_MS,
  })
}

export function useConnections() {
  return useApiQuery<{ data: ConnectionSummary[] }>(portfolioKeys.connections, '/api/connections', {
    staleTime: 5 * 60_000,
  })
}
