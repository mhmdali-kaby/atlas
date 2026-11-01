import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { BREAKEVEN_PROPOSAL_ID, dailyReportBreachMessage, dailyReportMessage, positionUpdateMessage } from '../fixtures/pm'
import { Message } from '../Message'
import { readDailyReport, readPositionUpdate } from './pm'

/* ATL-178: the Portfolio Manager's daily_report and position_update cards (brief §7.8). */

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

describe('DailyReportCard', () => {
  it('a three-trade day renders three table rows, plus best and worst', () => {
    draw(dailyReportMessage())
    const card = screen.getByTestId('dr-card')
    const rows = within(screen.getByTestId('dr-trades')).getAllByTestId('dr-trade')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('ETH-USDT-SWAP')
    expect(rows[0]).toHaveTextContent('+1.4R')
    expect(rows[0]).toHaveTextContent('+$1,820.00')
    expect(rows[0]).toHaveTextContent('target')
    expect(rows[1]).toHaveTextContent('−1.0R')
    expect(rows[1]).toHaveTextContent('stop')
    expect(rows[2]).toHaveTextContent('Short')
    expect(rows[2]).toHaveTextContent('time stop')
    expect(within(card).getByTestId('dr-best')).toHaveTextContent('ETH-USDT-SWAP')
    expect(within(card).getByTestId('dr-best')).toHaveTextContent('TP2 hit after the ETF-flow headline')
    expect(within(card).getByTestId('dr-worst')).toHaveTextContent('NVDA-USDT-SWAP')
    expect(within(card).getByTestId('dr-worst')).toHaveTextContent('−1.0R')
  })

  it('shows equity start → end with the change, the four PnL parts, limits and tomorrow', () => {
    draw(dailyReportMessage())
    const card = screen.getByTestId('dr-card')
    const eq = within(card).getByTestId('dr-equity')
    expect(eq).toHaveTextContent('$5,061,204.10')
    expect(eq).toHaveTextContent('$5,065,230.16')
    expect(eq).toHaveTextContent('+$4,026.06')
    expect(eq).toHaveTextContent('+0.08%')
    expect(card).toHaveTextContent('Realised')
    expect(card).toHaveTextContent('−$186.22')
    expect(card).toHaveTextContent('−$54.62')
    const limits = within(card).getAllByTestId('dr-limit').map((x) => x.getAttribute('data-limit'))
    expect(limits).toEqual(['daily_loss', 'open_positions', 'exposure_peak'])
    expect(within(card).getAllByTestId('dr-limit')[0]).toHaveTextContent('0.4% of 4%')
    expect(within(card).getByTestId('dr-no-breach')).toHaveTextContent('No limit was breached.')
    expect(within(card).queryByTestId('dr-breach')).toBeNull()
    expect(within(card).getByTestId('dr-carried')).toHaveTextContent('BTC-USDT-SWAP')
    expect(within(card).getByTestId('dr-events')).toHaveTextContent('GDP Growth Rate QoQ Adv')
  })

  it('one breach renders in the alert style with an icon, a title and a sentence, not colour only', () => {
    draw(dailyReportBreachMessage())
    const card = screen.getByTestId('dr-card')
    const breach = within(card).getByRole('alert')
    expect(breach).toHaveAttribute('data-testid', 'dr-breach')
    expect(breach).toHaveClass('an-breach')
    expect(breach.querySelector('svg')).not.toBeNull()
    expect(breach).toHaveTextContent('A limit was breached')
    expect(within(breach).getAllByTestId('dr-breach-row')).toHaveLength(1)
    expect(within(breach).getByTestId('dr-breach-row')).toHaveTextContent('Open positions reached 7 of 6.')
    expect(within(card).queryByTestId('dr-no-breach')).toBeNull()
    // the open-positions bar goes past 70%: warned in words and an icon as well as colour
    const pos = within(card).getAllByTestId('dr-limit').find((x) => x.getAttribute('data-limit') === 'open_positions')!
    expect(pos).toHaveClass('is-warn')
    expect(pos).toHaveTextContent('7 of 6')
    expect(pos).toHaveTextContent('117%')
  })

  it('Arabic: the breach title and sentence are Arabic', () => {
    draw(dailyReportBreachMessage(), 'ar')
    const breach = screen.getByRole('alert')
    expect(breach).toHaveTextContent('تم تجاوز أحد الحدود')
    expect(breach).toHaveTextContent('بلغت المراكز المفتوحة')
  })

  it('a no-trade day shows the empty line, no table', () => {
    draw(dailyReportMessage({ trades: [], trade_count: 0, best_trade: null, worst_trade: null }))
    const card = screen.getByTestId('dr-card')
    expect(within(card).queryByTestId('dr-trades')).toBeNull()
    expect(card).toHaveTextContent('No trades closed today.')
  })

  it('the reader refuses a card that is not a daily report', () => {
    expect(readDailyReport({ type: 'technical_setup' })).toBeNull()
    expect(readDailyReport(null)).toBeNull()
  })
})

describe('PositionUpdateCard', () => {
  it('rule breakeven renders the rule name, its proposed action and a link to its proposal', () => {
    draw(positionUpdateMessage())
    const card = screen.getByTestId('pu-card')
    const rule = within(card).getByTestId('pu-rule')
    expect(rule).toHaveAttribute('data-rule', 'breakeven')
    expect(within(rule).getByTestId('pu-rule-name')).toHaveTextContent('Rule: breakeven')
    expect(rule).toHaveTextContent('Move the stop to 4,117.6')
    const link = within(rule).getByTestId('pu-proposal-link')
    expect(link).toHaveAttribute('href', `/warroom?proposal=${BREAKEVEN_PROPOSAL_ID}`)
    expect(link).toHaveTextContent('Proposal 5b2e7d10')
  })

  it('shows instrument, side, PnL in USD and R, the ATR distances, time in trade and funding paid', () => {
    draw(positionUpdateMessage())
    const card = screen.getByTestId('pu-card')
    expect(card).toHaveTextContent('XAU-USDT-SWAP')
    expect(within(card).getByTestId('pu-side')).toHaveTextContent('Short')
    expect(within(card).getByTestId('pu-pnl')).toHaveTextContent('+$1,660.00')
    expect(within(card).getByTestId('pu-pnl')).toHaveTextContent('+1.0R')
    expect(within(card).getByTestId('pu-dist')).toHaveTextContent('Stop 7.2 ATR away, TP1 0.1 ATR away')
    expect(within(card).getByTestId('pu-in-trade')).toHaveTextContent('in trade')
    expect(within(card).getByTestId('pu-funding')).toHaveTextContent('Funding −$12.40')
    expect(within(card).getByTestId('ladder-compact')).toBeInTheDocument()
  })

  it('no rule fired: says so instead of a rule row', () => {
    draw(positionUpdateMessage({ actions: [] }))
    const card = screen.getByTestId('pu-card')
    expect(within(card).queryByTestId('pu-rule')).toBeNull()
    expect(card).toHaveTextContent('No rule fired.')
  })

  it('Arabic: the rule name is Arabic', () => {
    draw(positionUpdateMessage(), 'ar')
    expect(screen.getByTestId('pu-rule-name')).toHaveTextContent('القاعدة: نقطة التعادل')
  })

  it('a position_update without a card (older messages) falls back to the generic card', () => {
    expect(readPositionUpdate(null)).toBeNull()
    draw({ ...positionUpdateMessage(), card: null })
    expect(screen.queryByTestId('pu-card')).toBeNull()
  })
})
