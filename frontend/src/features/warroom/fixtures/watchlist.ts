import type { WarroomMessage } from '../api'
import type { ApiWatchlist } from '../watchlist'

/*
 * Market Scout's watchlist card in the shapes ATL-108/109/120 publish (docs/handoff/ATL-108-
 * market-scout.md, ATL-109-scout-reruns.md) and ATL-103's API answers (ATL-103-watchlist-api.md).
 * The day is Wednesday 28 October 2026: 08:00 Baghdad = 05:00 UTC.
 */

export const DAY = '2026-10-28'
export const PUBLISHED_AT = '2026-10-28T05:00:00+00:00'
export const WINDOW_ENDS_AT = '2026-10-28T05:10:00+00:00'

const CPI = { name: 'US CPI (Sep)', time: '2026-10-28T12:30:00Z', source: 'forexfactory', importance: 5 }
const WALLER = { name: 'Fed’s Waller speaks', time: '2026-10-28T16:00:00Z', source: 'forexfactory', importance: 4 }
const TSLA_EARN = { name: 'Tesla Q3 earnings', time: '2026-10-28T20:05:00Z', source: 'finnhub', importance: 5 }
const SOL_ETF = { name: 'Spot SOL ETFs start trading', time: '2026-10-29T13:30:00Z', source: 'coindesk', importance: 4 }

type Pick = Record<string, unknown>

export const BTC: Pick = {
  inst_id: 'BTC-USDT-SWAP',
  rank: 1,
  asset_class: 'crypto',
  bias: 'long',
  conviction: 0.58,
  thesis: 'Half an ATR under its 20-day high at 119,950, with open interest up 6.1% in 24 hours on neutral funding: new longs, not short covering.',
  catalysts: [CPI],
  key_levels: { support: 118200, resistance: 119950, invalidation: 116800 },
  invalidation: 'A 4-hour close under 116,800 ends the breakout case.',
  data_as_of: '2026-10-28T04:58:00Z',
  pinned_by_owner: false,
}
export const XAU: Pick = {
  inst_id: 'XAU-USDT-SWAP',
  rank: 2,
  asset_class: 'commodity',
  bias: 'short',
  conviction: 0.52,
  thesis: 'Rejected twice at the 4,120 range top this week, while spot gold ETFs saw a fourth day of outflows.',
  catalysts: [CPI, WALLER],
  key_levels: { support: 4050, resistance: 4120, invalidation: 4128 },
  invalidation: 'A 1-hour close above 4,128 means the range top has given way.',
  data_as_of: '2026-10-28T04:58:00Z',
  pinned_by_owner: false,
}
export const TSLA: Pick = {
  inst_id: 'TSLA-USDT-SWAP',
  rank: 3,
  asset_class: 'stock',
  bias: 'neutral',
  conviction: 0.4,
  thesis: 'Earnings after the US close at 23:05, with options pricing a 9% move.',
  catalysts: [TSLA_EARN],
  key_levels: { support: 431.2, resistance: 452.8 },
  invalidation: '',
  data_as_of: '2026-10-28T04:58:00Z',
  pinned_by_owner: false,
}

const REJECTED = [
  { inst_id: 'NVDA-USDT-SWAP', reason: 'Up 3.4 ATR in nine sessions; buying now would be chasing.' },
  { inst_id: 'SOL-USDT-SWAP', reason: 'No catalyst in the next 48 hours.' },
  { inst_id: 'SPY-USDT-SWAP', reason: 'CPI at 15:30 makes the first hours a coin toss.' },
]
const BACKDROP = [
  'BTC and ETH trend higher on rising open interest; realised volatility sits in the bottom third of the month.',
  'US CPI at 15:30 is the day’s event. SPY and US100 drift into it on half their usual volume.',
  'Gold ranges under 4,120 after four days of ETF outflows, and the dollar is flat.',
]
const SHORTLIST = Array.from({ length: 25 }, (_, i) => `I${i}-USDT-SWAP`)
const teammates = (historian: 'replied' | 'timed_out' = 'replied') => [
  { agent: 'news_intelligence', status: 'replied', mention_id: 'm1', message_id: 'r1', replied_at: '2026-10-28T05:02:00Z', note: null },
  { agent: 'historian', status: historian, mention_id: 'm2', message_id: historian === 'replied' ? 'r2' : null, replied_at: null, note: null },
]

export function watchlistCard(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'watchlist',
    watchlist_id: 'wl-v1',
    version: 1,
    date: DAY,
    status: 'published',
    created_at: PUBLISHED_AT,
    edit_window_ends_at: WINDOW_ENDS_AT,
    reason: null,
    picks: [BTC, XAU, TSLA],
    degraded_asset_classes: [],
    degraded_since: {},
    backdrop: BACKDROP,
    rejected: REJECTED,
    corrections: [],
    trigger: 'schedule',
    correlation_id: `scout_watchlist:${DAY}`,
    feature_table_as_of: '2026-10-28T04:55:00+00:00',
    shortlist: SHORTLIST,
    previous_version: null,
    evidence: { feature_table_as_of: '2026-10-28T04:55:00Z', feature_table_age_minutes: 6.2, feature_table_stale: false, teammates: teammates() },
    meta: { unmatched_numbers: [], checked: 14 },
    ...over,
  }
}

const capped = (p: Pick, reason: string): Pick => ({ ...p, conviction: 0.5, conviction_before_cap: p.conviction, cap_reason: reason })

