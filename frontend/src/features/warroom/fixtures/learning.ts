import type { WarroomMessage } from '../api'

/*
 * Patch K's War Room messages as the API returns them (ATL-203): the Portfolio Manager's
 * post_mortem card (ATL-194 atlas_agents/pm/postmortem.py) and the Historian's weekly_review card
 * (ATL-199 atlas_agents/historian_weekly.py). Figures follow the design's SPY stop-out and its
 * week to 25 Oct (design v16 §9). Shared by the Vitest tests and e2e/warroom-learning-cards.spec.ts.
 */

export const JOURNAL_ID = '0b7e2c41-8f3a-4c2d-9a51-6e0f3d2b7a10'
export const PROPOSAL_ID = '6c1d93ae-27b4-4f0e-b8a2-1f5d7c3e9a44'

const BASE: Omit<WarroomMessage, 'id' | 'author' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 7240,
  tokens_out: 610,
  cost_usd: '0.012000',
  unmatched_numbers: null,
}

export function postMortemCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'post_mortem',
    journal_id: JOURNAL_ID,
    lesson_update: false,
    env: 'demo',
    as_of: '2026-10-27T14:09:00Z',
    inst_id: 'SPY-USDT-SWAP',
    side: 'long',
    asset_class: 'etf',
    setup_tag: null,
    proposal_id: PROPOSAL_ID,
    position_ref: '2001234:2026-10-27T14:04:00Z',
    outcome: 'loss',
    expected: {
      entry: null,
      entry_zone: ['677.20', '678.10'],
      stop: '671.40',
      take_profits: [
        { price: '684.00', pct: '50' },
        { price: '690.00', pct: '30' },
        { price: '698.00', pct: '20' },
      ],
      expected_r: '1.9',
      probability: '0.55',
      invalidation: '1H close below 672',
      evidence: [],
      time_stop_minutes: 1440,
      author: 'strategist',
    },
    actual: {
      avg_entry: '677.65',
      avg_exit: '671.95',
      size: '3',
      ct_val: '1',
      gross_pnl_usd: '-17.10',
      fees_usd: '-2.04',
      funding_usd: '-1.41',
      realised_pnl_usd: '-20.55',
      okx_realised_pnl_usd: '-20.55',
      initial_risk_usd: '21.15',
      realised_r: '-0.97',
      mfe_r: '0.58',
      mae_r: '-1.06',
      excursion_bar: '1m',
      opened_at: '2026-10-26T23:39:00Z',
      closed_at: '2026-10-27T14:04:00Z',
      holding_minutes: 865,
      close_reason: 'stop_loss',
      close_type: '2',
      fill_count: 2,
    },
    votes: [],
    agents: ['devils_advocate', 'strategist', 'technical_analyst'],
    deadline_at: '2026-10-27T14:19:00Z',
    notes: [],
    review: { right_md: '', wrong_md: '' },
    lesson: 'Before the US cash open, give stock-perp stops at least 1.2 ATR(1H); the open gaps through tighter ones.',
    lesson_pending: false,
    tags: { setup: 'breakout', asset: 'etf', regime: 'trend', catalyst: 'macro_release', outcome: 'loss' },
    tag_list: ['asset:etf', 'catalyst:macro_release', 'loss', 'outcome:loss', 'regime:trend', 'setup:breakout'],
    meta: { unmatched_numbers: [], checked: 3 },
    ...over,
  }
}

export function postMortemMessage(over: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-post-mortem',
    author: 'portfolio_manager',
    kind: 'post_mortem',
    content_md: '**SPY long: −0.97R. Stopped out.** The open gapped through a stop inside normal 4h noise.\n\n**Lesson:** Before the US cash open, give stock-perp stops at least 1.2 ATR(1H).',
    card: postMortemCard(over),
    created_at: '2026-10-27T14:13:00Z',
  }
}

