import type { WarroomMessage } from '../api'

/*
 * News Intelligence messages as the War Room API returns them, with the news_brief card shape
 * from docs/handoff/ATL-107-news-intelligence.md (ATL-106 status fields, ATL-120 degraded_since):
 * the 07:30 morning brief, an instrument brief answering @News about Tesla, and an alert analysis
 * replying under an ingest news_alert. The stories are the design v9 prototype's
 * (js/pages/wr-newsbrief.js). Shared by the Vitest tests and e2e/news-brief.spec.ts.
 */

const BASE: Omit<WarroomMessage, 'id' | 'kind' | 'content_md' | 'card' | 'created_at'> = {
  thread_id: 'thread-main',
  author_type: 'agent',
  author: 'news_intelligence',
  reply_to: null,
  mentions: [],
  reasoning_md: 'Morning brief: the top clusters from the last 24 hours, the next 48 hours of events.',
  tool_calls: [],
  tokens_in: 5212,
  tokens_out: 744,
  cost_usd: '0.000000',
  unmatched_numbers: null,
}

type Card = Record<string, unknown>
const src = (name: string, url: string | null, published_at: string) => ({ name, url, published_at })
const mv = (inst_id: string, value: number | null, reason: string | null = null) => ({ inst_id, value, reason })

const MORNING_CLUSTERS: Card[] = [
  {
    cluster_id: 'c-7d02',
    headline: 'Fed’s Waller says a December cut is “not a done deal” as services inflation stays sticky',
    status: 'CONFIRMED',
    status_sources: ['rss.cnbc', 'rss.reuters'],
    sources: [
      src('CNBC', 'https://www.cnbc.com/2026/10/27/fed-waller-december-rate-cut.html', '2026-10-27T21:10:04Z'),
      src('Reuters', 'https://www.reuters.com/markets/us/fed-waller-says-december-cut-not-done-deal-2026-10-27/', '2026-10-27T21:24:40Z'),
    ],
    event_type: 'central_bank',
    importance: 5,
    sentiment: -0.42,
    first_seen: '2026-10-27T21:10:04Z',
    price_move_since_first_seen_atr: [mv('SPY-USDT-SWAP', -0.3), mv('US100-USDT-SWAP', -0.4)],
  },
  {
    cluster_id: 'c-7c88',
    headline: 'Spot gold ETFs post a fourth straight day of outflows, the longest run since June',
    status: 'REPORTED',
    status_sources: ['rss.reuters'],
    sources: [
      src('Reuters', 'https://www.reuters.com/markets/commodities/gold-etf-outflows-fourth-day-2026-10-27/', '2026-10-27T19:40:12Z'),
      src('Kitco', 'https://www.kitco.com/news/article/2026-10-27/gold-etf-outflows', '2026-10-27T20:05:00Z'),
    ],
    event_type: 'ETF_flow',
    importance: 4,
    sentiment: -0.36,
    first_seen: '2026-10-27T19:40:12Z',
    price_move_since_first_seen_atr: [mv('XAU-USDT-SWAP', -0.6)],
  },
  {
    cluster_id: 'c-7f21',
    headline: 'Tesla reports third-quarter results after the US close today; options price a 9% move',
    status: 'CONFIRMED',
    status_sources: ['rss.marketwatch', 'rss.reuters'],
    sources: [
      src('MarketWatch', 'https://www.marketwatch.com/story/tesla-third-quarter-earnings-preview-2026-10-28', '2026-10-28T01:47:12Z'),
      src('Reuters', 'https://www.reuters.com/business/autos-transportation/tesla-q3-preview-2026-10-28/', '2026-10-28T02:02:30Z'),
      src('Yahoo Finance', 'https://finance.yahoo.com/news/tesla-earnings-what-to-watch-2026-10-28', '2026-10-28T03:15:00Z'),
    ],
    event_type: 'earnings',
    importance: 4,
    sentiment: -0.12,
    first_seen: '2026-10-28T01:47:12Z',
    price_move_since_first_seen_atr: [mv('TSLA-USDT-SWAP', 0.2)],
  },
  {
    cluster_id: 'c-80b3',
    headline: 'Solana ETF decision expected this week, people familiar with the matter say',
    status: 'RUMOUR',
    status_sources: ['cryptopanic'],
    sources: [src('CryptoPanic', 'https://cryptopanic.com/news/1983221456/', '2026-10-28T00:52:00Z')],
    event_type: 'regulation',
    importance: 4,
    sentiment: 0.58,
    first_seen: '2026-10-28T00:52:00Z',
    price_move_since_first_seen_atr: [mv('SOL-USDT-SWAP', 1.6)],
  },
  {
    cluster_id: 'c-7e40',
    headline: 'Bitcoin perpetual open interest hits a record $86B as price nears its 20-day high',
    status: 'CONFIRMED',
    status_sources: ['rss.coindesk', 'rss.theblock'],
    sources: [
      src('CoinDesk', 'https://www.coindesk.com/markets/2026/10/28/bitcoin-open-interest-record', '2026-10-28T02:20:00Z'),
      src('The Block', 'https://www.theblock.co/post/361204/bitcoin-perp-open-interest-record', '2026-10-28T02:41:00Z'),
    ],
    event_type: 'other',
    importance: 3,
    sentiment: 0.31,
    first_seen: '2026-10-28T02:20:00Z',
    price_move_since_first_seen_atr: [mv('BTC-USDT-SWAP', 0.9)],
  },
  {
    cluster_id: 'c-7a19',
    headline: 'TSMC raises its 2027 capital spending outlook on AI chip demand',
    status: 'REPORTED',
    status_sources: ['rss.reuters'],
    sources: [src('Reuters', 'https://www.reuters.com/technology/tsmc-raises-2027-capex-outlook-2026-10-27/', '2026-10-27T23:05:00Z')],
    event_type: 'guidance',
    importance: 3,
    sentiment: 0.44,
    first_seen: '2026-10-27T23:05:00Z',
    price_move_since_first_seen_atr: [mv('NVDA-USDT-SWAP', 0.7), mv('TSM-USDT-SWAP', 1.1)],
  },
  {
    cluster_id: 'c-7b55',
    headline: 'Ether ETF inflows turn positive for a third day',
    status: 'CONFIRMED',
    status_sources: ['rss.coindesk', 'finnhub.news'],
    sources: [
      src('CoinDesk', 'https://www.coindesk.com/markets/2026/10/27/ether-etf-inflows-third-day', '2026-10-27T20:30:00Z'),
      src('Bloomberg', 'https://www.bloomberg.com/news/articles/2026-10-27/ether-etfs-inflows', '2026-10-27T20:52:00Z'),
    ],
    event_type: 'ETF_flow',
    importance: 3,
    sentiment: 0.36,
    first_seen: '2026-10-27T20:30:00Z',
    price_move_since_first_seen_atr: [mv('ETH-USDT-SWAP', 0.3)],
  },
  {
    cluster_id: 'c-7c02',
    headline: 'Oil slips as US crude inventories rise for a second week',
    status: 'REPORTED',
    status_sources: ['rss.reuters'],
    sources: [src('Reuters', 'https://www.reuters.com/business/energy/oil-slips-us-crude-stocks-rise-2026-10-27/', '2026-10-27T15:10:00Z')],
    event_type: 'supply_shock',
    importance: 3,
    sentiment: -0.28,
    first_seen: '2026-10-27T15:10:00Z',
    price_move_since_first_seen_atr: [mv('CL-USDT-SWAP', -0.5)],
  },
  {
    cluster_id: 'c-7d90',
    headline: 'OKX schedules system maintenance for 30 October, 06:00–07:00 UTC',
    status: 'CONFIRMED',
    status_sources: ['okx.announcements'],
    sources: [src('OKX announcements', null, '2026-10-28T03:00:00Z')],
    event_type: 'other',
    importance: 3,
    sentiment: null,
    first_seen: '2026-10-28T03:00:00Z',
    price_move_since_first_seen_atr: [],
  },
  {
    cluster_id: 'c-7e12',
    headline: 'Coinbase <b>lists</b> SUI and APT perpetuals for US users',
    status: 'REPORTED',
    status_sources: ['rss.theblock'],
    sources: [src('The Block', 'https://www.theblock.co/post/361188/coinbase-lists-sui-apt-perpetuals', '2026-10-27T18:12:00Z')],
    event_type: 'listing',
    importance: 2,
    sentiment: 0.22,
    first_seen: '2026-10-27T18:12:00Z',
    price_move_since_first_seen_atr: [mv('SUI-USDT-SWAP', 0.4), mv('APT-USDT-SWAP', null, 'no closed 1m candle for APT-USDT-SWAP to price it now')],
  },
]

