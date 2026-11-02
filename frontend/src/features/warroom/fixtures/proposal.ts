import type { WarroomMessage } from '../api'
import type { ProposalDetail, RiskCheckEntry } from '../cards/proposal'

/*
 * A Strategist proposal as the War Room API and /api/proposals return it (ATL-153 card,
 * ATL-162 resource). The numbers are the design's fixture day (design v11 wr-proposal): a SOL
 * long, entry zone 248.60–250.20, stop 241.80, three targets, 3,000 USD after the risk check on a
 * 100,000 USD sizing equity. Shared by the Vitest tests and e2e/approve-proposal.spec.ts.
 */

export const PROPOSAL_ID = '8b0c3a50-1f6e-4c3e-9a5a-2f1d7a0c4e12'
export const DA_MESSAGE_ID = 'm-da-412'
export const EXPIRES_AT = '2026-10-28T11:38:00.000000Z'
export const CHECKED_AT = '2026-10-28T11:23:00.000000Z'

const TPS = [
  { price: '258.5', pct: '50' },
  { price: '264.6', pct: '30' },
  { price: '272.2', pct: '20' },
]

export function proposalMessage(over: Partial<WarroomMessage> = {}): WarroomMessage {
  return {
    id: 'm-prop-412',
    thread_id: 'thread-main',
    author_type: 'agent',
    author: 'strategist',
    kind: 'proposal',
    content_md: '**BUY SOL-USDT-SWAP** v2: limit 248.60–250.20, stop 241.80, three targets, 3% of equity.',
    card: {
      type: 'proposal',
      proposal_id: PROPOSAL_ID,
      version: 2,
      parent_id: null,
      cycle_id: null,
      kind: 'entry',
      author: 'strategist',
      status: 'under_review',
      inst_id: 'SOL-USDT-SWAP',
      side: 'buy',
      order_type: 'post_only',
      entry: null,
      entry_zone: ['248.60', '250.20'],
      stop: '241.80',
      take_profits: TPS,
      time_stop_minutes: 1440,
      size_suggestion_pct_equity: '3',
      close_pct: null,
      leverage_suggestion: 3,
      expected_r_multiple: '1.95',
      probability_estimate: '0.52',
      rationale_md: 'Spot SOL ETF approval plus a 1H breakout on fresh longs that aren’t crowded yet.',
      invalidation: '1H close below 244.00',
      evidence: [],
      checklist: [],
      created_at: '2026-10-28T11:19:00+00:00',
    },
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: [],
    tokens_in: 4810,
    tokens_out: 690,
    cost_usd: '0.024000',
    unmatched_numbers: null,
    created_at: '2026-10-28T11:19:00.000000Z',
    ...over,
  }
}

/** ATL-189: the Strategist's odds basis (design v11: "Historian analogs 48%, +4 for the ETF catalyst"). */
export const BASIS = { analog_hit_rate: '0.48', n: 31, adjustment_pts: '4', reason: 'for the ETF catalyst' } as const

/**
 * The autonomy check's own stored values (decision 254, ATL-212/219): check 9 for an entry, the
 * `autonomy` check for a position action. Raw, as trading_core writes `risk_checks.checks[*].values`.
 */
export function autonomyCheck(n: number, values: Record<string, unknown>): RiskCheckEntry {
  return { n, check: 'autonomy', passed: true, code: null, detail: 'Autonomy (ATL-219 fixture).', values }
}

