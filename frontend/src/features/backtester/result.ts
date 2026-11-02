/*
 * trading_core's POST /internal/backtest 200 body (docs/handoff/ATL-205-backtest-endpoint.md), as
 * ATL-208 stores it unchanged in GET /api/backtests/{id} → result. Every metric is a float fraction
 * of starting equity (equity starts at 1.0); several can be null (too few bars, a flat run).
 * Stored as jsonb, so `2.0` comes back as `2` and key order is not kept.
 */

export interface Metrics {
  start?: string
  end?: string
  bars?: number
  total_return: number | null
  cagr: number | null
  sharpe: number | null
  sortino: number | null
  max_drawdown: number | null
  n_trades: number
  win_rate: number | null
  /** null also means "no losing trades" when there were trades. */
  profit_factor: number | null
  fees_paid: number | null
  /** Negative = received. */
  funding_paid: number | null
}

export interface Fold {
  fold: number
  in_sample: Metrics
  out_of_sample: Metrics
}

export interface EquityPoint {
  ts: string
  equity: number
}

export interface Trade {
  side: 'long' | 'short'
  entry_ts: string
  exit_ts: string
  entry_price: number
  exit_price: number
  size?: number
  /** Fraction of starting equity, after fees and funding. */
  pnl: number
  /** vectorbt's trade return × 100, before funding. */
  return_pct: number
  fees: number
  funding?: number
}

export interface InstrumentResult {
  inst_id: string
  bar: string
  data_gap: boolean
  gap_notes: string[]
  full: Metrics
  folds: Fold[]
  is_sharpe_mean: number | null
  oos_sharpe_mean: number | null
  overfit_warning: boolean
  inst_type?: string
  asset_class?: string
  taker_fee_rate: number
  fee_source: string
  funding_included: boolean
  verdict_cap: string | null
  equity_curve: EquityPoint[]
  equity_curve_points?: number
  trades: Trade[]
  trades_total: number
  trades_truncated: boolean
}

export interface Excluded {
  inst_id: string
  reason: string
}

export interface BacktestResult {
  ok: true
  env: string
  bar: string
  splits: number
  slippage_bps: number
  n_trades: number
  data_gap: boolean
  overfit_warning: boolean
  instruments: InstrumentResult[]
  excluded: Excluded[]
  data_as_of: string
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

/**
 * The stored body narrowed to a BacktestResult, or null when it isn't one (a body from an older
 * trading_core, or a stub). Missing lists default to empty so one absent field can't blank the page.
 */
export function asResult(raw: unknown): BacktestResult | null {
  if (!isObj(raw) || !Array.isArray(raw.instruments)) return null
  const instruments = raw.instruments.filter((x): x is Record<string, unknown> => isObj(x) && typeof x.inst_id === 'string' && isObj(x.full))
  return {
    ...(raw as unknown as BacktestResult),
    overfit_warning: raw.overfit_warning === true,
    data_gap: raw.data_gap === true,
    excluded: Array.isArray(raw.excluded) ? (raw.excluded as Excluded[]) : [],
    instruments: instruments.map((x) => ({
      ...(x as unknown as InstrumentResult),
      gap_notes: Array.isArray(x.gap_notes) ? (x.gap_notes as string[]) : [],
      folds: Array.isArray(x.folds) ? (x.folds as Fold[]) : [],
      equity_curve: Array.isArray(x.equity_curve) ? (x.equity_curve as EquityPoint[]) : [],
      trades: Array.isArray(x.trades) ? (x.trades as Trade[]) : [],
      trades_total: typeof x.trades_total === 'number' ? x.trades_total : Array.isArray(x.trades) ? x.trades.length : 0,
      trades_truncated: x.trades_truncated === true,
      overfit_warning: x.overfit_warning === true,
      data_gap: x.data_gap === true,
      funding_included: x.funding_included === true,
    })),
  }
}

/** A fraction of starting equity in percent units, for SignedValue / formatPercent. */
export const pct = (v: number | null | undefined): number | null => (v === null || v === undefined || !Number.isFinite(v) ? null : v * 100)

/** "1, 2, 2.5 or 5 × a power of ten": round axis steps (the design's niceStep). */
export function niceStep(x: number): number {
  if (!(x > 0) || !Number.isFinite(x)) return 1
  const p = Math.pow(10, Math.floor(Math.log10(x)))
  const m = x / p
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p
}

/**
 * The design's value axis for the equity curve, in percent: a domain padded 12% beyond the
 * curve and 0, and round ticks at least a fifth of the range apart that always include 0%.
 */
export function percentAxis(values: readonly number[]): { lo: number; hi: number; ticks: number[]; digits: number } {
  let lo = Math.min(0, ...values)
  let hi = Math.max(0, ...values)
  const pad = (hi - lo) * 0.12 || 1
  lo -= pad
  hi += pad
  const step = niceStep((hi - lo) / 5)
  const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : 2
  const edge = (hi - lo) * (10 / 220)
  const ticks: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
    if (v - lo < edge || hi - v < edge) continue
    ticks.push(Math.abs(v) < step / 1e6 ? 0 : Number(v.toFixed(6)))
  }
  return { lo, hi, ticks, digits }
}

/** Where a Sharpe sits on the pair track, clamped to ±2.5 (design foldsTable), as 0–100. */
export const pairPos = (v: number | null, lim = 2.5): number => (v === null ? 50 : ((Math.max(-lim, Math.min(lim, v)) + lim) / (2 * lim)) * 100)
