import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { JOURNAL_ID, pendingPostMortemMessage, postMortemMessage, quietWeekMessage, weeklyReviewMessage } from '../fixtures/learning'
import { Message } from '../Message'
import arJournal from '../../../i18n/locales/ar/journal.json'
import { closeLabel } from '../../journal/trade'
import { readPostMortem, readWeeklyReview } from './learning'

/* ATL-203: the post_mortem and weekly_review cards in the War Room (design v16 §9). */

function JournalProbe() {
  const loc = useLocation()
  return <p data-testid="journal-page">{`${loc.pathname}${loc.search}`}</p>
}

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
    { path: '/journal', element: <JournalProbe /> },
  ])
  return { router, ...render(<RouterProvider router={router} />) }
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('PostMortemCard', () => {
  it('renders the outcome with its icon and sign, R, USD, the close reason and the lesson', () => {
    draw(postMortemMessage())
    const card = screen.getByTestId('pmo-card')
    const outcome = within(card).getByTestId('pmo-outcome')
    expect(outcome).toHaveTextContent('Lost')
    expect(outcome).toHaveClass('is-loss')
    expect(outcome.querySelector('svg')).not.toBeNull()
    expect(within(card).getByTestId('pmo-close')).toHaveTextContent(/^Stopped out$/)
    const r = within(card).getByTestId('pmo-r')
    expect(r).toHaveTextContent('−0.97R')
    expect(r.querySelector('svg')).not.toBeNull()
    expect(within(card).getByTestId('pmo-usd')).toHaveTextContent('−$20.55')
    expect(within(card).getByTestId('pmo-lesson')).toHaveTextContent('give stock-perp stops at least 1.2 ATR(1H)')
    expect(within(card).queryByTestId('pmo-pending')).toBeNull()
    expect(within(card).getByTestId('pmo-held')).toHaveTextContent('Held')
    expect(within(card).getByTestId('pmo-caption')).toHaveTextContent('Best +0.58R, worst −1.06R. It closed at −0.97R.')
    const rows = within(card).getByTestId('pmo-table').querySelectorAll('tbody tr')
    expect([...rows].map((x) => x.getAttribute('data-row'))).toEqual(['result', 'tp1', 'time'])
    expect(rows[0]).toHaveTextContent('+1.9R expected')
    expect(rows[1]).toHaveTextContent('55% likely')
    expect(rows[1]).toHaveTextContent('No')
    expect(within(card).getByTestId('pmo-tags')).toHaveTextContent('Breakout')
    expect(within(card).getByTestId('pmo-tags')).not.toHaveTextContent('loss')
  })

  it('draws the closed ladder: the travel band and the exit diamond, left to right', () => {
    draw(postMortemMessage())
    const ladder = within(screen.getByTestId('pmo-card')).getByTestId('ladder-compact')
    expect(ladder).toHaveAttribute('dir', 'ltr')
    expect(within(ladder).getByTestId('ld-travel')).toBeInTheDocument()
    expect(within(ladder).getByTestId('ld-exit')).toBeInTheDocument()
    expect(ladder.getAttribute('aria-label')).toContain('The price went from −1.1R at worst to +0.6R at best, and the trade closed at −1.0R.')
  })

  it('links to the Journal with that entry open', () => {
    const { router } = draw(postMortemMessage())
    const link = screen.getByTestId('pmo-journal')
    expect(link).toHaveAttribute('href', `/journal?entry=${JOURNAL_ID}`)
    fireEvent.click(link)
    expect(router.state.location.pathname).toBe('/journal')
    expect(new URLSearchParams(router.state.location.search).get('entry')).toBe(JOURNAL_ID)
    expect(screen.getByTestId('journal-page')).toHaveTextContent(`/journal?entry=${JOURNAL_ID}`)
  })

  it('a lesson_pending post-mortem says its lesson is still being written', () => {
    draw(pendingPostMortemMessage())
    const card = screen.getByTestId('pmo-card')
    expect(within(card).getByTestId('pmo-pending')).toHaveTextContent('The lesson is still being written')
    expect(within(card).getByTestId('pmo-pending')).toHaveTextContent('The figures are final.')
    expect(within(card).queryByTestId('pmo-lesson')).toBeNull()
    // Unknown regime and catalyst are not drawn as tags while the lesson is pending.
    expect(within(card).getByTestId('pmo-tags').querySelectorAll('li')).toHaveLength(2)
    expect(within(card).getByTestId('pmo-r')).toHaveTextContent('−0.97R')
  })

  it('reads the same close labels as the Journal, verbatim, in Arabic too', () => {
    draw(postMortemMessage(), 'ar')
    expect(screen.getByTestId('pmo-close')).toHaveTextContent(/^ضُرب الوقف$/)
    expect(screen.getByTestId('pmo-close').textContent).toBe(arJournal.rule.stop)
    expect(screen.getByTestId('pmo-outcome')).toHaveTextContent('خاسرة')
    expect(screen.getByTestId('pmo-journal')).toHaveTextContent('افتحها في السجل')
  })

  it('maps take_profit to the furthest target reached, and other reasons to their labels', () => {
    const tp = readPostMortem({ ...postMortemMessage().card!, outcome: 'win', actual: { ...(postMortemMessage().card!.actual as object), close_reason: 'take_profit', mfe_r: '2.4', realised_r: '2.10' } })!
    expect(closeLabel(tp.trade)).toBe('tp2')
    const manual = readPostMortem({ ...postMortemMessage().card!, actual: { close_reason: 'manual', realised_r: '0.02' } })!
    expect(closeLabel(manual.trade)).toBe('owner')
    expect(manual.outcome).toBe('loss')
    expect(readPostMortem({ type: 'post_mortem' })).toBeNull()
  })
})

