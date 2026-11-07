import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import ar from '../../i18n/locales/ar/warroom.json'
import en from '../../i18n/locales/en/warroom.json'
import type { Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { apiItem, apiWatchlist, BTC, cards, DAY, pinItem, TSLA, watchlistMessage, XAU } from './fixtures/watchlist'
import { Message } from './Message'
import { readWatchlistCard, usSessions, watchlistKey, type ApiWatchlist } from './watchlist'

/*
 * ATL-115: Market Scout's watchlist card (design v9 §A), driven by the card shapes ATL-108/109/120
 * publish and ATL-103's API. The API is seeded into the query cache, so nothing is fetched.
 */

function draw(message: WarroomMessage, opts: { lang?: Lang; api?: ApiWatchlist | null; onJump?: (id: string) => void } = {}) {
  const lang = opts.lang ?? 'en'
  void i18n.changeLanguage(lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(watchlistKey('demo', DAY), { env: 'demo', date: DAY, watchlist: opts.api ?? null })
  const noop = () => {}
  const view = render(
    <QueryClientProvider client={qc}>
      <Message
        message={message}
        prev={undefined}
        parent={undefined}
        lang={lang}
        active={false}
        tabbable
        flash={false}
        onJump={opts.onJump ?? noop}
        onReply={noop}
        onMenuChange={noop}
      />
    </QueryClientProvider>,
  )
  return { ...view, qc, card: screen.getByTestId('wl-card') }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  // 14:32 Baghdad: the edit window closed hours ago.
  vi.setSystemTime(new Date('2026-10-28T11:32:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  void i18n.changeLanguage('en')
})

describe('readWatchlistCard', () => {
  it('reads every pick field and keeps the date a plain day', () => {
    const c = readWatchlistCard(cards.published())!
    expect(c.date).toBe('2026-10-28')
    expect(c.picks.map((p) => p.instId)).toEqual(['BTC-USDT-SWAP', 'XAU-USDT-SWAP', 'TSLA-USDT-SWAP'])
    expect(c.picks[0]).toMatchObject({ bias: 'long', conviction: 0.58, levels: { support: 118200, resistance: 119950, invalidation: 116800 } })
    expect(c.shortlistCount).toBe(25)
    expect(c.trigger).toBeNull() // "schedule" is a string: not a re-run
  })

  it('returns null for something that is not a watchlist card', () => {
    expect(readWatchlistCard({ type: 'news_brief' })).toBeNull()
    expect(readWatchlistCard({ type: 'watchlist', watchlist_id: 'x', date: '28/10/2026' })).toBeNull()
  })

  it('treats an ATL-103 pin placeholder as not analysed yet', () => {
    const c = readWatchlistCard(cards.published())!
    expect(c.picks.every((p) => !p.placeholder)).toBe(true)
  })
})

describe('usSessions', () => {
  it('follows New York’s clocks: 16:30–23:00 Baghdad before 1 Nov, 17:30–00:00 after', () => {
    const fmt = (ms: number) => new Date(ms + 3 * 3_600_000).toISOString().slice(11, 16)
    const before = usSessions(Date.parse('2026-10-28T05:00:00Z'), Date.parse('2026-10-29T05:00:00Z'))
    expect(before.map(([, , o, c]) => `${fmt(o)}–${fmt(c)}`)).toEqual(['16:30–23:00'])
    const after = usSessions(Date.parse('2026-11-03T05:00:00Z'), Date.parse('2026-11-04T05:00:00Z'))
    expect(after.map(([, , o, c]) => `${fmt(o)}–${fmt(c)}`)).toEqual(['17:30–00:00'])
  })
})

describe('WatchlistCard', () => {
  it('renders a published watchlist with every pick field', () => {
    const { card } = draw(watchlistMessage(cards.published()))
    expect(within(card).getByTestId('wl-title')).toHaveTextContent('Wednesday 28 October')
    expect(within(card).getByTestId('wl-funnel')).toHaveTextContent('3 picks from a shortlist of 25 instruments.')
    expect(within(card).getByTestId('wl-backdrop').querySelectorAll('p')).toHaveLength(3)
    expect(within(card).getByTestId('wl-rail')).toHaveAccessibleName(/US CPI \(Sep\) at 15:30 for BTC and XAU/)
    expect(within(card).getByTestId('wl-rail')).toHaveAccessibleName(/US market hours 16:30 to 23:00/)

    const rows = within(card).getAllByTestId('wl-pick')
    expect(rows).toHaveLength(3)
    const btc = rows[0]!
    expect(within(btc).getByTestId('wl-inst')).toHaveTextContent('BTC-USDT-SWAP')
    expect(within(btc).getByTestId('wl-class')).toHaveTextContent('Crypto')
    // Bias is an icon plus a word, never colour alone.
    expect(within(btc).getByTestId('wl-bias')).toHaveTextContent('Long')
    expect(within(btc).getByTestId('wl-bias-icon-long')).toBeInTheDocument()
    expect(within(btc).getByTestId('wl-conv')).toHaveAccessibleName('Conviction 0.58 of 1')
    expect(within(btc).getByTestId('wl-thesis')).toHaveTextContent('Half an ATR under its 20-day high')
    expect(within(btc).getByTestId('wl-chip')).toHaveTextContent('15:30')
    expect(within(btc).getByTestId('wl-chip')).toHaveTextContent('US CPI (Sep)')
    expect(within(btc).getByTestId('wl-levels')).toHaveTextContent('Support 118,200')
    expect(within(btc).getByTestId('wl-levels')).toHaveTextContent('Resistance 119,950')
    expect(within(btc).getByTestId('wl-inval')).toHaveTextContent('Invalidation')
    expect(within(btc).getByTestId('wl-inval')).toHaveTextContent('A 4-hour close under 116,800 ends the breakout case.')

    expect(within(rows[1]!).getByTestId('wl-bias')).toHaveTextContent('Short')
    expect(within(rows[1]!).getAllByTestId('wl-chip')).toHaveLength(2)
    expect(within(rows[2]!).getByTestId('wl-class')).toHaveTextContent('Stock')
    expect(within(rows[2]!).getByTestId('wl-bias')).toHaveTextContent('Neutral')

    const rej = within(card).getAllByTestId('wl-rejected-row')
    expect(rej).toHaveLength(3)
    expect(rej[0]).toHaveTextContent('NVDA-USDT-SWAP')
    expect(rej[0]).toHaveTextContent('chasing')
    // Window over: no controls, and the footer says when it closed.
    expect(within(card).queryByTestId('wl-band')).toBeNull()
    expect(within(card).queryByTestId('wl-remove')).toBeNull()
    expect(within(card).getByTestId('wl-foot')).toHaveTextContent('Editing closed at 08:10.')
    expect(within(card).queryByTestId('wl-partial')).toBeNull()
  })

  it('shows the partial-evidence notice and caps with their reason', () => {
    const { card } = draw(watchlistMessage(cards.partialEvidence()))
    expect(within(card).getByTestId('wl-partial')).toHaveTextContent(
      'Partial evidence. Historian didn’t reply in time, so every conviction is capped at 0.50.',
    )
    const convs = within(card).getAllByTestId('wl-conv')
    expect(convs[0]).toHaveAttribute('data-capped', 'true')
    expect(convs[0]).toHaveAttribute('title', 'Capped at 0.50 because the evidence was partial. Market Scout’s estimate was 0.58.')
    expect(convs[2]).not.toHaveAttribute('data-capped')
  })

  it('caps only the class whose news is down, and says since when', () => {
    const { card } = draw(watchlistMessage(cards.newsDown()))
    expect(within(card).getByTestId('wl-degraded')).toHaveTextContent('No crypto news has come in since 06:10, so crypto convictions are capped at 0.50.')
    const convs = within(card).getAllByTestId('wl-conv')
    expect(convs[0]).toHaveAttribute('title', 'Capped at 0.50 because no crypto news has come in since 06:10. Market Scout’s estimate was 0.58.')
    expect(convs[1]).not.toHaveAttribute('data-capped') // XAU keeps 0.52
    expect(within(card).queryByTestId('wl-partial')).toBeNull()
  })

  it('shows “No watchlist today” with the reason for an empty shortlist', () => {
    const { card } = draw(watchlistMessage(cards.emptyShortlist(), { content_md: 'No watchlist today: no instrument passed the filter.' }))
    expect(within(card).getByTestId('wl-title')).toHaveTextContent('No watchlist today')
    expect(within(card).getByTestId('wl-empty-reason')).toHaveTextContent(en.wl.empty.empty_shortlist)
    expect(within(card).queryByTestId('wl-rail')).toBeNull()
    expect(within(card).queryByTestId('wl-list')).toBeNull()
  })

  it('shows the no-picks reason with the shortlist size and keeps the rejected list', () => {
    const { card } = draw(watchlistMessage(cards.noPicks()))
    expect(within(card).getByTestId('wl-empty-reason')).toHaveTextContent('Market Scout reviewed a shortlist of 25 instruments and found nothing')
    expect(within(card).getAllByTestId('wl-rejected-row')).toHaveLength(3)
    expect(within(card).getByTestId('wl-backdrop')).toBeInTheDocument()
  })

  it('names an intraday re-run’s trigger and what changed, and jumps to the alert', () => {
    const onJump = vi.fn()
    const { card } = draw(watchlistMessage(cards.rerun()), { onJump })
    expect(within(card).getByTestId('wl-title')).toHaveTextContent('Updated at 12:10')
    const trig = within(card).getByTestId('wl-trigger')
    expect(trig).toHaveTextContent('After an importance-5 alert at 12:04')
    expect(trig).toHaveTextContent('SEC approves spot Solana ETFs')
    fireEvent.click(trig)
    expect(onJump).toHaveBeenCalledWith('msg-alert')
    expect(within(card).getByTestId('wl-added')).toHaveTextContent('New since 08:00: SOL-USDT-SWAP.')
    expect(within(card).getByTestId('wl-dropped')).toHaveTextContent('Dropped since 08:00: TSLA-USDT-SWAP.')
    expect(within(within(card).getAllByTestId('wl-pick')[1]!).getByText('New')).toBeInTheDocument()
  })

  it('reads ATL-109’s vol_spike trigger', () => {
    const { card } = draw(watchlistMessage(cards.spike()))
    expect(within(card).getByTestId('wl-trigger')).toHaveTextContent('After a 3.4σ hourly move in NVDA-USDT-SWAP at 14:00')
  })

  it('says an older version was replaced, with the way to the update', () => {
    const onJump = vi.fn()
    const newer = apiWatchlist({ id: 'wl-v2', version: 2, created_at: '2026-10-28T09:10:00.000000Z', message_id: 'msg-wl-v2', edit_window_open: false })
    const { card } = draw(watchlistMessage(cards.published()), { api: newer, onJump })
    expect(within(card).getByTestId('wl-replaced')).toHaveTextContent('Replaced at 12:10 by an updated list.')
    fireEvent.click(within(card).getByText('Show the update'))
    expect(onJump).toHaveBeenCalledWith('msg-wl-v2')
  })

  it('draws an owner pin that is not analysed yet without a bias or a 0.00 conviction', () => {
    const api = apiWatchlist({ edit_window_open: false, items: [apiItem(BTC), apiItem(XAU), apiItem(TSLA), pinItem('ETH-USDT-SWAP', 4)] })
    const { card } = draw(watchlistMessage(cards.published()), { api })
    const eth = within(card).getAllByTestId('wl-pick')[3]!
    expect(eth).toHaveTextContent('Pinned by you')
    expect(eth).toHaveTextContent('The team includes it from the next analysis cycle.')
    expect(within(eth).queryByTestId('wl-bias')).toBeNull()
    expect(within(eth).queryByTestId('wl-conv')).toBeNull()
    expect(within(card).getByTestId('wl-foot')).toHaveTextContent('You pinned ETH-USDT-SWAP.')
  })

  it('inside the window shows the countdown, Remove and Pin, and folds them at zero', () => {
    vi.setSystemTime(new Date('2026-10-28T05:02:48Z')) // 7:12 left
    const { card } = draw(watchlistMessage(cards.published()), { api: apiWatchlist() })
    expect(within(card).getByTestId('wl-countdown')).toHaveTextContent('7:12 left')
    expect(within(card).getAllByTestId('wl-remove')).toHaveLength(3)
    expect(within(card).getByTestId('wl-pin-instrument')).toBeInTheDocument()
    expect(within(card).getAllByTestId('wl-pin-rejected')).toHaveLength(3)
    expect(within(card).getAllByTestId('wl-remove')[0]).toHaveAccessibleName('Remove BTC-USDT-SWAP from today’s watchlist')

    act(() => {
      vi.advanceTimersByTime(7 * 60_000 + 13_000)
    })
    expect(within(card).queryByTestId('wl-band')).toBeNull()
    expect(within(card).queryByTestId('wl-remove')).toBeNull()
    expect(within(card).getByTestId('wl-foot')).toHaveTextContent('Editing closed at 08:10.')
    expect(within(card).getByTestId('wl-live')).toHaveTextContent(en.wl.closedLive)
  })

  it('links a hovered row to its rail diamonds, and a hovered diamond to its rows', () => {
    const { card } = draw(watchlistMessage(cards.published()))
    const rows = within(card).getAllByTestId('wl-pick')
    const diamonds = () => within(card).getAllByTestId('wl-diamond')
    const linked = () => diamonds().filter((d) => d.classList.contains('is-linked'))
    expect(linked()).toHaveLength(0)

    fireEvent.mouseEnter(rows[0]!) // BTC: only the US CPI diamond is BTC's
    expect(linked()).toHaveLength(1)
    expect(linked()[0]!.title).toContain('US CPI (Sep)')
    fireEvent.mouseLeave(rows[0]!)
    expect(linked()).toHaveLength(0)

    const cpi = diamonds().find((d) => d.title.includes('US CPI (Sep)'))!
    fireEvent.mouseEnter(cpi)
    const linkedRows = within(card).getAllByTestId('wl-pick').filter((r) => r.classList.contains('is-linked'))
    expect(linkedRows.map((r) => r.getAttribute('data-wl-row'))).toEqual(['BTC-USDT-SWAP', 'XAU-USDT-SWAP'])
    fireEvent.mouseLeave(cpi)
    expect(within(card).getAllByTestId('wl-pick').some((r) => r.classList.contains('is-linked'))).toBe(false)
  })

  it('renders in Arabic with the bias word and the notice translated', () => {
    const { card } = draw(watchlistMessage(cards.newsDown()), { lang: 'ar' })
    expect(within(card).getAllByTestId('wl-bias')[0]).toHaveTextContent(ar.wl.bias.long)
    expect(within(card).getByTestId('wl-degraded')).toHaveTextContent('لم تصل أخبار العملات المشفرة منذ الساعة 06:10')
    expect(within(card).getByTestId('wl-funnel')).toHaveTextContent('3 اختيارات من قائمة مختصرة من 25 أداة.')
  })
})
