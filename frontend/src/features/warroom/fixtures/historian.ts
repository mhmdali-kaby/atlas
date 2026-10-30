import type { WarroomMessage } from '../api'

/*
 * Historian messages as the War Room API returns them (ATL-89 WarroomMessageResource), with the
 * card shapes from docs/handoff/ATL-93-historian-agent.md: a research_report reply whose card is
 * a HistoryVerdict + meta {unmatched_numbers, checked, verdict_corrections}, and the 07:30
 * regime_table. Shared by the Vitest tests and the Playwright spec (e2e/historian-cards.spec.ts).
 */

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'historian',
  reply_to: null,
  mentions: [],
  reasoning_md: 'Find analogs of a 3-day 10% drop.',
  tool_calls: [],
  tokens_in: 900,
  tokens_out: 200,
  cost_usd: '0.000000',
  unmatched_numbers: null,
}

/** The ATL-93 handoff example: n = 12, capped from Supports, one untraced number in the text and one in the card. */
export function verdictMessage(over: Partial<WarroomMessage> = {}, card: Record<string, unknown> = {}): WarroomMessage {
  const unmatched = [
    { raw: '4.7%', value: 4.7, kind: 'percent', location: 'text', span: [62, 66], path: null },
    { raw: '+7.7%', value: 7.7, kind: 'percent', location: 'card', span: [37, 42], path: 'key_stat' },
  ]
  return {
    ...BASE,
    id: 'msg-verdict-btc',
    kind: 'research_report',
    content_md: 'History weakly supports a bounce: 24h median +1.2%, best decile 4.7% above entry.',
    card: {
      type: 'history_verdict',
      question: 'BTC after a 3-day 10% drop',
      inst_id: 'BTC-USDT-SWAP',
      bar: '1H',
      period: '2024-10-30..2026-10-30',
      n: 12,
      distribution: {
        '24h': { median: 0.012, p10: -0.031, p90: 0.054, pct_positive: 61.8, n: null },
        '1h': { median: 0.0011, p10: -0.0062, p90: 0.0079, pct_positive: 55.0, n: null },
        '72h': { median: 0.021, p10: -0.052, p90: 0.088, pct_positive: 63.6, n: null },
        '4h': { median: 0.0034, p10: -0.0141, p90: 0.0187, pct_positive: 58.3, n: null },
      },
      base_rate: {
        '1h': { median: 0.0001, p10: -0.0071, p90: 0.0073, pct_positive: 50.4, n: null },
        '4h': { median: 0.0004, p10: -0.0158, p90: 0.0162, pct_positive: 51.0, n: null },
        '24h': { median: 0.002, p10: -0.035, p90: 0.04, pct_positive: 52.1, n: null },
      },
      regime: { label: 'trend_down', probability: 0.71, days_in_regime: 3.2 },
      stop_noise_check: null,
      overfit_warning: false,
      verdict: 'weakly_supports',
      key_stat: '24h median +1.2% vs base +0.2%; +7.7% best case',
      data_quality_notes: [],
      data_as_of: '2026-10-30T04:00:00Z',
      meta: {
        unmatched_numbers: unmatched,
        checked: 14,
        verdict_corrections: [{ from: 'supports', to: 'weakly_supports', rule: 'n_below_20', n: 12 }],
      },
      ...card,
    },
    unmatched_numbers: unmatched,
    created_at: '2026-10-30T09:12:03.000000Z',
    ...over,
  }
}