describe('WeeklyReviewCard', () => {
  it('renders the setup table rows, the total and exactly one suggested change', () => {
    draw(weeklyReviewMessage())
    const card = screen.getByTestId('wk-card')
    expect(within(card).getByTestId('wk-span')).toHaveTextContent('19 Oct to 25 Oct')
    expect(within(card).getByTestId('wk-verdict')).toHaveTextContent('breakout setups made 0.50R over 2 trades')
    const rows = within(card).getAllByTestId('wk-row')
    expect(rows.map((x) => x.getAttribute('data-key'))).toEqual(['breakout', 'pullback_to_support'])
    expect(rows[0]).toHaveTextContent('Breakout')
    expect(rows[0]).toHaveTextContent('+0.50R')
    expect(rows[0]).toHaveTextContent('+$9.45')
    expect(rows[1]).toHaveTextContent('Pullback to support')
    const total = within(card).getByTestId('wk-total')
    expect(total).toHaveTextContent('All trades')
    expect(total).toHaveTextContent('+2.75R')
    expect(total).toHaveTextContent('+$54.95')
    expect(within(card).getAllByTestId('wk-change')).toHaveLength(1)
    expect(within(card).getAllByTestId('wk-change-text')).toHaveLength(1)
    expect(within(card).getByTestId('wk-change-text')).toHaveTextContent('Raise min R for breakout setups to 2.0')
    expect(within(card).getByTestId('wk-paper')).toHaveTextContent('The 2 ideas the team skipped would have lost 0.6R on paper, so skipping them was right.')
  })

  it('the By control switches the table to markets and agents', () => {
    draw(weeklyReviewMessage())
    const card = screen.getByTestId('wk-card')
    fireEvent.click(within(card).getByRole('radio', { name: 'Market' }))
    expect(within(card).getAllByTestId('wk-row').map((x) => x.textContent)).toEqual([
      expect.stringContaining('Commodities'),
      expect.stringContaining('Crypto'),
      expect.stringContaining('Stocks'),
    ])
    expect(within(card).getAllByTestId('wk-row')[2]).toHaveTextContent('—')
    fireEvent.click(within(card).getByRole('radio', { name: 'Agent' }))
    expect(within(card).getAllByTestId('wk-row')[1]).toHaveTextContent('Strategist')
  })

  it('links the change to the setting it names when the card says where', () => {
    draw(weeklyReviewMessage({ suggestion: { change: 'Raise min R to 2.0', reason: 'n is 2', where: 'risk' } }))
    expect(screen.getByTestId('wk-change-link')).toHaveAttribute('href', '/settings/risk')
    expect(screen.getByTestId('wk-change-link')).toHaveTextContent('Open Settings → Risk profile')
  })

  it('omits the paper line when ATL-196’s table is absent', () => {
    draw(weeklyReviewMessage({ paper_trades: undefined }))
    expect(screen.queryByTestId('wk-paper')).toBeNull()
  })

  it('a degraded run keeps the figures with no verdict and no suggested change', () => {
    draw(weeklyReviewMessage({ status: 'degraded', verdict: null, suggestion: null }))
    expect(screen.getByTestId('wk-noverdict')).toBeInTheDocument()
    expect(screen.queryByTestId('wk-change')).toBeNull()
    expect(screen.getAllByTestId('wk-row')).toHaveLength(2)
  })

  it('the no-trades variant renders its short text and no table', () => {
    draw(quietWeekMessage())
    const card = screen.getByTestId('wk-card')
    expect(within(card).getByTestId('wk-quiet')).toHaveTextContent('No closed trades this week')
    expect(within(card).getByTestId('wk-quiet')).toHaveTextContent('Nothing closed between 12 Oct and 18 Oct, so there is nothing to review. Atlas wrote this without asking the Historian.')
    expect(within(card).queryByTestId('wk-table')).toBeNull()
    expect(within(card).queryByTestId('wk-change')).toBeNull()
  })

  it('is told apart from other research reports by card.type', () => {
    expect(readWeeklyReview(weeklyReviewMessage().card)).not.toBeNull()
    expect(readWeeklyReview({ type: 'history_verdict' })).toBeNull()
  })
})
