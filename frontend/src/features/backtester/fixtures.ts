import type { BacktestResult, Fold, InstrumentResult, Metrics, Trade } from './result'

/* A done backtest result in trading_core's shape (ATL-205 handoff), for tests and stories. */

const H = 3_600_000

export function metrics(over: Partial<Metrics> = {}): Metrics {
  return {
    start: '2026-05-01T00:00:00Z',
    end: '2026-09-30T23:00:00Z',
    bars: 3672,
    total_return: 0.0832,
    cagr: 0.2104,
    sharpe: 1.21,
    sortino: 1.67,
    max_drawdown: -0.0614,
    n_trades: 18,
    win_rate: 0.5,
    profit_factor: 1.42,
    fees_paid: 0.017,
    funding_paid: 0.0061,
    ...over,
  }
}

function folds(pairs: Array<[number, number]>): Fold[] {
  const start = Date.parse('2026-05-01T00:00:00Z')
  const end = Date.parse('2026-09-30T23:00:00Z')
  const block = (end - start) / (pairs.length + 1)
  const iso = (ms: number) => new Date(Math.round(ms / H) * H).toISOString().replace('.000Z', 'Z')
  return pairs.map(([is, oos], i) => ({
    fold: i + 1,
    in_sample: metrics({ start: iso(start), end: iso(start + (i + 1) * block - H), sharpe: is, total_return: is * 0.021, n_trades: 4 + i * 4 }),
    out_of_sample: metrics({ start: iso(start + (i + 1) * block), end: iso(start + (i + 2) * block - H), sharpe: oos, total_return: oos * 0.013, n_trades: 4 + (i % 2) }),
  }))
}

function curve(n: number, drift: number, seed: number) {
  const start = Date.parse('2026-05-01T00:00:00Z')
  const end = Date.parse('2026-09-30T23:00:00Z')
  let s = seed
  let e = 1
  return Array.from({ length: n + 1 }, (_, i) => {
    const p = { ts: new Date(start + (i / n) * (end - start)).toISOString(), equity: e }
    s = (s * 48271) % 2147483647
    e *= 1 + drift + (s / 2147483647 - 0.5) * 0.016
    return p
  })
}

export function trades(n: number, price = 64000): Trade[] {
  const start = Date.parse('2026-05-02T00:00:00Z')
  return Array.from({ length: n }, (_, i) => {
    const ret = ((i * 37) % 9) - 3.5
    const entry = price + i * 10
    return {
      side: 'long',
      entry_ts: new Date(start + i * 8 * H).toISOString(),
      exit_ts: new Date(start + i * 8 * H + 5 * H).toISOString(),
      entry_price: entry,
      exit_price: entry * (1 + ret / 100),
      size: 0.01,
      pnl: (ret / 100) * 0.28,
      return_pct: ret,
      fees: 0.001,
      funding: 0.0001,
    }
  })
}

export function instrument(inst_id: string, over: Partial<InstrumentResult> = {}): InstrumentResult {
  return {
    inst_id,
    bar: '1H',
    data_gap: false,
    gap_notes: [],
    full: metrics(),
    folds: folds([
      [1.08, 0.94],
      [1.17, 1.32],
      [1.25, 0.88],
    ]),
    is_sharpe_mean: 1.17,
    oos_sharpe_mean: 1.05,
    overfit_warning: false,
    inst_type: 'SWAP',
    asset_class: 'crypto',
    taker_fee_rate: 0.0005,
    fee_source: 'config',
    funding_included: true,
    verdict_cap: null,
    equity_curve: curve(240, 0.0004, inst_id.length * 7),
    equity_curve_points: 3672,
    trades: trades(18),
    trades_total: 18,
    trades_truncated: false,
    ...over,
  }
}

export function doneResult(over: Partial<BacktestResult> = {}): BacktestResult {
  return {
    ok: true,
    env: 'demo',
    bar: '1H',
    splits: 3,
    slippage_bps: 2,
    n_trades: 36,
    data_gap: false,
    overfit_warning: false,
    instruments: [
      instrument('BTC-USDT-SWAP'),
      instrument('XAU-USDT-SWAP', {
        full: metrics({ total_return: -0.0215, cagr: -0.051, sharpe: -0.42, sortino: -0.6, max_drawdown: -0.0891, n_trades: 12, win_rate: 0.25, profit_factor: 0.71, fees_paid: 0.011, funding_paid: -0.0012 }),
        trades: trades(12, 2400),
        trades_total: 12,
      }),
    ],
    excluded: [{ inst_id: 'CL-USDT-SWAP', reason: 'no 1H candles in demo for the period' }],
    data_as_of: '2026-11-01T20:00:00Z',
    ...over,
  }
}

export function overfitResult(): BacktestResult {
  const btc = instrument('BTC-USDT-SWAP', {
    folds: folds([
      [1.71, 0.42],
      [1.48, 0.11],
      [1.39, 0.62],
    ]),
    is_sharpe_mean: 1.53,
    oos_sharpe_mean: 0.38,
    overfit_warning: true,
  })
  return doneResult({ overfit_warning: true, instruments: [btc], excluded: [] })
}