/** The model failed: code wrote the row and the card from the facts; the lesson is still pending. */
export function pendingPostMortemMessage(): WarroomMessage {
  const m = postMortemMessage({
    lesson: null,
    lesson_pending: true,
    tags: { setup: 'breakout', asset: 'etf', regime: 'unknown', catalyst: 'unknown', outcome: 'loss' },
    tag_list: ['asset:etf', 'catalyst:unknown', 'loss', 'outcome:loss', 'regime:unknown', 'setup:breakout'],
    meta: undefined,
  })
  return { ...m, id: 'msg-post-mortem-pending', content_md: 'SPY-USDT-SWAP long closed at −0.97R (−$20.55), stopped out. Lesson pending.' }
}

const g = (key: string, count: number, wins: number, losses: number, scratches: number, total_r: string | null, r_n: number, total_usd: string) => ({
  key,
  count,
  wins,
  losses,
  scratches,
  total_r,
  r_n,
  total_usd,
})

export function weeklyReviewCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'weekly_review',
    kind: 'weekly_review',
    env: 'demo',
    as_of: '2026-10-25T15:00:00Z',
    week_start: '2026-10-19',
    day: '2026-10-25',
    window: { from: '2026-10-18T21:00:00Z', to: '2026-10-25T15:00:00Z' },
    trade_count: 4,
    no_trades: false,
    totals: g('all', 4, 2, 1, 1, '2.75', 3, '54.95'),
    by_setup: [g('breakout', 2, 1, 1, 0, '0.50', 2, '9.45'), g('pullback_to_support', 2, 1, 0, 1, '2.25', 1, '45.50')],
    by_asset_class: [g('commodity', 1, 0, 1, 0, '-1.00', 1, '-20.55'), g('crypto', 2, 2, 0, 0, '3.75', 2, '75.10'), g('stock', 1, 0, 0, 1, null, 0, '0.40')],
    by_agent: [g('portfolio_manager', 1, 0, 0, 1, null, 0, '0.40'), g('strategist', 3, 2, 1, 0, '2.75', 3, '54.55')],
    journal_ids: [],
    agent_scores: [],
    paper_trades: { count: 3, filled: 2, total_r: '-0.60', avg_r: '-0.30', by_status: [{ status: 'rejected', count: 3, filled: 2, total_r: '-0.60' }] },
    verdict: 'History weakly supports the process: breakout setups made 0.50R over 2 trades.',
    suggestion: { change: 'Raise min R for breakout setups to 2.0', reason: 'n is 2' },
    status: 'ok',
    meta: { unmatched_numbers: [], checked: 3 },
    ...over,
  }
}

export function weeklyReviewMessage(over: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-weekly-review',
    author: 'historian',
    kind: 'research_report',
    tokens_in: 9820,
    tokens_out: 540,
    cost_usd: '0.044000',
    content_md: 'Pullbacks carried the week; breakouts barely paid.\n\n**Verdict:** History weakly supports the process.\n\n**Suggested change:** Raise min R for breakout setups to 2.0 (n is 2)',
    card: weeklyReviewCard(over),
    created_at: '2026-10-25T15:00:00Z',
  }
}

/** A week with no closed trades: code's short card, no model run. */
export function quietWeekMessage(): WarroomMessage {
  return {
    ...weeklyReviewMessage({
      as_of: '2026-10-18T15:00:00Z',
      week_start: '2026-10-12',
      day: '2026-10-18',
      window: { from: '2026-10-11T21:00:00Z', to: '2026-10-18T15:00:00Z' },
      trade_count: 0,
      no_trades: true,
      totals: g('all', 0, 0, 0, 0, null, 0, '0.00'),
      by_setup: [],
      by_asset_class: [],
      by_agent: [],
      verdict: null,
      suggestion: null,
      status: 'no_trades',
      meta: undefined,
    }),
    id: 'msg-weekly-quiet',
    content_md: 'No closed trades this week.',
    created_at: '2026-10-18T15:00:00Z',
  }
}