const ev = (time: string, name: string, importance: number | null, forecast: string | null, previous: string | null, sensitive_instruments: string[]) => ({
  time,
  name,
  importance,
  forecast,
  previous,
  sensitive_instruments,
})

const MORNING_EVENTS: Card[] = [
  ev('2026-10-28T09:30:00Z', 'US MBA mortgage applications', 2, null, '-1.8%', ['SPY-USDT-SWAP']),
  ev('2026-10-28T12:30:00Z', 'US CPI (Sep) m/m', 5, '+0.3%', '+0.4%', ['XAU-USDT-SWAP', 'BTC-USDT-SWAP', 'US100-USDT-SWAP']),
  ev('2026-10-28T12:30:00Z', 'US core CPI (Sep) m/m', 5, '+0.3%', '+0.3%', ['US100-USDT-SWAP', 'SPY-USDT-SWAP', 'XAU-USDT-SWAP']),
  ev('2026-10-28T14:30:00Z', 'US crude oil inventories (EIA)', 3, '-1.2M', '+2.1M', ['CL-USDT-SWAP']),
  ev('2026-10-28T16:00:00Z', 'Fed’s Waller speaks on the economic outlook', 4, null, null, ['SPY-USDT-SWAP', 'US100-USDT-SWAP', 'XAU-USDT-SWAP']),
  ev('2026-10-28T20:05:00Z', 'Tesla Q3 earnings, after the close', null, 'EPS 0.62', 'EPS 0.55', ['TSLA-USDT-SWAP']),
  ev('2026-10-29T12:15:00Z', 'ECB rate decision', 5, '2.00%', '2.00%', ['XAU-USDT-SWAP', 'BTC-USDT-SWAP']),
  ev('2026-10-29T12:30:00Z', 'US GDP Q3, advance, annualised', 5, '+2.1%', '+3.8%', ['SPY-USDT-SWAP', 'US100-USDT-SWAP', 'XAU-USDT-SWAP']),
  ev('2026-10-29T12:30:00Z', 'US initial jobless claims', 3, '226K', '229K', ['SPY-USDT-SWAP']),
  ev('2026-10-29T20:30:00Z', 'Apple Q4 earnings, after the close', null, 'EPS 1.76', 'EPS 1.64', ['AAPL-USDC-SWAP']),
]

