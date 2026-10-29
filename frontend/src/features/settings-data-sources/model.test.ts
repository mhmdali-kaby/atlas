import { describe, expect, it } from 'vitest'
import {
  bucketStart,
  downCauses,
  groups,
  missingKeyProviders,
  monogram,
  niceMax,
  rowStatus,
  startedBy,
  summary,
  usSessions,
  wire,
  withEnabled,
} from './model'
import { iso, mixed, report, source } from './testFixtures'

const NOW = Date.parse('2026-10-28T11:32:00Z')
const MIN = 60_000

describe('rowStatus', () => {
  const r = mixed(NOW)
  const by = (k: string) => r.data.find((s) => s.key === k)!
  it('follows the design precedence', () => {
    expect(rowStatus(by('rss.coindesk'), NOW)).toBe('working')
    expect(rowStatus(by('rss.theblock'), NOW)).toBe('retrying')
    expect(rowStatus(by('cryptopanic'), NOW)).toBe('needs_key')
    expect(rowStatus(by('rss.cnbc'), NOW)).toBe('off')
    expect(rowStatus(by('rss.yahoo'), NOW)).toBe('failing')
    expect(rowStatus(by('forexfactory.calendar'), NOW)).toBe('standby')
    expect(rowStatus(by('alternative_me.fng'), NOW)).toBe('never_run')
  })
  it('needs_key wins over off', () => {
    expect(rowStatus({ ...by('cryptopanic'), enabled: false }, NOW)).toBe('needs_key')
  })
  it('a retry time in the past is no longer retrying', () => {
    expect(rowStatus({ ...by('rss.theblock'), disabled_until: iso(NOW - MIN) }, NOW)).toBe('failing')
  })
  it('starting holds until a success newer than the moment it was turned on', () => {
    const s = by('rss.coindesk')
    expect(rowStatus(s, NOW, { 'rss.coindesk': NOW - 30_000 })).toBe('starting')
    expect(rowStatus(s, NOW, { 'rss.coindesk': NOW - 2 * MIN })).toBe('working')
  })
})

describe('summary', () => {
  it('mixed: trouble with the retrying and failing sources, keys missing for CryptoPanic', () => {
    const sm = summary(mixed(NOW), NOW)
    expect(sm.state).toBe('trouble')
    expect(sm.trouble.map((x) => x.s.key)).toEqual(['rss.theblock', 'rss.yahoo'])
    expect(sm.missingKeys).toEqual(['cryptopanic'])
    expect(sm.down).toEqual([])
  })
  it('a kind whose API summary is all_down gets a notice', () => {
    const now = NOW
    const r = report([
      source('rss.coindesk', 'crypto_news', now, { status: 'degraded', consecutive_failures: 3, disabled_until: iso(now + 9 * MIN) }),
      source('rss.decrypt', 'crypto_news', now, { enabled: false, status: 'disabled' }),
      source('rss.cnbc', 'markets_news', now),
    ])
    expect(r.summary.crypto_news?.all_down).toBe(true)
    const sm = summary(r, now)
    expect(sm.down).toEqual(['crypto_news'])
    expect(downCauses(sm.rows, 'crypto_news', now)).toEqual({ retrying: 1, needs_key: 0, off: 1, nextTry: now + 9 * MIN })
  })
  it('allOff when nothing is usable, and no notices', () => {
    const r = report([source('rss.coindesk', 'crypto_news', NOW, { enabled: false, status: 'disabled' })])
    const sm = summary(r, NOW)
    expect(sm.state).toBe('allOff')
    expect(sm.down).toEqual([])
  })
  it('firstRun when every usable source is never run', () => {
    const r = report([source('rss.coindesk', 'crypto_news', NOW, { last_success_at: null, status: 'never_run' })])
    expect(summary(r, NOW).state).toBe('firstRun')
  })
  it('stalled when every fast feed is overdue by 3 polls + 60 s', () => {
    const r = report([
      source('rss.coindesk', 'crypto_news', NOW, { poll_seconds: 120, last_success_at: iso(NOW - 18 * MIN) }),
      source('alternative_me.fng', 'sentiment', NOW, { poll_seconds: 3600, last_success_at: iso(NOW - 40 * MIN) }),
    ])
    const sm = summary(r, NOW)
    expect(sm.state).toBe('stalled')
    expect(sm.newestCheck).toBe(NOW - 18 * MIN)
  })
  it('noneWorking when only troubled sources are usable', () => {
    const r = report([source('rss.coindesk', 'crypto_news', NOW, { consecutive_failures: 2 })])
    expect(summary(r, NOW).state).toBe('noneWorking')
  })
})

