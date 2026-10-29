import { summarise, type ApiSource, type SourcesReport } from './model'

/* Test-only builders for GET /api/data-sources (shapes from docs/handoff/ATL-66-data-sources-api.md). */

export const iso = (ms: number) => new Date(ms).toISOString()

export function source(key: string, category: string, now: number, over: Partial<ApiSource> = {}): ApiSource {
  const feed = ['exchange', 'crypto_news', 'markets_news'].includes(category)
  return {
    key,
    provider: key.split('.')[key.startsWith('rss.') ? 1 : 0]!,
    name: key,
    kind: key.startsWith('rss.') ? 'rss' : 'api',
    category,
    url: null,
    enabled: true,
    requires_key: false,
    has_key: false,
    key_last4: null,
    poll_seconds: 120,
    status: 'ok',
    consecutive_failures: 0,
    disabled_until: null,
    last_success_at: iso(now - 60_000),
    last_item_at: feed ? iso(now - 5 * 60_000) : null,
    last_error: null,
    hourly_24h: Array.from({ length: 24 }, (_, i) => (feed ? (i % 5) + 1 : 0)),
    covered_by: null,
    ...over,
  }
}

export const report = (data: ApiSource[]): SourcesReport => ({ data, summary: summarise(data) })

/** One row per status the page draws: ok, degraded (retrying), needs_key, disabled, failing, never run, standby. */
export function mixed(now: number): SourcesReport {
  return report([
    source('okx.announcements', 'exchange', now, { provider: 'okx', name: 'OKX announcements' }),
    source('rss.coindesk', 'crypto_news', now, { name: 'CoinDesk' }),
    source('rss.theblock', 'crypto_news', now, {
      name: 'The Block',
      poll_seconds: 300,
      status: 'degraded',
      consecutive_failures: 3,
      disabled_until: iso(now + 3 * 60_000),
      last_error: 'HTTP 503 Service Unavailable from www.theblock.co',
    }),
    source('cryptopanic', 'crypto_news', now, {
      provider: 'cryptopanic',
      name: 'CryptoPanic',
      requires_key: true,
      status: 'needs_key',
      last_success_at: null,
      last_item_at: null,
      hourly_24h: Array(24).fill(0),
    }),
    source('rss.cnbc', 'markets_news', now, { name: 'CNBC Top News', enabled: false, status: 'disabled' }),
    source('rss.yahoo', 'markets_news', now, {
      name: 'Yahoo Finance',
      poll_seconds: 300,
      consecutive_failures: 1,
      last_error: 'Timed out after 20 s waiting for feeds.finance.yahoo.com',
    }),
    source('finnhub.calendar', 'calendar', now, {
      provider: 'finnhub',
      name: 'Finnhub economic calendar',
      requires_key: true,
      has_key: true,
      key_last4: '7Q2F',
      poll_seconds: 900,
      hourly_24h: Array(24).fill(0),
    }),
    source('forexfactory.calendar', 'calendar', now, {
      provider: 'forexfactory',
      name: 'ForexFactory weekly calendar',
      poll_seconds: 900,
      covered_by: 'finnhub.calendar',
      hourly_24h: Array(24).fill(0),
    }),
    source('alternative_me.fng', 'sentiment', now, {
      provider: 'alternative_me',
      name: 'Crypto Fear & Greed Index',
      poll_seconds: 3600,
      status: 'never_run',
      last_success_at: null,
      hourly_24h: Array(24).fill(0),
    }),
  ])
}