export function morningCard(over: Card = {}): Card {
  return {
    type: 'news_brief',
    form: 'morning_brief',
    inst_ids: [],
    clusters: MORNING_CLUSTERS,
    upcoming_events: MORNING_EVENTS,
    top_instruments: [
      { inst_id: 'BTC-USDT', stories: 42, net_sentiment: 0.21 },
      { inst_id: 'TSLA-USDT', stories: 31, net_sentiment: -0.08 },
      { inst_id: 'XAU-USDT', stories: 24, net_sentiment: -0.27 },
      { inst_id: 'NVDA-USDT', stories: 18, net_sentiment: 0.34 },
      { inst_id: 'ETH-USDT', stories: 15, net_sentiment: null },
    ],
    net_sentiment_24h: null,
    net_sentiment_72h: null,
    priced_in: null,
    what_to_watch: null,
    degraded_asset_classes: [],
    degraded_since: {},
    data_as_of: '2026-10-28T04:29:12Z',
    meta: { unmatched_numbers: [], checked: 14, verdict_corrections: [] },
    ...over,
  }
}

export function morningBriefMessage(over: Partial<WarroomMessage> = {}, card: Card = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-news-morning',
    kind: 'research_report',
    content_md:
      '**Morning brief.** Three things move our instruments today: US CPI at 15:30, Tesla’s results at 23:05, and a fourth day of gold ETF outflows. A RUMOUR about a Solana ETF decision is lifting SOL; nothing confirms it yet.',
    card: morningCard(card),
    created_at: '2026-10-28T04:30:02.000000Z',
    ...over,
  }
}