export function proposalDetail(over: Partial<ProposalDetail> = {}): ProposalDetail {
  return {
    id: PROPOSAL_ID,
    env: 'demo',
    version: 2,
    parent_id: null,
    kind: 'entry',
    author: 'strategist',
    inst_id: 'SOL-USDT-SWAP',
    side: 'buy',
    order_type: 'post_only',
    entry: null,
    entry_zone: ['248.6', '250.2'],
    stop: '241.8',
    take_profits: TPS,
    time_stop_minutes: 1440,
    size_suggestion_pct: '3',
    leverage_suggestion: 3,
    expected_r: '1.95',
    probability: '0.52',
    rationale_md: 'Spot SOL ETF approval plus a 1H breakout on fresh longs that aren’t crowded yet.',
    invalidation: '1H close below 244.00',
    status: 'awaiting_owner',
    status_reason: null,
    owner_note: null,
    decided_by: null,
    decided_at: null,
    approval_expires_at: EXPIRES_AT,
    created_at: '2026-10-28T11:19:00.000000Z',
    updated_at: CHECKED_AT,
    risk_check: {
      id: 'rc-412',
      result: 'resized',
      decision: 'awaiting_owner',
      reason: 'Resized by max_usdt_per_trade: 12.02 contracts, 2997.79 USD.',
      final_contracts: '12.02',
      final_notional_usd: '2997.79',
      leverage: 3,
      checks: [
        {
          n: 4,
          check: 'position_size',
          passed: true,
          code: null,
          detail: 'Resized by max_usdt_per_trade.',
          values: {
            entry: '249.4',
            stop: '241.8',
            stop_dist_pct: '0.03047313552526062550120288693',
            final_notional_usd: '2997.79',
            margin_usd: '999.27',
            caps: { notional_by_risk: '31800', max_usdt_per_trade: '3000', strategist_suggestion: '3000' },
            leverage: 3,
            equity: '5000000',
            sizing_equity: '100000',
            fee_rate: '-0.0005',
          },
        },
        {
          n: 7,
          check: 'liquidation_buffer',
          passed: true,
          code: null,
          detail: 'The liquidation price is far enough beyond the stop.',
          values: {
            entry: '249.4',
            stop: '241.8',
            leverage: 3,
            maint_margin_ratio: '0.002',
            estimated_liq_price: '166.765466666666666666666667',
            required_buffer: '15.2',
            liq_buffer_mult: '2',
            buffer_beyond_stop: '75.034533333333333333333333',
          },
        },
      ],
      created_at: CHECKED_AT,
    },
    actions: { approve: true, approve_blocked_reason: null, edit: true, reject: true, ask_team: true },
    versions: [
      { id: 'p-v1', version: 1, author: 'strategist', status: 'cancelled', status_reason: null, stop: '241.8', size_suggestion_pct: '5', take_profits: TPS, created_at: '2026-10-28T11:12:00.000000Z' },
      { id: PROPOSAL_ID, version: 2, author: 'strategist', status: 'awaiting_owner', status_reason: null, stop: '241.8', size_suggestion_pct: '3', take_profits: TPS, created_at: '2026-10-28T11:19:00.000000Z' },
    ],
    da_review: {
      message_id: DA_MESSAGE_ID,
      proposal_id: PROPOSAL_ID,
      risk_rating: 2,
      content_md: 'Risk 2 of 5.',
      card: { type: 'da_review', version: 2, risk_rating: 2, strongest_objection: 'The BTC long is 0.82 correlated; the size cut to 3% resolves it.' },
      created_at: '2026-10-28T11:21:00.000000Z',
    },
    orders: [],
    ...over,
  }
}

/** The same proposal after Approve: sent to OKX, a live post-only limit at 249.40. */
export function sentDetail(): ProposalDetail {
  return proposalDetail({
    status: 'sent',
    decided_by: 'owner',
    decided_at: '2026-10-28T11:33:10.000000Z',
    actions: { approve: false, approve_blocked_reason: 'not_awaiting_owner', edit: false, reject: false, ask_team: true },
    orders: [
      {
        id: 'o-1',
        proposal_id: PROPOSAL_ID,
        purpose: 'entry',
        ord_type: 'post_only',
        px: '249.4',
        sz: '12.02',
        filled_sz: '0',
        avg_px: null,
        state: 'live',
        created_at: '2026-10-28T11:33:14.000000Z',
      },
    ],
  })
}