/** ATL-99: a 5-case "contradicts" turned into does_not_support, with a stop inside noise and an overfit backtest. */
export function changedVerdictMessage(): WarroomMessage {
  return verdictMessage(
    { id: 'msg-verdict-tsla', content_md: 'Too few cases to say history contradicts the short.', unmatched_numbers: [] },
    {
      question: 'TSLA short after an earnings gap up',
      inst_id: 'TSLA-USDT-SWAP',
      bar: '4H',
      period: '2025-06-02..2026-10-29',
      n: 5,
      distribution: { '24h': { median: 0.008, p10: -0.02, p90: 0.031, pct_positive: 60, n: null } },
      base_rate: {},
      regime: { label: 'range', probability: null, days_in_regime: 0.4 },
      stop_noise_check: { stop_distance_atr: 1.1, median_4h_range_atr: 1.5, verdict: 'inside_noise' },
      overfit_warning: true,
      verdict: 'does_not_support',
      key_stat: '3 of 5 rose over 24h',
      data_quality_notes: ['Reference data before 2026-03-01 (not OKX prices).', 'One 4H gap on 2026-08-14, repaired.'],
      meta: {
        unmatched_numbers: [],
        checked: 9,
        verdict_corrections: [{ from: 'contradicts', to: 'does_not_support', rule: 'small_sample', n: 5 }],
      },
    },
  )
}

/** The sparse card: no matching cases (status no_data, n = 0). */
export function noDataVerdictMessage(): WarroomMessage {
  return verdictMessage(
    { id: 'msg-verdict-eth', content_md: 'No weekday pattern to report.', unmatched_numbers: [] },
    {
      question: 'ETH on Mondays after a red weekend',
      inst_id: 'ETH-USDT-SWAP',
      bar: '1D',
      period: null,
      n: 0,
      status: 'no_data',
      distribution: {},
      base_rate: {},
      regime: null,
      verdict: 'does_not_support',
      key_stat: 'No matching weekends in the history searched',
      meta: { unmatched_numbers: [], checked: 0, verdict_corrections: [] },
    },
  )
}

const LABELS = ['trend_up', 'range', 'trend_down', 'high_vol', 'low_vol', 'range', 'trend_up', 'insufficient_data']
const BASES = [
  'BTC', 'ETH', 'SOL', 'XAU', 'TSLA', 'NVDA', 'DOGE', 'XRP', 'SPY', 'QQQ', 'AAPL', 'MSFT', 'XAG', 'BNB', 'ADA',
  'AVAX', 'LINK', 'DOT', 'LTC', 'TRX', 'TON', 'SUI', 'APT', 'ARB', 'OP', 'NEAR', 'ATOM', 'FIL', 'INJ', 'TIA',
  'SEI', 'PEPE', 'WIF', 'SHIB', 'BCH', 'ETC', 'UNI', 'AAVE', 'MKR', 'LDO', 'CRV', 'GOOGL', 'META', 'AMZN', 'COIN',
  'MSTR', 'HOOD', 'PLTR', 'AMD', 'TSM', 'CL', 'NG', 'XCU', 'US100', 'IWM', 'ORCL', 'LLY', 'COST', 'JUP', 'ENA',
]

/** 60 rows in volume order; probabilities are deterministic and every 7th row is rule-based. */
export function regimeTableMessage(): WarroomMessage {
  const rows = BASES.map((base, i) => {
    const label = LABELS[i % LABELS.length]!
    const none = label === 'insufficient_data'
    return {
      inst_id: `${base}-USDT-SWAP`,
      label,
      probability: none || i % 7 === 3 ? null : Math.round(((37 * i + 11) % 50) * 100 + 5000) / 10000,
      days_in_regime: none ? null : Math.round((((i * 13) % 29) / 3) * 100) / 100,
      method: none ? null : i % 7 === 3 ? 'fallback' : 'hmm',
    }
  })
  return {
    ...BASE,
    id: 'msg-regime-table',
    kind: 'regime_table',
    content_md: 'Regime table: crypto majors mostly trending; metals ranging.',
    card: { type: 'regime_table', rows, data_as_of: '2026-10-30T00:00:00Z', meta: { unmatched_numbers: [], checked: 3, verdict_corrections: [] } },
    reasoning_md: null,
    created_at: '2026-10-30T04:31:00.000000Z',
  }
}
