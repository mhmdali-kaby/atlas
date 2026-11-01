import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { btcCoveringMessage, tslaUnreliableMessage } from '../fixtures/positioning'
import { Message } from '../Message'
import { readEvidence, readPositioningReport } from './positioning'

/* ATL-140: the Derivatives & Sentiment Analyst's PositioningReport card (brief §7.5, design v11 Phase 2 §7). */

function draw(message: WarroomMessage, lang: Lang = 'en') {
  void i18n.changeLanguage(lang)
  const noop = () => {}
  const router = createMemoryRouter([
    {
      path: '/',
      element: (
        <QueryClientProvider client={new QueryClient()}>
          <Message message={message} prev={undefined} parent={undefined} lang={lang} active={false} tabbable flash={false} onJump={noop} onReply={noop} onMenuChange={noop} />
        </QueryClientProvider>
      ),
    },
  ])
  return render(<RouterProvider router={router} />)
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('PositioningReport card', () => {
  it('short covering: the regime label and its meaning, crowding 0.72 as 72%, and both squeeze risks', () => {
    draw(btcCoveringMessage())
    const card = screen.getByTestId('pos-card')
    expect(within(card).getByTestId('pos-inst')).toHaveTextContent('BTC-USDT-SWAP')
    expect(within(card).getByTestId('pos-regime')).toHaveTextContent('Short covering')
    expect(within(card).getByTestId('pos-meaning')).toHaveTextContent('Price up with open interest down: shorts are closing, not fresh buying.')
    // The quadrant fills today's cell, and only that one.
    const quad = within(card).getByTestId('pos-quad')
    expect(quad).toHaveAccessibleName('Open interest against price: Short covering, now.')
    const on = quad.querySelectorAll('[aria-current="true"]')
    expect(on).toHaveLength(1)
    expect(on[0]).toHaveAttribute('data-regime', 'short_covering')

    expect(within(card).getByTestId('pos-crowding')).toHaveTextContent('72%, shorts crowded')
    expect(within(card).getByTestId('pos-sq-long')).toHaveTextContent('18%')
    expect(within(card).getByTestId('pos-sq-short')).toHaveTextContent('64%')
    expect(within(card).getByTestId('pos-squeeze')).toHaveTextContent('Long squeeze18%Short squeeze64%')

    expect(within(card).getByTestId('pos-funding')).toHaveTextContent('shorts are paying to stay in')
    expect(within(card).getByTestId('pos-basis')).toHaveTextContent('+4.3 bps')
    expect(within(card).queryByTestId('pos-premium')).toBeNull()
    expect(within(card).getByTestId('pos-senti')).toHaveTextContent('−0.15')
    expect(within(card).getByTestId('pos-senti')).toHaveClass('is-neg')

    // Key evidence: the as_of moved out of the sentence into its own Baghdad time.
    const ev = within(card).getAllByTestId('pos-ev')
    expect(ev).toHaveLength(3)
    expect(ev[0]).toHaveTextContent('OI −6.0% in 24h with price +3.0%')
    expect(ev[0]).not.toHaveTextContent('as_of')
    expect(within(ev[0]!).getByTestId('pos-ev-asof')).toHaveAttribute('dateTime', '2026-10-28T11:00Z')
    expect(within(ev[0]!).getByTestId('pos-ev-asof')).toHaveTextContent('14:00')
  })

  it('supports_direction {long: opposes, short: supports}: a word and an icon for both sides', () => {
    draw(btcCoveringMessage())
    const card = screen.getByTestId('pos-card')
    const long = within(card).getByTestId('pos-stance-long')
    const short = within(card).getByTestId('pos-stance-short')
    expect(long).toHaveTextContent('Positioning: Opposes a long')
    expect(long).toHaveAttribute('data-stance', 'opposes')
    expect(short).toHaveTextContent('Positioning: Supports a short')
    expect(short).toHaveAttribute('data-stance', 'supports')
    // An icon in each pill, so colour is never the only signal (and a different icon per stance).
    const li = within(long).getByTestId('pos-stance-long-icon')
    const si = within(short).getByTestId('pos-stance-short-icon')
    expect(li.tagName.toLowerCase()).toBe('svg')
    expect(si.tagName.toLowerCase()).toBe('svg')
    expect(li.getAttribute('class')).toContain('lucide-circle-x')
    expect(si.getAttribute('class')).toContain('lucide-circle-check')
  })

  it('Arabic: the stance words and the regime are translated', () => {
    draw(btcCoveringMessage(), 'ar')
    const card = screen.getByTestId('pos-card')
    expect(within(card).getByTestId('pos-stance-long')).toHaveTextContent('يعارض الشراء')
    expect(within(card).getByTestId('pos-stance-short')).toHaveTextContent('يدعم البيع')
    expect(within(card).getByTestId('pos-regime')).toHaveTextContent('تغطية مراكز البيع')
    expect(within(card).getByTestId('pos-crowding')).toHaveTextContent('72%')
  })

  it('an unreliable report: the notice with its reason, and no stances, crowding, squeeze percentages or sentiment', () => {
    draw(tslaUnreliableMessage())
    const card = screen.getByTestId('pos-card')
    expect(card).toHaveAttribute('data-reliable', 'false')
    const notice = within(card).getByTestId('pos-unreliable')
    expect(notice).toHaveAttribute('role', 'note')
    expect(notice).toHaveTextContent('Unreliable data. Open interest reads 0, which is not plausible for a listed perp.')
    expect(within(card).queryByTestId('pos-squeeze')).toBeNull()
    expect(within(card).queryByTestId('pos-sq-long')).toBeNull()
    expect(within(card).queryByTestId('pos-sq-short')).toBeNull()
    expect(within(card).queryByTestId('pos-crowding')).toBeNull()
    expect(within(card).queryByTestId('pos-stances')).toBeNull()
    expect(within(card).queryByTestId('pos-senti')).toBeNull()
    expect(within(card).queryByTestId('pos-regime')).toBeNull()
    expect(card.textContent).not.toMatch(/\d+%, (longs|shorts|neither)/)
    expect(within(card).getByTestId('pos-quad').querySelectorAll('[aria-current="true"]')).toHaveLength(0)
    // Funding, basis and the underlying premium (a stock perp) still show.
    expect(within(card).getByTestId('pos-funding')).toBeInTheDocument()
    expect(within(card).getByTestId('pos-basis')).toHaveTextContent('−12.4 bps')
    expect(within(card).getByTestId('pos-premium')).toHaveTextContent('+0.62%')
  })
})

describe('readPositioningReport', () => {
  it('refuses other cards and unknown regimes, so the generic card draws them', () => {
    expect(readPositioningReport({ type: 'technical_setup' })).toBeNull()
    expect(readPositioningReport(btcCoveringMessage({}, { regime: 'moon' }).card)).toBeNull()
    expect(readPositioningReport(btcCoveringMessage({}, { supports_direction: null }).card)).toBeNull()
  })

  it('unreliable data is never a direction, whatever the card says', () => {
    const r = readPositioningReport(tslaUnreliableMessage({}, { supports_direction: { long: 'supports', short: 'opposes' } }).card)!
    expect(r.supports).toEqual({ long: 'neutral', short: 'neutral' })
  })

  it('reads evidence as a string with an as_of tail or as {text, as_of}', () => {
    expect(readEvidence('Funding z 2.4 (as_of 2026-10-30T10:00Z)')).toEqual({ text: 'Funding z 2.4', asOf: '2026-10-30T10:00Z' })
    expect(readEvidence({ text: 'OI +9.2%', as_of: '2026-10-28T11:00:00Z' })).toEqual({ text: 'OI +9.2%', asOf: '2026-10-28T11:00:00Z' })
    expect(readEvidence('No time here')).toEqual({ text: 'No time here', asOf: null })
    expect(readEvidence('')).toBeNull()
  })
})
