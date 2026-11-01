import type { WarroomMessage } from '../api'

/*
 * Portfolio Manager messages as the War Room API returns them (ATL-178): the daily report card from
 * atlas_agents/pm/daily_report.py (ATL-170) and the position_update card from
 * atlas_agents/pm/review.py::status_card. Figures follow the design's fixture day (design v14
 * wr-analysts.js). Shared by the Vitest tests and e2e/warroom-pm-cards.spec.ts.
 */

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'portfolio_manager',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 11840,
  tokens_out: 1310,
  cost_usd: '0.019000',
  unmatched_numbers: null,
}

export const BREACH = { limit: 'max_open_positions', used: '7', cap: '6', at: null }

export function dailyReportCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'daily_report',
    day: '2026-10-27',
    env: 'demo',
    as_of: '2026-10-27T20:30:00Z',
    window: { start: '2026-10-26T21:00:00Z', end: '2026-10-27T20:30:00Z' },
    equity: { start_usd: '5061204.10', end_usd: '5065230.16', net_flows_usd: '0.00', change_usd: '4026.06', change_pct: '0.08' },
    pnl: { realised_usd: '3204.50', fees_usd: '-186.22', funding_usd: '-54.62', net_realised_usd: '2963.66', unrealised_usd: '1062.40', bill_count: 14 },
    trade_count: 3,
    trades: [
      { inst_id: 'ETH-USDT-SWAP', side: 'long', size: '12', entry: '3920.5', exit: '4012.1', r: '1.40', pnl_usd: '1820.00', holding_minutes: 372, closing_rule: 'take_profit', opened_at: '2026-10-27T08:10:00Z', closed_at: '2026-10-27T14:22:00Z', proposal_id: '9a1f0c2e-0000-4000-8000-000000000001', origin: 'atlas' },
      { inst_id: 'NVDA-USDT-SWAP', side: 'long', size: '40', entry: '188.20', exit: '183.17', r: '-1.00', pnl_usd: '-1005.30', holding_minutes: 220, closing_rule: 'stop_loss', opened_at: '2026-10-27T13:00:00Z', closed_at: '2026-10-27T16:40:00Z', proposal_id: '9a1f0c2e-0000-4000-8000-000000000002', origin: 'atlas' },
      { inst_id: 'XAG-USDT-SWAP', side: 'short', size: '300', entry: '48.92', exit: '48.55', r: '0.60', pnl_usd: '410.20', holding_minutes: 545, closing_rule: 'time_stop', opened_at: '2026-10-27T06:00:00Z', closed_at: '2026-10-27T15:05:00Z', proposal_id: '9a1f0c2e-0000-4000-8000-000000000003', origin: 'atlas' },
    ],
    best_trade: { inst_id: 'ETH-USDT-SWAP', side: 'long', r: '1.40', pnl_usd: '1820.00', closing_rule: 'take_profit', note: 'TP2 hit after the ETF-flow headline' },
    worst_trade: { inst_id: 'NVDA-USDT-SWAP', side: 'long', r: '-1.00', pnl_usd: '-1005.30', closing_rule: 'stop_loss', note: 'Stopped at the US open on earnings chatter' },
    limits: {
      peak_daily_loss_pct: '0.40',
      max_daily_loss_pct: '4',
      peak_exposure_usd: '19500.00',
      peak_exposure_pct: '0.39',
      peak_exposure_at: '2026-10-27T12:12:00Z',
      peak_open_positions: 5,
      max_open_positions: '6',
    },
    breaches: [],
    tomorrow: {
      positions: [
        { inst_id: 'BTC-USDT-SWAP', side: 'long', size: '3', avg_px: '116842.3', mark_px: '119480.1', upl_usd: '6594.50' },
        { inst_id: 'XAU-USDT-SWAP', side: 'short', size: '4000', avg_px: '4118.4', mark_px: '4076.9', upl_usd: '1660.00' },
      ],
      events: [{ name: 'GDP Growth Rate QoQ Adv', country: 'USD', importance: 5, scheduled_at: '2026-10-28T12:30:00Z', forecast: '1.8%', previous: '3.8%' }],
    },
    notes: [],
    ...over,
  }
}

export function dailyReportMessage(over: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'm-daily-1027',
    kind: 'research_report',
    content_md: '**Daily report, Tuesday 27 October.** Equity +$4,026.06 (+0.08%) on three closed trades: two winners and one stop.',
    card: dailyReportCard(over),
    created_at: '2026-10-27T20:30:00.000000Z',
  }
}

/** The same day with open positions at 7 against a limit of 6: one breach. */
export function dailyReportBreachMessage(): WarroomMessage {
  return dailyReportMessage({
    limits: { ...(dailyReportCard().limits as Record<string, unknown>), peak_open_positions: 7 },
    breaches: [BREACH],
  })
}

export const BREAKEVEN_PROPOSAL_ID = '5b2e7d10-4f1a-4c6e-9a77-0e1d2c3b4a59'

export function positionUpdateCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'position_update',
    inst_id: 'XAU-USDT-SWAP',
    side: 'short',
    size: '4000',
    entry: '4118.4',
    mark: '4076.9',
    stop: '4161.0',
    initial_stop: '4161.0',
    targets: ['4075.8', '4033.2'],
    tp1: '4075.8',
    tp1_filled: false,
    pnl_usd: '1660.00',
    r: '0.97',
    atr_1h: '11.8',
    stop_distance_atr: '7.15',
    tp1_distance_atr: '0.09',
    opened_at: '2026-10-27T15:52:00Z',
    minutes_in_trade: 1133,
    funding_paid_usd: '-12.40',
    opening_proposal_id: '9a1f0c2e-0000-4000-8000-000000000004',
    actions: [
      { kind: 'amend_stop', rule: 'breakeven', stop: '4117.6', close_pct: null, proposal_id: BREAKEVEN_PROPOSAL_ID, status: 'submitted' },
    ],
    advisories: [],
    as_of: '2026-10-28T10:45:00Z',
    ...over,
  }
}

export function positionUpdateMessage(over: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'm-pu-xau',
    kind: 'position_update',
    tokens_in: 3120,
    tokens_out: 310,
    cost_usd: '0.004000',
    content_md: '**XAU-USDT-SWAP short reached +1.0R.** The breakeven rule fired, so I have proposed moving the stop to 4,117.6.',
    card: positionUpdateCard(over),
    created_at: '2026-10-28T10:45:00.000000Z',
  }
}
