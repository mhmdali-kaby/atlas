import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import type { Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { alertAnalysisMessage, instrumentBriefMessage, morningBriefMessage, solAlertMessage } from './fixtures/newsBrief'
import { Message } from './Message'
import { outages, readNewsBrief } from './newsBrief'

/* ATL-116: News Intelligence's news_brief card in its three forms (design v9 §B). */

function draw(message: WarroomMessage, lang: Lang = 'en', parent?: WarroomMessage) {
  void i18n.changeLanguage(lang)
  const noop = () => {}
  const router = createMemoryRouter([
    {
      path: '/',
      element: (
        <QueryClientProvider client={new QueryClient()}>
          <Message message={message} prev={undefined} parent={parent} lang={lang} active={false} tabbable flash={false} onJump={noop} onReply={noop} onMenuChange={noop} />
        </QueryClientProvider>
      ),
    },
  ])
  return render(<RouterProvider router={router} />)
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

/** Every story shows its status as text (and an icon), and each source both its Baghdad and UTC time. */
function expectStoriesLabelled(card: HTMLElement) {
  const stories = within(card).getAllByTestId('nb-story')
  expect(stories.length).toBeGreaterThan(0)
  for (const story of stories) {
    const badge = within(story).getByTestId('nb-status')
    const word = i18n.t(`warroom:tag.${badge.dataset.status}`)
    expect(['CONFIRMED', 'REPORTED', 'RUMOUR']).toContain(badge.dataset.status)
    expect(badge.textContent!.startsWith(word)).toBe(true)
    expect(badge.querySelector('svg')).not.toBeNull()
    for (const time of within(story).queryAllByTestId('nb-time')) {
      expect(within(time).getByTestId('nb-baghdad').textContent).toMatch(/\d{2}:\d{2}/)
      expect(within(time).getByTestId('nb-utc').textContent).toMatch(/^\d{2}:\d{2} UTC$/)
    }
  }
}

describe('NewsBriefCard: the three forms', () => {
  it('morning brief: top stories (5 then all 10), the 48h calendar by day, news flow', () => {
    draw(morningBriefMessage())
    const card = screen.getByTestId('nb-card')
    expect(card).toHaveAttribute('data-form', 'morning_brief')
    expect(card).toHaveAccessibleName('Morning brief')
    expect(within(card).getByTestId('hv-asof')).toHaveTextContent(/Data as of (28 Oct, )?07:29/)

    const top = within(card).getByTestId('nb-top')
    expect(within(top).getAllByTestId('nb-story')).toHaveLength(5)
    expectStoriesLabelled(card)
    fireEvent.click(within(top).getByTestId('nb-more'))
    expect(within(top).getAllByTestId('nb-story')).toHaveLength(10)
    expect(within(top).getByTestId('nb-more')).toHaveAttribute('aria-expanded', 'true')
    expectStoriesLabelled(card)

    // RUMOUR is the only tinted badge; its tooltip says it is unverified.
    const rumour = within(top).getAllByTestId('nb-status').find((b) => b.dataset.status === 'RUMOUR')!
    expect(rumour).toHaveClass('is-rumour')
    expect(rumour).toHaveAttribute('title', 'Unverified: no reputable or official source has carried this yet.')
    // CONFIRMED names its deciding sources, matched to the stories' source names.
    const confirmed = within(top).getAllByTestId('nb-status')[0]!
    expect(confirmed).toHaveAttribute('title', 'Confirmed: CNBC and Reuters.')

    // First source with "and N more", which expands in place.
    const tesla = within(top).getAllByTestId('nb-story')[2]!
    expect(within(tesla).getAllByTestId('nb-src')).toHaveLength(1)
    fireEvent.click(within(tesla).getByRole('button', { name: 'and 2 more sources' }))
    expect(within(tesla).getAllByTestId('nb-src')).toHaveLength(3)
    // 01:47 UTC is 04:47 in Baghdad.
    const first = within(tesla).getAllByTestId('nb-time')[0]!
    expect(first).toHaveTextContent(/04:47 \(01:47 UTC\)/)

    const cal = within(card).getByTestId('nb-cal')
    expect(within(cal).getAllByTestId('nb-day')).toHaveLength(2)
    expect(within(cal).getAllByTestId('nb-event')).toHaveLength(10)
    // A missing forecast is a dash read as "None".
    expect(within(cal).getAllByTestId('nb-event')[0]).toHaveTextContent('None')

    const flow = within(card).getByTestId('nb-flow')
    expect(within(flow).getAllByTestId('nb-flow-row')).toHaveLength(5)
    expect(flow).toHaveTextContent('42 stories')
  })

  it('instrument brief: title, what to watch, net sentiment 24h/72h, priced in, every source, upcoming catalysts', () => {
    draw(instrumentBriefMessage())
    const card = screen.getByTestId('nb-card')
    expect(card).toHaveAttribute('data-form', 'instrument_brief')
    expect(within(card).getByTestId('nb-title')).toHaveTextContent('TSLA-USDT-SWAP')
    expect(within(card).getByTestId('nb-watch')).toHaveTextContent('The report at 23:05 decides this.')
    const sum = within(card).getByTestId('nb-sum')
    expect(sum).toHaveTextContent('Net sentiment, 24 hours')
    expect(sum).toHaveTextContent('−0.18')
    expect(sum).toHaveTextContent('+0.05')
    expect(within(card).getByTestId('nb-priced')).toHaveTextContent('Partly')
    expectStoriesLabelled(card)
    // No "more sources" toggle: the news brief lists them all.
    expect(within(card).queryByTestId('nb-src-more')).toBeNull()
    const stories = within(card).getAllByTestId('nb-story')
    expect(within(stories[0]!).getAllByTestId('nb-src')).toHaveLength(3)
    // Moves keep their sign in label ink.
    expect(within(stories[1]!).getByTestId('nb-move')).toHaveTextContent('−0.9 ATR')
    expect(within(card).getAllByTestId('nb-event')).toHaveLength(2)
  })

  it('alert analysis: replies under the ingest alert with status by sources, sources, move since first seen', () => {
    draw(alertAnalysisMessage(), 'en', solAlertMessage())
    const article = document.querySelector('[data-msg-id="msg-sol-analysis"]') as HTMLElement
    // The reply quote of the alert sits above the card.
    expect(article.querySelector('.msg-quote')).not.toBeNull()
    const card = within(article).getByTestId('nb-card')
    expect(card).toHaveAttribute('data-form', 'alert_analysis')
    const facts = within(card).getByTestId('nb-facts')
    expect(within(facts).getByTestId('nb-status')).toHaveTextContent('CONFIRMED')
    expect(facts).toHaveTextContent('by CoinDesk and The Block')
    expect(within(facts).getAllByTestId('nb-src')).toHaveLength(2)
    for (const time of within(facts).getAllByTestId('nb-time')) {
      expect(within(time).getByTestId('nb-baghdad').textContent).toMatch(/\d{2}:\d{2}/)
      expect(within(time).getByTestId('nb-utc')).toHaveTextContent(/UTC$/)
    }
    expect(within(facts).getByText('09:02 UTC')).toBeInTheDocument()
    expect(within(facts).getAllByTestId('nb-move').map((m) => m.textContent)).toEqual(['+1.2 ATR since first seen', '+1.1 ATR since first seen'])
    expect(within(facts).getByTestId('nb-priced')).toHaveTextContent('Partly')
  })

  it('renders in Arabic with Arabic labels and Western-digit times', () => {
    draw(morningBriefMessage(), 'ar')
    const card = screen.getByTestId('nb-card')
    expect(card).toHaveAccessibleName('موجز الصباح')
    expect(card).toHaveTextContent('أبرز الأخبار')
    expectStoriesLabelled(card)
  })
})

describe('NewsBriefCard: untrusted text and links', () => {
  it('opens source links in a new tab with noopener noreferrer, and only for http(s) URLs', () => {
    draw(morningBriefMessage())
    const card = screen.getByTestId('nb-card')
    const links = within(card).getAllByTestId('nb-src-link')
    expect(links.length).toBeGreaterThan(0)
    for (const a of links) {
      expect(a).toHaveAttribute('target', '_blank')
      expect(a).toHaveAttribute('rel', 'noopener noreferrer')
      expect(a.getAttribute('href')).toMatch(/^https:\/\//)
    }
    // A source with no URL shows its name and time without a link.
    fireEvent.click(within(card).getByTestId('nb-more'))
    const okx = within(card).getAllByTestId('nb-story')[8]!
    expect(within(okx).getByTestId('nb-src')).toHaveTextContent('OKX announcements')
    expect(within(okx).queryByTestId('nb-src-link')).toBeNull()
  })

  it('a javascript: URL never becomes a link', () => {
    draw(instrumentBriefMessage())
    const rumour = screen.getAllByTestId('nb-story')[2]!
    expect(within(rumour).getByTestId('nb-src')).toHaveTextContent('r/teslainvestorsclub')
    expect(within(rumour).queryByTestId('nb-src-link')).toBeNull()
    expect(document.querySelector('a[href^="javascript"]')).toBeNull()
  })

  it('a headline containing HTML tags renders as literal text', () => {
    draw(morningBriefMessage())
    const card = screen.getByTestId('nb-card')
    fireEvent.click(within(card).getByTestId('nb-more'))
    const headline = within(card)
      .getAllByTestId('nb-headline')
      .find((h) => h.textContent?.includes('Coinbase'))!
    expect(headline.textContent).toBe('10. Coinbase <b>lists</b> SUI and APT perpetuals for US users')
    expect(headline.querySelector('b')).toBeNull()
    // A move with a null value is a chip with no figure; its reason is the tooltip.
    const story = headline.closest('[data-testid="nb-story"]') as HTMLElement
    const chips = within(story).getAllByTestId('nb-inst')
    expect(chips[1]).toHaveTextContent('APT-USDT-SWAP')
    expect(within(chips[1]!).queryByTestId('nb-move')).toBeNull()
    expect(chips[1]).toHaveAttribute('title', 'no closed 1m candle for APT-USDT-SWAP to price it now')
  })
})

describe('NewsBriefCard: degraded news classes', () => {
  it('shows the notice for degraded_asset_classes ["crypto"] with the Baghdad time the news stopped', () => {
    draw(morningBriefMessage({}, { degraded_asset_classes: ['crypto'], degraded_since: { crypto: '2026-10-28T03:10:00Z' } }))
    const bands = screen.getAllByTestId('nb-degraded')
    expect(bands).toHaveLength(1)
    expect(bands[0]).toHaveTextContent('No crypto news since 06:10')
    expect(bands[0]).toHaveTextContent('Market Scout caps crypto conviction at 0.50 until a source works again.')
    expect(within(bands[0]!).getByRole('link', { name: /Open Data sources/ })).toHaveAttribute('href', '/settings/sources')
  })

  it('a class with no degraded_since says news is not coming in, with no time', () => {
    draw(instrumentBriefMessage({}, { degraded_asset_classes: ['crypto'], degraded_since: { crypto: null } }))
    expect(screen.getByTestId('nb-degraded')).toHaveTextContent('No crypto news is coming in')
  })

  it('one band per outage: classes that stopped together share one, others get their own', () => {
    draw(
      alertAnalysisMessage({}, {
        degraded_asset_classes: ['crypto', 'stock', 'etf'],
        degraded_since: { crypto: '2026-10-28T03:10:00Z', stock: '2026-10-28T01:00:20Z', etf: '2026-10-28T01:00:40Z' },
      }),
    )
    const bands = screen.getAllByTestId('nb-degraded')
    expect(bands.map((b) => b.querySelector('.nb-deg-title')!.textContent)).toEqual(['No stock and ETF news since 04:00', 'No crypto news since 06:10'])
  })

  it('no band when no class is degraded', () => {
    draw(morningBriefMessage())
    expect(screen.queryByTestId('nb-degraded')).toBeNull()
  })
})

describe('NewsBriefCard: number guard', () => {
  it('marks untraced numbers in the lead, in what to watch and in card figures, with the footer note', () => {
    draw(instrumentBriefMessage())
    const article = document.querySelector('[data-msg-id="msg-news-tsla"]') as HTMLElement
    const marks = within(article).getAllByTestId('unv')
    expect(marks.map((m) => m.textContent)).toEqual(['9%', '438', '−0.18'])
    for (const m of marks) expect(m).toHaveAccessibleDescription('Not found in any tool output')
    expect(within(article).getByTestId('nb-unv-note')).toHaveTextContent('3 numbers in this answer aren’t in the tools’ data.')
  })
})

describe('readNewsBrief', () => {
  it('ignores other cards, needs data_as_of, and infers an old card’s form from its parent', () => {
    expect(readNewsBrief({ type: 'history_verdict' } as never)).toBeNull()
    expect(readNewsBrief({ type: 'news_brief' } as never)).toBeNull()
    const legacy = { type: 'news_brief', data_as_of: '2026-10-28T04:00:00Z' } as never
    expect(readNewsBrief(legacy)!.form).toBe('instrument_brief')
    expect(readNewsBrief(legacy, 'news_alert')!.form).toBe('alert_analysis')
  })

  it('groups outages by minute, timed ones in order, the untimed one last', () => {
    expect(outages({ degraded: ['a', 'b', 'c'], degradedSince: { a: null, b: '2026-10-28T03:10:00Z', c: '2026-10-28T01:00:00Z' } })).toEqual([
      { since: '2026-10-28T01:00:00Z', classes: ['c'] },
      { since: '2026-10-28T03:10:00Z', classes: ['b'] },
      { since: null, classes: ['a'] },
    ])
  })
})