export const cards = {
  published: () => watchlistCard(),
  partialEvidence: () =>
    watchlistCard({
      status: 'partial_evidence',
      reason: 'Partial evidence: historian did not reply in time',
      picks: [capped(BTC, 'partial_evidence'), capped(XAU, 'partial_evidence'), TSLA],
      evidence: { feature_table_as_of: '2026-10-28T04:55:00Z', feature_table_age_minutes: 6.2, feature_table_stale: false, teammates: teammates('timed_out') },
    }),
  /** Crypto news has been down since 06:10 Baghdad: only BTC (crypto) is capped. */
  newsDown: () =>
    watchlistCard({
      picks: [capped(BTC, 'news_class_down'), XAU, TSLA],
      degraded_asset_classes: ['crypto'],
      degraded_since: { crypto: '2026-10-28T03:10:00Z' },
    }),
  emptyShortlist: () =>
    watchlistCard({
      status: 'empty',
      picks: [],
      backdrop: [],
      rejected: [],
      shortlist: [],
      evidence: undefined,
      reason: 'No instrument passed the universe filter: 30 rejected (by check: volume 22, state 5, age 3).',
    }),
  noPicks: () => watchlistCard({ status: 'empty', picks: [], reason: 'Nothing on the shortlist has a catalyst worth trading today.' }),
  rerun: () =>
    watchlistCard({
      watchlist_id: 'wl-v2',
      version: 2,
      created_at: '2026-10-28T09:10:00+00:00',
      edit_window_ends_at: '2026-10-28T09:20:00+00:00',
      picks: [
        BTC,
        {
          ...BTC,
          inst_id: 'SOL-USDT-SWAP',
          rank: 2,
          conviction: 0.62,
          thesis: 'The SEC approved spot SOL ETFs from three issuers at 12:04, CONFIRMED by two outlets.',
          catalysts: [SOL_ETF],
          key_levels: { support: 186.4, resistance: 198, invalidation: 181 },
          invalidation: 'A 4-hour close under 181.00 gives the news move back.',
        },
        { ...XAU, rank: 3 },
      ],
      trigger: { kind: 'news_alert', message_id: 'msg-alert', event_id: 'ev-1', headline: 'SEC approves spot Solana ETFs', importance: 5, at: '2026-10-28T09:04:00+00:00' },
      changes: { since: PUBLISHED_AT, version: 1, added: ['SOL-USDT-SWAP'], dropped: ['TSLA-USDT-SWAP'] },
    }),
  spike: () =>
    watchlistCard({
      watchlist_id: 'wl-v3',
      version: 3,
      created_at: '2026-10-28T11:00:00+00:00',
      trigger: { kind: 'vol_spike', inst_id: 'NVDA-USDT-SWAP', sigma: 3.4, bar: '1H', bar_ts: '2026-10-28T10:00:00+00:00', at: '2026-10-28T11:00:00+00:00' },
      changes: { since: PUBLISHED_AT, version: 2, added: [], dropped: [] },
    }),
}

export function watchlistMessage(card: Record<string, unknown>, over: Partial<WarroomMessage> = {}): WarroomMessage {
  return {
    id: `msg-${String(card.watchlist_id)}`,
    thread_id: 'thread-main',
    author_type: 'agent',
    author: 'market_scout',
    kind: 'watchlist',
    content_md: "Today's watchlist: BTC-USDT-SWAP long (0.58), XAU-USDT-SWAP short (0.52), TSLA-USDT-SWAP neutral (0.40).",
    card,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    created_at: String(card.created_at),
    ...over,
  }
}

/** A card pick as ATL-103 returns it: an item id, a 4-dp decimal string conviction. */
export function apiItem(p: Pick, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `item-${String(p.inst_id)}`,
    inst_id: p.inst_id,
    rank: p.rank,
    asset_class: p.asset_class,
    bias: p.bias,
    conviction: Number(p.conviction).toFixed(4),
    thesis: p.thesis,
    catalysts: p.catalysts,
    key_levels: p.key_levels,
    invalidation: p.invalidation,
    pinned_by_owner: false,
    removed_by_owner_at: null,
    data_as_of: '2026-10-28T04:58:00.000000Z',
    ...over,
  }
}

/** ATL-103's placeholder row for a fresh owner pin. */
export function pinItem(instId: string, rank: number, assetClass = 'crypto'): Record<string, unknown> {
  return {
    id: `item-${instId}`,
    inst_id: instId,
    rank,
    asset_class: assetClass,
    bias: 'neutral',
    conviction: '0.0000',
    thesis: 'Pinned by the owner.',
    catalysts: [],
    key_levels: {},
    invalidation: '',
    pinned_by_owner: true,
    removed_by_owner_at: null,
    data_as_of: '2026-10-28T05:04:00.000000Z',
  }
}

export function apiWatchlist(over: Partial<ApiWatchlist> = {}): ApiWatchlist {
  return {
    id: 'wl-v1',
    date: DAY,
    version: 1,
    status: 'published',
    created_at: '2026-10-28T05:00:00.000000Z',
    edit_window_ends_at: '2026-10-28T05:10:00.000000Z',
    edit_window_open: true,
    message_id: 'msg-wl-v1',
    items: [apiItem(BTC), apiItem(XAU), apiItem(TSLA)],
    removed_items: [],
    ...over,
  }
}