export function instrumentBriefMessage(over: Partial<WarroomMessage> = {}, card: Card = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-news-tsla',
    thread_id: 'thread-tsla',
    kind: 'research_report',
    reply_to: null,
    content_md: 'Tesla’s results at 23:05 are the story; options price a 9% move. Sentiment turned slightly negative over 24 hours after the China price cut.',
    card: {
      type: 'news_brief',
      form: 'instrument_brief',
      inst_ids: ['TSLA-USDT-SWAP'],
      clusters: [
        MORNING_CLUSTERS[2],
        {
          cluster_id: 'c-79e6',
          headline: 'Tesla cuts Model Y prices in China for the second time this quarter',
          status: 'CONFIRMED',
          status_sources: ['rss.cnbc', 'finnhub.news'],
          sources: [
            src('Reuters', 'https://www.reuters.com/business/autos-transportation/tesla-cuts-model-y-prices-china-2026-10-27/', '2026-10-27T03:15:00Z'),
            src('CNBC', 'https://www.cnbc.com/2026/10/27/tesla-china-model-y-price-cut.html', '2026-10-27T04:05:00Z'),
          ],
          event_type: 'product',
          importance: 3,
          sentiment: -0.46,
          first_seen: '2026-10-27T03:15:00Z',
          price_move_since_first_seen_atr: [mv('TSLA-USDT-SWAP', -0.9)],
        },
        {
          cluster_id: 'c-7c70',
          headline: 'Robotaxi permit in Nevada is “days away”, posts claim',
          status: 'RUMOUR',
          status_sources: ['cryptopanic'],
          sources: [src('r/teslainvestorsclub', 'javascript:alert(1)', '2026-10-27T22:10:00Z')],
          event_type: 'regulation',
          importance: 2,
          sentiment: 0.4,
          first_seen: '2026-10-27T22:10:00Z',
          price_move_since_first_seen_atr: [mv('TSLA-USDT-SWAP', 0.1)],
        },
      ],
      upcoming_events: [
        ev('2026-10-28T12:30:00Z', 'US CPI (Sep) m/m', 5, '+0.3%', '+0.4%', ['US100-USDT-SWAP', 'TSLA-USDT-SWAP']),
        ev('2026-10-28T20:05:00Z', 'Tesla Q3 earnings, after the close', null, 'EPS 0.62', 'EPS 0.55', ['TSLA-USDT-SWAP']),
      ],
      net_sentiment_24h: -0.18,
      net_sentiment_72h: 0.05,
      priced_in: 'partly',
      what_to_watch: 'The report at 23:05 decides this. Until then the perp trades on books about a third as deep as in US hours, so a stop under 438 could fill badly. @PM may want it wider.',
      top_instruments: [],
      degraded_asset_classes: [],
      degraded_since: {},
      data_as_of: '2026-10-28T10:21:04Z',
      meta: {
        unmatched_numbers: [
          { raw: '9%', location: 'text', path: null },
          { raw: '438', location: 'card', path: 'what_to_watch' },
          { raw: '-0.18', location: 'card', path: 'net_sentiment_24h' },
        ],
        checked: 11,
      },
      ...card,
    },
    created_at: '2026-10-28T10:21:30.000000Z',
    ...over,
  }
}

/** The ingest alert the analysis replies under (ATL-65 news.alert payload). */
export function solAlertMessage(): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-sol-alert',
    author_type: 'system',
    author: 'news',
    kind: 'news_alert',
    content_md: '',
    reasoning_md: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    card: {
      headline: 'SEC approves spot Solana ETFs from three issuers, trading to start Thursday',
      source: 'rss.coindesk',
      source_name: 'CoinDesk',
      url: 'https://www.coindesk.com/policy/2026/10/28/sec-approves-spot-solana-etfs',
      published_at: '2026-10-28T09:02:51Z',
      inst_ids: ['SOL-USDT-SWAP', 'SOL-USDT'],
      importance: 5,
      event_type: 'regulation',
      sentiment: 0.78,
    },
    created_at: '2026-10-28T09:04:00.000000Z',
  } as WarroomMessage
}

export function alertAnalysisMessage(over: Partial<WarroomMessage> = {}, card: Card = {}): WarroomMessage {
  return {
    ...BASE,
    id: 'msg-sol-analysis',
    kind: 'research_report',
    reply_to: 'msg-sol-alert',
    content_md: 'CONFIRMED and partly priced in: SOL-USDT-SWAP is +1.2 ATR since the story broke at 12:02.',
    card: {
      type: 'news_brief',
      form: 'alert_analysis',
      inst_ids: ['SOL-USDT-SWAP', 'SOL-USDT'],
      clusters: [
        {
          cluster_id: 'c-81d4',
          headline: 'SEC approves spot Solana ETFs from three issuers, trading to start Thursday',
          status: 'CONFIRMED',
          status_sources: ['rss.coindesk', 'rss.theblock'],
          sources: [
            src('CoinDesk', 'https://www.coindesk.com/policy/2026/10/28/sec-approves-spot-solana-etfs', '2026-10-28T09:02:51Z'),
            src('The Block', 'https://www.theblock.co/post/361290/sec-approves-solana-etfs', '2026-10-28T09:04:12Z'),
          ],
          event_type: 'regulation',
          importance: 5,
          sentiment: 0.78,
          first_seen: '2026-10-28T09:02:51Z',
          price_move_since_first_seen_atr: [mv('SOL-USDT-SWAP', 1.2), mv('SOL-USDT', 1.1)],
        },
      ],
      upcoming_events: [],
      net_sentiment_24h: 0.52,
      net_sentiment_72h: 0.41,
      priced_in: 'partly',
      what_to_watch: '@Scout this calls for an intraday re-run. @PM nothing is held in SOL. Trading in the ETFs starts Thursday; first-day flows are the next catalyst.',
      top_instruments: [],
      degraded_asset_classes: [],
      degraded_since: {},
      data_as_of: '2026-10-28T09:05:02Z',
      meta: { unmatched_numbers: [], checked: 4 },
      ...card,
    },
    created_at: '2026-10-28T09:05:10.000000Z',
    ...over,
  }
}
