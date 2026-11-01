import type { WarroomMessage } from '../api'

/*
 * Technical Analyst messages as the War Room API returns them, with the card shape from
 * docs/handoff/ATL-135-technical-analyst.md (decimal strings, code-written meta). The numbers are
 * the design's fixture day (design v11 Phase 2 §3): SOL long breakout, XAU no setup. Shared by the
 * Vitest tests and the Playwright spec (e2e/warroom-technical-card.spec.ts).
 */

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'technical_analyst',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 9120,
  tokens_out: 1040,
  cost_usd: '0.041000',
  unmatched_numbers: null,
}

export function solSetupCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'technical_setup',
    inst_id: 'SOL-USDT-SWAP',
    timeframe_bias: { '1D': 'up', '4H': 'up', '1H': 'up', '15m': 'range' },
    setup_type: 'breakout',
    direction: 'long',
    entry_zone: ['248.60', '250.20'],
    trigger: '1H close above 250.20 with relative volume above 1.3',
    stop: '241.80',
    targets: [
      { price: '258.50', r_multiple: '1.20', reason: 'Prior week high', close_pct: '50' },
      { price: '264.60', r_multiple: '2.00', reason: '4H resistance zone', close_pct: '30' },
      { price: '272.20', r_multiple: '3.00', reason: 'Measured move', close_pct: '20' },
    ],
    expected_r: '1.80',
    invalidation: 'A 1H close back below 244.00, the breakout level',
    quality_score: 0.72,
    est_slippage_bps: '4.10',
    levels_used: [
      { id: '1H:z2', price: '244.60' },
      { id: '4H:z4', price: '264.60' },
    ],
    data_as_of: '2026-10-28T11:00:00Z',
    no_setup_reason: null,
    notes: [],
    meta: { unmatched_numbers: [], checked: 14, corrections: [{ rule: 'tick_rounding', tick_sz: '0.01' }] },
    ...over,
  }
}

export function xauNoSetupCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'technical_setup',
    inst_id: 'XAU-USDT-SWAP',
    timeframe_bias: { '1D': 'down', '4H': 'range', '1H': 'range', '15m': 'down' },
    setup_type: 'none',
    direction: 'none',
    entry_zone: null,
    trigger: '',
    stop: null,
    targets: [],
    expected_r: null,
    invalidation: '',
    quality_score: 0.2,
    est_slippage_bps: '3.00',
    levels_used: [],
    data_as_of: '2026-10-28T11:00:00Z',
    no_setup_reason: 'A 1H close below 4,082.0 turns this into a breakdown short.',
    notes: [],
    meta: { unmatched_numbers: [], checked: 3, corrections: [] },
    ...over,
  }
}

export function solSetupMessage(over: Partial<WarroomMessage> = {}, card: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-ta-sol',
    kind: 'research_report',
    content_md: '**SOL-USDT-SWAP: long breakout setup.** A 1H close above 250.20 on volume triggers it. Expected 1.8R, quality 0.72.',
    card: solSetupCard(card),
    created_at: '2026-10-28T11:03:00.000000Z',
    ...over,
  }
}

export function xauNoSetupMessage(over: Partial<WarroomMessage> = {}, card: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-ta-xau',
    kind: 'research_report',
    tokens_in: 7480,
    tokens_out: 520,
    content_md: '**XAU-USDT-SWAP: no setup.** Price is mid-range with no momentum either way.',
    card: xauNoSetupCard(card),
    created_at: '2026-10-28T11:04:00.000000Z',
    ...over,
  }
}
