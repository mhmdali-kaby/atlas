import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { daRerateMessage, daReviewMessage, PROPOSAL_MSG_V1, riskCheckMessage, voteSummaryMessage } from '../fixtures/debate'
import { Message } from '../Message'
import { readDevilsReview, readRiskCheck, readVoteSummary } from './debate'

/* ATL-165: the Devil's Advocate review, vote_summary and risk_check cards (design v11 Phase 2 §6). */

function draw(message: WarroomMessage, opts: { lang?: Lang; parent?: WarroomMessage; onJump?: (id: string) => void } = {}) {
  const lang = opts.lang ?? 'en'
  void i18n.changeLanguage(lang)
  const noop = () => {}
  const router = createMemoryRouter([
    {
      path: '/',
      element: (
        <QueryClientProvider client={new QueryClient()}>
          <Message message={message} prev={undefined} parent={opts.parent} lang={lang} active={false} tabbable flash={false} onJump={opts.onJump ?? noop} onReply={noop} onMenuChange={noop} />
        </QueryClientProvider>
      ),
    },
  ])
  return render(<RouterProvider router={router} />)
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe("Devil's Advocate review card", () => {
  it('rating 4: the strongest objection comes first, then the rating, and all 10 checklist rows in order', () => {
    draw(daReviewMessage())
    const card = screen.getByTestId('db-review')
    const objection = within(card).getByTestId('db-objection')
    expect(objection).toHaveTextContent('Strongest objection')
    expect(objection).toHaveTextContent('SOL’s 30-day correlation with it is 0.82')

    // Objection before the rating, the rating before the checklist (document order).
    const rating = within(card).getByTestId('db-rating')
    const list = within(card).getByTestId('db-checklist')
    expect(objection.compareDocumentPosition(rating) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(rating.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(rating).toHaveAccessibleName('Risk rating 4 of 5: two or more serious problems')
    expect(rating).toHaveTextContent('4 of 5: two or more serious problems')
    expect(card.querySelectorAll('.db-pip.is-on')).toHaveLength(4)

    const rows = within(card).getAllByTestId('db-ci')
    expect(rows).toHaveLength(10)
    expect(rows.map((r) => r.dataset.item)).toEqual(['event_risk', 'crowding', 'history', 'stop_quality', 'chasing', 'correlation', 'liquidity', 'priced_in', 'data_quality', 'overconfidence'])
    expect(rows[0]).toHaveAttribute('data-status', 'serious')
    expect(rows[0]).toHaveTextContent('Event riskSerious US CPI at 15:30')
    expect(rows[1]).toHaveTextContent('CrowdingOK 58% long')
    expect(rows[5]).toHaveTextContent('CorrelationSerious BTC long open, 30-day correlation 0.82')
    expect(rows[7]).toHaveAttribute('data-status', 'minor')
    // A bare "OK" detail is not repeated after the OK word.
    expect(rows[8]).toHaveTextContent(/^Data qualityOK$/)
    // Every row's state is a word, not only a colour; each has an icon.
    for (const r of rows) expect(r.querySelector('.db-ci-st')?.textContent).toMatch(/^(OK|Serious|Minor)$/)

    expect(within(card).getByTestId('db-fix')).toHaveTextContent('What would change my view: Cut the size to 3% of equity')
    expect(within(card).getByTestId('db-vote')).toHaveAccessibleName('Vote: Change, 70% confident')
  })

  it('the head links to the proposal it reviews', () => {
    const onJump = vi.fn()
    draw(daReviewMessage(), { onJump })
    const ref = screen.getByTestId('db-ref')
    expect(ref).toHaveTextContent('Proposal, version 1')
    ref.click()
    expect(onJump).toHaveBeenCalledWith(PROPOSAL_MSG_V1)
  })

  it('the re-rate is compact: the new rating, "Was 4 of 5" and the items now OK', () => {
    draw(daRerateMessage(), { parent: daReviewMessage() })
    const card = screen.getByTestId('db-review')
    expect(card).toHaveAttribute('data-round', '2')
    expect(within(card).getByTestId('db-rating')).toHaveTextContent('2 of 5: minor problems only')
    expect(within(card).getByTestId('db-was')).toHaveTextContent('Was 4 of 5')
    expect(within(card).getAllByTestId('db-resolved').map((x) => x.textContent)).toEqual(['Event risk now OK', 'Correlation now OK'])
    expect(within(card).queryByTestId('db-checklist')).toBeNull()
  })

  it('Arabic: the words are translated', () => {
    draw(daReviewMessage(), { lang: 'ar' })
    const card = screen.getByTestId('db-review')
    expect(within(card).getByTestId('db-objection')).toHaveTextContent('أقوى اعتراض')
    expect(within(card).getByTestId('db-rating')).toHaveTextContent('4 من 5: مشكلتان جديتان أو أكثر')
  })

  it('a card without a rating or a vote falls back to the generic card', () => {
    expect(readDevilsReview({ type: 'da_review', proposal_id: 'x', vote: 'take' })).toBeNull()
    expect(readDevilsReview({ type: 'da_review', proposal_id: 'x', risk_rating: 3 })).toBeNull()
  })
})

describe('Vote summary card', () => {
  it('a missing voter reads "Skip (no vote)" at 0.5, and the total is shown against the 0.6 threshold', () => {
    draw(voteSummaryMessage(true))
    const card = screen.getByTestId('db-votes')
    const voters = within(card).getAllByTestId('db-voter')
    expect(voters.map((v) => v.dataset.agent)).toEqual(['technical_analyst', 'derivatives_sentiment', 'news_intelligence', 'historian', 'devils_advocate'])

    const news = voters[2]!
    expect(news).toHaveClass('is-missing')
    expect(within(news).getByTestId('db-voter-vote')).toHaveTextContent('Skip (no vote)')
    expect(news).toHaveTextContent('0.50 × 1.00')
    expect(news).toHaveAccessibleName('News Intelligence: Skip (no vote), weight 0.50, adds 0.00')
    expect(within(voters[3]!).getByTestId('db-voter-vote')).toHaveTextContent('Change, fix applied')
    expect(within(voters[0]!).getByTestId('db-voter-vote')).toHaveTextContent('Take')

    expect(within(card).getByTestId('db-vt-result')).toHaveTextContent('Passed')
    expect(within(card).getByTestId('db-vt-needs')).toHaveTextContent('Needs 0.60')
    expect(card.querySelector('.db-vt-score')).toHaveTextContent('0.76')
    expect(within(card).getByTestId('db-vt-math')).toHaveTextContent('2.69 ÷ 3.55 = 0.76')
    expect(within(card).getByTestId('db-gate')).toHaveTextContent('Devil’s Advocate rated it 2 of 5, within the limit of 4')
  })

  it('everyone voted: 3.23 ÷ 3.59 = 0.90', () => {
    draw(voteSummaryMessage(false))
    const card = screen.getByTestId('db-votes')
    expect(within(card).getByTestId('db-vt-math')).toHaveTextContent('3.23 ÷ 3.59 = 0.90')
    expect(within(card).queryByText('Skip (no vote)')).toBeNull()
  })

  it('a failed vote and an over-limit rating read as words, not only colour', () => {
    const m = voteSummaryMessage(false)
    m.card = { ...m.card, result: 'rejected', da_rating: 5, reasons: ['da_rating_too_high'] }
    draw(m)
    const card = screen.getByTestId('db-votes')
    expect(within(card).getByTestId('db-vt-result')).toHaveTextContent('Didn’t pass')
    expect(within(card).getByTestId('db-gate')).toHaveTextContent('rated it 5 of 5, over the limit of 4')
    expect(readVoteSummary({ ...m.card, da_rating: null })?.daRating).toBeNull()
  })
})

describe('Risk check card', () => {
  it('rejected at check 6: checks 7-9 are "Not run", and the card names the reason', () => {
    draw(riskCheckMessage('rejected'))
    const card = screen.getByTestId('db-risk')
    expect(within(card).getByTestId('db-rk-result')).toHaveTextContent('Rejected')
    const stopped = within(card).getByTestId('db-rk-stopped')
    expect(stopped).toHaveTextContent('Stopped at check 6')
    expect(stopped).toHaveTextContent('Today’s loss is 4.1% of equity, over the 4% daily limit.')
    expect(stopped).toHaveTextContent('No order goes to OKX.')

    const steps = within(card).getAllByTestId('db-rk-step')
    expect(steps).toHaveLength(9)
    expect(steps.map((s) => s.dataset.state)).toEqual(['pass', 'pass', 'pass', 'pass', 'pass', 'fail', 'not_run', 'not_run', 'not_run'])
    expect(steps[5]).toHaveTextContent('Loss limits')
    expect(within(steps[5]!).getByTestId('db-rk-step-state')).toHaveTextContent('Failed')
    for (const s of steps.slice(6)) {
      expect(within(s).getByTestId('db-rk-step-state')).toHaveTextContent('Not run')
      expect(s).toHaveTextContent('Not run: the checks stopped at 6.')
    }
    expect(within(card).queryByTestId('db-rk-final')).toBeNull()
    expect(within(card).queryByTestId('db-rk-binding')).toBeNull()
    expect(within(card).getByTestId('db-rk-settings')).toHaveTextContent('Settings version 6')
  })

  it('resized: final size, notional, leverage, margin, the binding cap and "from" the asked size', () => {
    draw(riskCheckMessage('resized'))
    const card = screen.getByTestId('db-risk')
    expect(within(card).getByTestId('db-rk-result')).toHaveTextContent('Resized')
    expect(card).toHaveTextContent('from $4,000.00')
    const final = within(card).getByTestId('db-rk-final')
    expect(final).toHaveTextContent('12.02 contracts')
    expect(final).toHaveTextContent('$2,997.79')
    expect(final).toHaveTextContent('3×')
    expect(final).toHaveTextContent('$999.27')
    expect(within(card).getByTestId('db-rk-binding')).toHaveTextContent('Max per trade $3,000.00 set the sizeMoney limit')
    const steps = within(card).getAllByTestId('db-rk-step')
    expect(steps[3]).toHaveAttribute('data-state', 'resize')
    expect(steps[8]).toHaveAttribute('data-state', 'awaiting_owner')
    expect(within(steps[8]!).getByTestId('db-rk-step-state')).toHaveTextContent('Awaiting you')
  })

  it('approved: the Strategist’s own size set it', () => {
    draw(riskCheckMessage('approved'))
    const card = screen.getByTestId('db-risk')
    expect(within(card).getByTestId('db-rk-result')).toHaveTextContent('Approved')
    expect(within(card).getAllByTestId('db-rk-step')[3]).toHaveAttribute('data-state', 'pass')
    expect(within(card).getByTestId('db-rk-binding')).toHaveTextContent('The Strategist’s suggested size $2,997.79 set the size')
  })

  it('reader: the failed check comes from the checks, not the name in failed_check', () => {
    const rc = readRiskCheck(riskCheckMessage('rejected').card)
    expect(rc?.failedCheck).toBe(6)
    expect(rc?.contracts).toBeNull()
  })

  it('autonomy 3, sent: step 9 shows "Autonomy 3" and the line is built from the check’s own values, not a hard-coded figure', () => {
    draw(riskCheckMessage('approved', { autonomy_level: 3, notional_usd: '2997.79', ask_above_usd: '3000', decision: 'send_now' }))
    const card = screen.getByTestId('db-risk')
    const steps = within(card).getAllByTestId('db-rk-step')
    const step9 = steps[8]!
    expect(step9).toHaveAttribute('data-state', 'send_now')
    expect(within(step9).getByTestId('db-rk-step-state')).toHaveTextContent('Sent without asking')
    expect(step9).toHaveTextContent('Autonomy 3')
    const reason = within(step9).getByTestId('db-rk-autonomy-reason')
    expect(reason).toHaveTextContent('$2,997.79')
    expect(reason).toHaveTextContent('$3,000.00')
    expect(reason).toHaveTextContent('is within your')
  })

  it('autonomy 3, above the ask-above amount: step 9 waits for the owner with both amounts', () => {
    draw(riskCheckMessage('approved', { autonomy_level: 3, notional_usd: '2997.79', ask_above_usd: '1000', note: 'above_ask_threshold', decision: 'awaiting_owner' }))
    const card = screen.getByTestId('db-risk')
    const step9 = within(card).getAllByTestId('db-rk-step')[8]!
    expect(step9).toHaveAttribute('data-state', 'awaiting_owner')
    const reason = within(step9).getByTestId('db-rk-autonomy-reason')
    expect(reason).toHaveTextContent('$2,997.79')
    expect(reason).toHaveTextContent('$1,000.00')
    expect(reason).toHaveTextContent('is above your')
  })

  it('autonomy 4: step 9 is sent without asking, with the level-4 line', () => {
    draw(riskCheckMessage('approved', { autonomy_level: 4, max_enabled_level: 4, notional_usd: '2997.79', ask_above_usd: '3000', decision: 'send_now' }))
    const card = screen.getByTestId('db-risk')
    const step9 = within(card).getAllByTestId('db-rk-step')[8]!
    expect(step9).toHaveAttribute('data-state', 'send_now')
    expect(step9).toHaveTextContent('Autonomy 4')
    expect(within(step9).getByTestId('db-rk-autonomy-reason')).toHaveTextContent('Every trade the Risk Engine approves goes straight to OKX.')
  })

  it('autonomy 2 (unchanged): step 9 still reads "Awaiting you" with the plain autonomy-2 text', () => {
    draw(riskCheckMessage('resized'))
    const card = screen.getByTestId('db-risk')
    const step9 = within(card).getAllByTestId('db-rk-step')[8]!
    expect(step9).toHaveAttribute('data-state', 'awaiting_owner')
    expect(within(step9).getByTestId('db-rk-step-state')).toHaveTextContent('Awaiting you')
    expect(step9).toHaveTextContent('The owner approves each trade.')
  })
})

describe('spacing inside flex chips', () => {
  it('"Was 4 of 5" and "Proposal, version 2" keep their spaces (inline-flex drops bare text-node spaces)', () => {
    draw(daRerateMessage(), { parent: daReviewMessage() })
    const was = screen.getByTestId('db-was')
    expect(was.children).toHaveLength(1)
    expect(was.firstElementChild?.tagName).toBe('SPAN')
  })
})
