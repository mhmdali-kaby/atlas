import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import type { Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { Message } from './Message'

/*
 * ATL-70: the news_alert card, driven by ATL-65's shared envelope fixture
 * (services/ingest/tests/fixtures/alerts/news_alert_envelope.json). The bridge stores the
 * payload verbatim as `card` and the headline as inert `content_md` (ATL-65 handoff).
 */
const ENVELOPE = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../services/ingest/tests/fixtures/alerts/news_alert_envelope.json'), 'utf8'),
) as { id: string; ts: string; payload: Record<string, unknown> }

function fromBridge(payload: Record<string, unknown> = {}): WarroomMessage {
  const card = { ...ENVELOPE.payload, ...payload }
  return {
    id: ENVELOPE.id,
    thread_id: 'main-1',
    author_type: 'system',
    author: 'system:news',
    kind: 'news_alert',
    content_md: String(card.headline ?? ''),
    card,
    reply_to: null,
    mentions: [],
    reasoning_md: null,
    tool_calls: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    created_at: ENVELOPE.ts,
  }
}

function draw(message: WarroomMessage, lang: Lang = 'en') {
  void i18n.changeLanguage(lang)
  const noop = () => {}
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Message
        message={message}
        prev={undefined}
        parent={undefined}
        lang={lang}
        active={false}
        tabbable
        flash={false}
        onJump={noop}
        onReply={noop}
        onMenuChange={noop}
      />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('NewsAlertCard', () => {
  it('draws the fixture as the designed card, not the GenericCard', () => {
    const { container } = draw(fromBridge())
    const card = screen.getByTestId('news-card')
    expect(container.querySelector('.kind-card')).toBeNull()
    expect(within(card).getByText('News alert')).toBeInTheDocument()
    expect(screen.getByTestId('news-headline')).toHaveTextContent('Tesla reports third-quarter earnings')
    expect(within(card).getByRole('img', { name: 'Importance 4 of 5' })).toBeInTheDocument()
    expect(card).toHaveAttribute('data-importance', '4')
    expect(card.querySelectorAll('.news-pips i.is-on')).toHaveLength(4)
    expect(screen.getByTestId('news-type')).toHaveTextContent('Earnings')
    // Linked instrument families as chips.
    const chips = within(card).getByRole('list', { name: 'Linked instruments' })
    expect(within(chips).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['TSLA-USDT'])
    // 12:00:00Z is 15:00 in Baghdad, from the source's display name.
    expect(screen.getByTestId('news-by')).toHaveTextContent('Published by CNBC Top News at 15:00')
    const time = within(screen.getByTestId('news-by')).getByText('15:00')
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('dateTime', '2026-10-29T12:00:00Z')
    expect(time.getAttribute('title')).toContain('15:00:00')
  })

  it('links the article in a new tab with noopener noreferrer, naming its host', () => {
    draw(fromBridge())
    const link = screen.getByTestId('news-link')
    expect(link).toHaveAttribute('href', 'https://www.cnbc.com/2026/10/29/tesla-q3-earnings.html')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveTextContent('Read on cnbc.com')
  })

  it('shows untrusted headline and source text literally, never as HTML or markdown', () => {
    const headline = '<script>alert(1)</script> **bold** [x](https://evil.example) `code`'
    const { container } = draw(fromBridge({ headline, source_name: '<strong>Evil</strong> {{time}}' }))
    expect(screen.getByTestId('news-headline').textContent).toBe(headline)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('.news-card strong')).toBeNull()
    expect(container.querySelector('.news-headline a, .news-headline code')).toBeNull()
    expect(screen.getByTestId('news-by')).toHaveTextContent('Published by <strong>Evil</strong> {{time}} at 15:00')
  })

  it('drops a non-http(s) url: no link, source and time only', () => {
    draw(fromBridge({ url: 'javascript:alert(1)' }))
    expect(screen.queryByTestId('news-link')).toBeNull()
    expect(screen.getByTestId('news-by')).toHaveTextContent('Published by CNBC Top News at 15:00')
  })

  it.each([
    [0.78, '+0.78', 'positive', 'profit'],
    [-0.31, '−0.31', 'negative', 'loss'],
    [0.02, '0.00', 'neutral', 'zero'],
  ])('sentiment %s reads %s with a sign, an icon and a spoken word', (value, text, word, tone) => {
    draw(fromBridge({ sentiment: value }))
    const s = screen.getByTestId('news-sentiment')
    expect(s).toHaveTextContent(`Sentiment${text} ${word}`)
    const signed = s.querySelector('[data-sign]')!
    expect(signed).toHaveAttribute('data-sign', tone)
    expect(signed.querySelector('svg')).toEqual(tone === 'zero' ? null : expect.anything())
  })

  it('with no instruments and no sentiment has no meta row, no empty chips and no NaN', () => {
    const { container } = draw(fromBridge({ inst_ids: [], sentiment: null, importance: 5 }))
    expect(container.querySelector('.news-meta')).toBeNull()
    expect(container.querySelector('.news-inst')).toBeNull()
    expect(screen.queryByTestId('news-sentiment')).toBeNull()
    expect(container.textContent).not.toMatch(/NaN|undefined|null/)
    expect(screen.getByTestId('news-card')).toHaveClass('imp-5')
  })

  it('leaves out a non-finite sentiment and an unknown event type, and clamps importance', () => {
    const { container } = draw(fromBridge({ sentiment: 'NaN', event_type: 'weather', importance: 9 }))
    expect(screen.queryByTestId('news-sentiment')).toBeNull()
    expect(screen.queryByTestId('news-type')).toBeNull()
    expect(screen.getByRole('img', { name: 'Importance 5 of 5' })).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/NaN/)
  })

  it('falls back to the GenericCard when the card has no headline', () => {
    const { container } = draw({ ...fromBridge(), card: null, content_md: 'Old alert' })
    expect(screen.queryByTestId('news-card')).toBeNull()
    expect(container.querySelector('.kind-card[data-kind="news_alert"]')).not.toBeNull()
  })

  it('in Arabic uses the translated strings and keeps figures in LTR islands', () => {
    draw(fromBridge({ sentiment: -0.31 }), 'ar')
    const card = screen.getByTestId('news-card')
    expect(within(card).getByText('تنبيه إخباري')).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: 'الأهمية 4 من 5' })).toBeInTheDocument()
    expect(screen.getByTestId('news-type')).toHaveTextContent('نتائج أعمال')
    expect(screen.getByTestId('news-by')).toHaveTextContent('نشره CNBC Top News الساعة 15:00')
    expect(screen.getByTestId('news-link')).toHaveTextContent('اقرأه على cnbc.com')
    expect(within(screen.getByTestId('news-sentiment')).getByText('−0.31')).toHaveAttribute('dir', 'ltr')
  })
})