describe('withEnabled (the optimistic toggle)', () => {
  it('turning a source off marks it disabled and recomputes all_down', () => {
    const r = report([source('rss.coindesk', 'crypto_news', NOW), source('rss.cnbc', 'markets_news', NOW)])
    const next = withEnabled(r, 'rss.coindesk', false, NOW)
    expect(next.data[0]).toMatchObject({ enabled: false, status: 'disabled' })
    expect(next.summary.crypto_news?.all_down).toBe(true)
    expect(next.summary.markets_news?.all_down).toBe(false)
    expect(r.data[0]!.enabled).toBe(true)
  })
  it('turning finnhub.calendar off releases ForexFactory, which shows Starting', () => {
    const r = mixed(NOW)
    expect(startedBy(r, 'finnhub.calendar', false)).toEqual(['forexfactory.calendar'])
    const next = withEnabled(r, 'finnhub.calendar', false, NOW)
    const ff = next.data.find((s) => s.key === 'forexfactory.calendar')!
    expect(ff.covered_by).toBeNull()
    expect(rowStatus(ff, NOW, { 'forexfactory.calendar': NOW })).toBe('starting')
  })
})

describe('layout helpers', () => {
  it('groups in fixed order, rows sorted by name, empty groups dropped', () => {
    const g = groups(mixed(NOW).data)
    expect(g.map((x) => x.cat)).toEqual(['exchange', 'crypto_news', 'markets_news', 'calendar', 'sentiment'])
    expect(g[1]!.rows.map((s) => s.name)).toEqual(['CoinDesk', 'CryptoPanic', 'The Block'])
  })
  it('monograms come from the provider', () => {
    expect(monogram({ provider: 'finnhub', name: 'Finnhub news' })).toBe('FH')
    expect(monogram({ provider: 'okx', name: 'x' })).toBe('OKX')
    expect(monogram({ provider: 'unknown', name: 'Some Feed' })).toBe('SO')
  })
  it('missing keys are listed once per provider', () => {
    const now = NOW
    const rows = [
      source('finnhub.news', 'markets_news', now, { provider: 'finnhub', requires_key: true }),
      source('finnhub.calendar', 'calendar', now, { provider: 'finnhub', requires_key: true }),
      source('cryptopanic', 'crypto_news', now, { provider: 'cryptopanic', requires_key: true }),
    ]
    expect(missingKeyProviders(rows)).toEqual(['finnhub', 'cryptopanic'])
  })
})

describe('the wire', () => {
  it('sums feeds per hour, ignores structured sources, and picks a nice max', () => {
    const w = wire(mixed(NOW).data)
    expect(w.hours).toHaveLength(24)
    // 6 feed rows with hourly (i % 5) + 1, CryptoPanic all zero: 5 feeds count.
    expect(w.hours[0]!.total).toBe(5)
    expect(w.hours[4]!.total).toBe(25)
    expect(w.max).toBe(25)
    expect(w.cats.map((c) => c.cat)).toEqual(['exchange', 'crypto_news', 'markets_news'])
  })
  it('niceMax steps', () => {
    expect(niceMax(0)).toBe(4)
    expect(niceMax(18)).toBe(20)
    expect(niceMax(501)).toBe(600)
  })
  it('the last bucket is the current UTC hour', () => {
    expect(bucketStart(NOW, 23)).toBe(Date.parse('2026-10-28T11:00:00Z'))
    expect(bucketStart(NOW, 0)).toBe(Date.parse('2026-10-27T12:00:00Z'))
  })
  it('US market hours come from the New York calendar (16:30–23:00 Baghdad on 28 Oct 2026, an hour later after 1 Nov)', () => {
    const late = Date.parse('2026-10-28T20:30:00Z')
    const s = usSessions(late)
    const from = bucketStart(late, 0)
    const last = s[s.length - 1]!
    expect(from + last.start * 3_600_000).toBe(Date.parse('2026-10-28T13:30:00Z'))
    expect(from + last.end * 3_600_000).toBe(Date.parse('2026-10-28T20:00:00Z'))
    const nov = Date.parse('2026-11-02T21:30:00Z')
    const t = usSessions(nov)
    const f2 = bucketStart(nov, 0)
    expect(f2 + t[t.length - 1]!.start * 3_600_000).toBe(Date.parse('2026-11-02T14:30:00Z'))
  })
  it('no band on a weekend', () => {
    expect(usSessions(Date.parse('2026-11-01T12:00:00Z'))).toEqual([])
  })
})
