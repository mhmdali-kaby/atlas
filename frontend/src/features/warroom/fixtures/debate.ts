import type { WarroomMessage } from '../api'

/*
 * Debate messages as the War Room API returns them (ATL-165), in the services' own card shapes:
 * da_review from docs/handoff/ATL-159-devils-advocate.md, vote_summary from ATL-154, risk_check
 * from ATL-127/ATL-160 (trading_core's decision body plus `type`). The numbers are the design's
 * fixture day (design v11 Phase 2 §3): SOL long, version 1 reviewed, re-rated on version 2, the vote
 * 3.23 ÷ 3.59 = 0.90, and the risk check on 12.02 contracts. Shared by Vitest and Playwright
 * (e2e/warroom-debate-cards.spec.ts).
 */

export const PROPOSAL_ID = '8b0c3f4e-5a61-4c1e-9f2d-6a7b8c9d0e12'
export const PROPOSAL_MSG_V1 = 'msg-prop-v1'
export const PROPOSAL_MSG_V2 = 'msg-prop-v2'
export const DA_MSG = 'msg-da-r1'

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at' | 'author'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 6120,
  tokens_out: 840,
  cost_usd: '0.031000',
  unmatched_numbers: null,
}

type Item = { status: 'ok' | 'problem'; severity: 'none' | 'minor' | 'serious' | 'loss_gt_1r'; detail: string }
const NAMES = ['event_risk', 'crowding', 'history', 'stop_quality', 'chasing', 'correlation', 'liquidity', 'priced_in', 'data_quality', 'overconfidence']
const ok = (detail: string): Item => ({ status: 'ok', severity: 'none', detail })
const problem = (severity: Item['severity'], detail: string): Item => ({ status: 'problem', severity, detail })

/** Round 1 on version 1, rated 4 of 5: two serious problems (CPI inside the window, the BTC overlap). */
export function daReviewCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  const items: Item[] = [
    problem('serious', 'US CPI at 15:30 Baghdad lands 1h into the 24h window, with the stop 1.1× the median 4h range.'),
    ok('58% long, not extreme (Derivatives, 14:05).'),
    ok('Analog hit rate 48%, n = 31.'),
    ok('1.1× the median 4h range, above the 0.8× noise floor.'),
    ok('Entry 0.6 ATR from the breakout level.'),
    problem('serious', 'BTC long open, 30-day correlation 0.82 against the 0.8 limit; 30% of equity on crypto beta.'),
    ok('4 bps slippage at size.'),
    problem('minor', 'SOL is up 1.4 ATR since the SEC headline at 12:04.'),
    ok('OK'),
    ok('52% against a 48% base rate, +4 points with a reason.'),
  ]
  return {
    type: 'da_review',
    proposal_id: PROPOSAL_ID,
    version: 1,
    inst_id: 'SOL-USDT-SWAP',
    round: 1,
    rerate_of: null,
    items: items.map((x, i) => ({ item: i + 1, name: NAMES[i], ...x })),
    strongest_objection: 'The BTC long is still open and SOL’s 30-day correlation with it is 0.82, above 0.8. Together they put 30% of equity on crypto beta.',
    risk_rating: 4,
    fix: 'Cut the size to 3% of equity, or trim the BTC long first.',
    fix_applied: false,
    vote: 'change',
    confidence: 0.7,
    reason: 'Risk 4/5. Cut the size to 3%.',
    precomputed: {},
    corrections: [],
    meta: { elapsed_seconds: 12.4, late: false },
    ...over,
  }
}

/** Round 2 on version 2: 2 of 5, take; correlation and event risk now OK. */
export function daRerateCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  const base = daReviewCard()
  const items = (base.items as Array<Record<string, unknown>>).map((x) =>
    x.item === 1 || x.item === 6 ? { ...x, status: 'ok', severity: 'none', detail: 'OK' } : x,
  )
  return { ...base, version: 2, round: 2, rerate_of: DA_MSG, items, risk_rating: 2, vote: 'take', confidence: 0.75, fix: null, fix_applied: true, ...over }
}

export function daReviewMessage(over: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: DA_MSG,
    author: 'devils_advocate',
    kind: 'research_report',
    content_md: '**4 of 5: two serious problems.** The open BTC long makes this a second bet on crypto beta, and CPI lands inside the window.',
    card: daReviewCard(over),
    reply_to: PROPOSAL_MSG_V1,
    created_at: '2026-10-28T11:16:00.000000Z',
  }
}

export function daRerateMessage(): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-da-r2',
    author: 'devils_advocate',
    kind: 'research_report',
    content_md: 'Re-rated version 2: **2 of 5.** Take, 75% confident.',
    card: daRerateCard(),
    reply_to: DA_MSG,
    created_at: '2026-10-28T11:20:00.000000Z',
  }
}

const voter = (agent: string, vote: string, confidence: string, reliability: string, weight: string, points: string, score: string, extra: Record<string, unknown> = {}) => ({
  agent,
  vote,
  missing: false,
  confidence,
  reliability,
  weight,
  points,
  score,
  voted_version: 2,
  fix: null,
  fix_applied: false,
  ...extra,
})

