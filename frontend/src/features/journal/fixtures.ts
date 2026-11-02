import type { JournalEntry } from './api'

/*
 * Journal rows in ATL-200's GET /api/journal shape, with ATL-194's post-mortem `data`
 * (docs/handoff/ATL-194-post-mortems.md). Shared by the Vitest suite.
 */

export const WIN: JournalEntry = {
  id: '9f1c2a40-6a0e-4c1e-9b8e-2d5f0c7a1b23',
  env: 'demo',
  title: 'BTC-USDT-SWAP long: +1.69R +$3.71 (win)',
  inst_id: 'BTC-USDT-SWAP',
  agent: 'portfolio_manager',
  proposal_id: '0b7e3c1d-2f4a-4b5c-8d6e-7f8091a2b3c4',
  expected_md: 'Entry 100, stop 95, TP1 110 (2R), p=0.55.',
  actual_md: 'Avg entry 100.5 → exit 110; +3.71 USD = +1.69R.',
  lessons_md: 'When the 1H trend and a strong level agree, let the take-profit work, do not trail early.',
  tags: ['asset:crypto', 'catalyst:none', 'outcome:win', 'regime:trend', 'setup:pullback_to_support', 'win'],
  data: {
    side: 'long',
    asset_class: 'crypto',
    outcome: 'win',
    expected: {
      entry: '100',
      entry_zone: null,
      stop: '95',
      take_profits: [
        { price: '105', pct: '50' },
        { price: '110', pct: '50' },
      ],
      expected_r: '1.5',
      probability: '0.55',
      invalidation: '1H close below 96',
      time_stop_minutes: 1440,
      author: 'strategist',
    },
    actual: {
      avg_entry: '100.5',
      avg_exit: '110',
      fees_usd: '-0.04',
      funding_usd: '-0.05',
      realised_pnl_usd: '3.71',
      initial_risk_usd: '2.20',
      realised_r: '1.69',
      mfe_r: '2.11',
      mae_r: '-0.45',
      opened_at: '2026-10-27T07:00:00Z',
      closed_at: '2026-10-27T11:30:00Z',
      holding_minutes: 270,
      close_reason: 'take_profit',
    },
    votes: [
      { agent: 'technical_analyst', vote: 'take', confidence: '0.7', reason: '…' },
      { agent: 'devils_advocate', vote: 'skip', confidence: '0.6', reason: '…' },
    ],
    agents: ['devils_advocate', 'strategist', 'technical_analyst'],
    review: { right_md: '**Technical Analyst** placed TP2 at the 4H resistance.', wrong_md: "**Devil's Advocate** voted skip on a clean setup." },
    lesson: 'When the 1H trend and a strong level agree, let the take-profit work, do not trail early.',
    lesson_pending: false,
    lesson_attempts: 1,
  },
  created_at: '2026-10-27T11:35:00.123456Z',
}

export const LOSS: JournalEntry = {
  ...WIN,
  id: '3d2e1f00-0000-4000-8000-000000000002',
  title: 'XAU-USDT-SWAP short: −1.02R −$512.40 (loss)',
  inst_id: 'XAU-USDT-SWAP',
  proposal_id: '1a2b3c4d-0000-4000-8000-000000000002',
  lessons_md: null,
  tags: ['asset:commodity', 'catalyst:macro_release', 'outcome:loss', 'regime:high_vol', 'setup:breakout', 'loss'],
  data: {
    ...WIN.data,
    side: 'short',
    asset_class: 'commodity',
    outcome: 'loss',
    expected: { ...WIN.data.expected, entry: '4000', stop: '4040', take_profits: [{ price: '3920', pct: '100' }] },
    actual: {
      ...WIN.data.actual,
      avg_entry: '4001',
      avg_exit: '4041.5',
      realised_pnl_usd: '-512.40',
      realised_r: '-1.02',
      mfe_r: '0.3',
      mae_r: '-1.05',
      opened_at: '2026-10-20T12:00:00Z',
      closed_at: '2026-10-20T15:10:00Z',
      holding_minutes: 190,
      close_reason: 'stop_loss',
    },
    lesson: null,
    lesson_pending: true,
  },
  created_at: '2026-10-20T15:12:00.000000Z',
}
