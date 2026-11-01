import type { WarroomMessage } from '../api'

/*
 * Derivatives & Sentiment Analyst messages as the War Room API returns them, with the card shape
 * from docs/handoff/ATL-136-derivatives-agent.md (decimal strings for basis and premium, code-written
 * regime, data_reliable and meta). Shared by the Vitest tests and the Playwright spec
 * (e2e/warroom-positioning-card.spec.ts).
 */

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'derivatives_sentiment',
  reply_to: null,
  mentions: [],
  reasoning_md: null,
  tool_calls: [],
  tokens_in: 6820,
  tokens_out: 760,
  cost_usd: '0.031000',
  unmatched_numbers: null,
}

/** BTC short covering: price up, OI down over 24h; crowded shorts; positioning opposes a long. */
export function btcCoveringCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'positioning_report',
    inst_id: 'BTC-USDT-SWAP',
    regime: 'short_covering',
    crowding_score: 0.72,
    crowded_side: 'short',
    squeeze_risk: { long_squeeze: 0.18, short_squeeze: 0.64 },
    funding_view: 'Funding is negative (z −2.1 over 7d): shorts are paying to stay in.',
    basis_bps: '4.25',
    underlying_premium_pct: null,
    sentiment_score: -0.15,
    supports_direction: { long: 'opposes', short: 'supports' },
    key_evidence: [
      'OI −6.0% in 24h with price +3.0% (as_of 2026-10-28T11:00Z)',
      'Funding −0.008% per 8h, 7-day z −2.1 (as_of 2026-10-28T10:58Z)',
      'Long/short account ratio 0.82, down from 0.95 (as_of 2026-10-28T10:55Z)',
    ],
    data_as_of: '2026-10-28T11:00:00Z',
    basis_reason: null,
    quadrants: { '1h': 'flat', '4h': 'short_covering', '24h': 'short_covering' },
    data_reliable: true,
    unreliable_reasons: {},
    notes: [],
    meta: { unmatched_numbers: [], checked: 9, corrections: [{ rule: 'regime_from_features', model: 'new_longs', code: 'short_covering' }] },
    ...over,
  }
}

/** TSLA on demo with OI reading 0: code forces neutral both sides and writes the reasons. */
export function tslaUnreliableCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'positioning_report',
    inst_id: 'TSLA-USDT-SWAP',
    regime: 'flat',
    crowding_score: 0.5,
    crowded_side: 'none',
    squeeze_risk: { long_squeeze: 0.3, short_squeeze: 0.3 },
    funding_view: 'Funding +0.010% per 8h, in line with its 30-day average.',
    basis_bps: '-12.4',
    underlying_premium_pct: '0.62',
    sentiment_score: 0.05,
    supports_direction: { long: 'neutral', short: 'neutral' },
    key_evidence: ['Open interest reads 0 contracts on demo (as_of 2026-10-28T11:00Z)'],
    data_as_of: '2026-10-28T11:00:00Z',
    basis_reason: null,
    quadrants: { '1h': null, '4h': null, '24h': null },
    data_reliable: false,
    unreliable_reasons: { oi_zero: 'Open interest reads 0, which is not plausible for a listed perp.' },
    notes: ['**Data unreliable for TSLA-USDT-SWAP:** Open interest reads 0. Atlas does not read positioning from this data: neutral for both long and short.'],
    meta: { unmatched_numbers: [], checked: 4, corrections: [{ rule: 'unreliable_data_neutral' }] },
    ...over,
  }
}

export function btcCoveringMessage(over: Partial<WarroomMessage> = {}, card: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-pos-btc',
    kind: 'research_report',
    content_md: '**BTC-USDT-SWAP: positioning opposes a long.** Price +3.0% with OI −6.0% over 24h is short covering, not fresh buying. Short-squeeze risk 64%.',
    card: btcCoveringCard(card),
    created_at: '2026-10-28T11:05:00.000000Z',
    ...over,
  }
}

export function tslaUnreliableMessage(over: Partial<WarroomMessage> = {}, card: Record<string, unknown> = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-pos-tsla',
    kind: 'research_report',
    content_md: '**Data unreliable for TSLA-USDT-SWAP:** Open interest reads 0. Atlas does not read positioning from this data: neutral for both long and short.',
    card: tslaUnreliableCard(card),
    created_at: '2026-10-28T11:06:00.000000Z',
    ...over,
  }
}