/** Everyone voted (3.23 ÷ 3.59 = 0.90), or News Intelligence missing (2.69 ÷ 3.55 = 0.76). */
export function voteSummaryCard(missing = false): Record<string, unknown> {
  const news = missing
    ? { agent: 'news_intelligence', vote: 'skip', missing: true, confidence: '0.5', reliability: '1', weight: '0.5', points: '0', score: '0', voted_version: null, fix: null, fix_applied: false }
    : voter('news_intelligence', 'take', '0.6', '0.9', '0.54', '1', '0.54')
  return {
    type: 'vote_summary',
    proposal_id: PROPOSAL_ID,
    version: 2,
    cycle_id: null,
    kind: 'entry',
    inst_id: 'SOL-USDT-SWAP',
    side: 'buy',
    result: 'passed',
    ratio: missing ? '0.7577' : '0.8997',
    threshold: '0.6',
    weighted_score: missing ? '2.69' : '3.23',
    total_weight: missing ? '3.55' : '3.59',
    da_rating: 2,
    da_max_rating: 4,
    reasons: [],
    voters: [
      voter('technical_analyst', 'take', '0.8', '1.1', '0.88', '1', '0.88'),
      voter('derivatives_sentiment', 'take', '0.7', '1', '0.7', '1', '0.7'),
      news,
      voter('historian', 'change', '0.6', '1.2', '0.72', '0.5', '0.36', { voted_version: 1, fix: 'widen the stop to 241.80', fix_applied: true }),
      voter('devils_advocate', 'take', '0.75', '1', '0.75', '1', '0.75'),
    ],
  }
}

export function voteSummaryMessage(missing = false): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-vote',
    author: 'orchestrator',
    kind: 'vote_summary',
    content_md: missing ? '**Passed: 0.76 against the 0.60 threshold.** News Intelligence didn’t vote in time, so it counts as a skip.' : '**Passed: 0.90 against the 0.60 threshold.**',
    card: voteSummaryCard(missing),
    created_at: '2026-10-28T11:22:00.000000Z',
  }
}

type Mode = 'approved' | 'resized' | 'rejected'
const NAMES9 = ['env_and_kill_switch', 'instrument_allowed', 'proposal_valid', 'position_size', 'portfolio', 'loss_limits', 'liquidation_buffer', 'event_blackout', 'autonomy']
const DETAILS = [
  'Trading on, no breaker, demo connection, market data 2 s old.',
  'Crypto perpetuals on, not blocked, live.',
  'Stop on the right side, 3.05% away or 1.1× ATR(1H), within 0.8× and 8%. Expected 1.80R meets the 1.8R minimum.',
  'Resized by max_usdt_per_trade: 12.02 contracts, 2997.79 USD.',
  '5 open positions of 6. Correlated crypto exposure 28% of 35%.',
  'Today −0.4% of the 4% daily limit, this week −1.1% of 10%, no losing streak.',
  'Liquidation at 166.80 sits 9.9× the stop distance beyond the stop; 2.0× is needed.',
  'No importance-5 event in the next 15 minutes.',
  'The owner approves each trade.',
]

export function riskCheckCard(mode: Mode = 'resized'): Record<string, unknown> {
  const rejectedAt = mode === 'rejected' ? 6 : null
  const caps = { notional_by_risk: '4210.55', max_position_pct: '5000', max_usdt_per_trade: '3000', max_new_exposure_per_day: '20000', max_share_per_instrument: '25000', strategist_suggestion: mode === 'approved' ? '2997.79' : '4000' }
  const checks = NAMES9.map((check, i) => {
    const n = i + 1
    if (rejectedAt !== null && n > rejectedAt) {
      return { n, check, status: 'not_run', passed: null, code: null, detail: 'Not run: an earlier check rejected the proposal.', values: {} }
    }
    if (n === rejectedAt) {
      return { n, check, status: 'failed', passed: false, code: 'daily_loss_limit', detail: 'Today’s loss is 4.1% of equity, over the 4% daily limit.', values: {} }
    }
    const values: Record<string, unknown> = n === 4 ? { contracts: '12.02', final_notional_usd: '2997.79', margin_usd: '999.27', leverage: 3, caps, binding_cap: mode === 'approved' ? 'strategist_suggestion' : 'max_usdt_per_trade' } : {}
    const detail = n === 4 && mode === 'approved' ? '12.02 contracts, 2997.79 USD at 3×.' : DETAILS[i]
    return { n, check, status: 'passed', passed: true, code: null, detail, values }
  })
  const rejected = mode === 'rejected'
  return {
    type: 'risk_check',
    proposal_id: PROPOSAL_ID,
    risk_check_id: '5d0e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f70',
    env: 'demo',
    settings_version: 6,
    result: mode,
    decision: rejected ? null : 'awaiting_owner',
    reason: rejected ? 'Today’s loss is 4.1% of equity, over the 4% daily limit.' : 'Resized by max_usdt_per_trade: 12.02 contracts, 2997.79 USD.',
    failed_check: rejected ? 'loss_limits' : null,
    binding_cap: rejected ? null : mode === 'approved' ? 'strategist_suggestion' : 'max_usdt_per_trade',
    final_contracts: rejected ? null : '12.02',
    final_notional_usd: rejected ? null : '2997.79',
    leverage: rejected ? null : 3,
    checks,
  }
}

export function riskCheckMessage(mode: Mode = 'resized'): WarroomMessage {
  return {
    ...BASE,
    id: `msg-risk-${mode}`,
    author: 'orchestrator',
    kind: 'risk_check',
    content_md: mode === 'rejected' ? '**Rejected at check 6.** Today’s loss is 4.1% of equity, over the 4% daily limit, so no order goes to OKX.' : '**Resized: $2,997.79 instead of $4,000.00.** Max per trade set the size.',
    card: riskCheckCard(mode),
    created_at: '2026-10-28T11:23:04.000000Z',
  }
}
