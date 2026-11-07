import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import type { Lang } from '../../../lib/format'
import type { WarroomMessage } from '../api'
import { solSetupMessage, xauNoSetupMessage } from '../fixtures/technicalSetup'
import { Message } from '../Message'
import { readTechnicalSetup } from './technicalSetup'

/* ATL-139: the Technical Analyst's TechnicalSetup card (brief §7.4, design v11 Phase 2 §7). */

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

describe('TechnicalSetup card', () => {
  it('a long breakout shows direction, setup type, entry zone, stop, three targets with R and the quality score', () => {
    draw(solSetupMessage())
    const card = screen.getByTestId('ta-card')
    expect(within(card).getByTestId('ta-headline')).toHaveTextContent('Long breakout')
    expect(within(card).getByTestId('ta-inst')).toHaveTextContent('SOL-USDT-SWAP')
    expect(within(card).getByTestId('ta-entry')).toHaveTextContent('248.60–250.20')
    expect(within(card).getByTestId('ta-stop')).toHaveTextContent('241.80')

    const targets = within(card).getAllByTestId('ta-target')
    expect(targets).toHaveLength(3)
    expect(targets[0]).toHaveTextContent('TP1258.50+1.20RPrior week high')
    expect(targets[1]).toHaveTextContent('264.60')
    expect(targets[1]).toHaveTextContent('+2.00R')
    expect(targets[2]).toHaveTextContent('272.20')
    expect(targets[2]).toHaveTextContent('+3.00R')

    // The ladder: each target labelled with its split and R; the stop with its distance.
    const ladder = within(card).getByTestId('ladder')
    const tl = within(ladder).getAllByTestId('ld-target')
    expect(tl.map((x) => x.textContent)).toEqual(['TP1258.5050% at +1.2R', 'TP2264.6030% at +2.0R', 'TP3272.2020% at +3.0R'])
    expect(within(ladder).getByTestId('ld-stop')).toHaveTextContent('Stop241.80−3.05%')
    expect(within(ladder).getByTestId('ld-entry')).toHaveTextContent('Entry248.60–250.20')
    expect(ladder).toHaveAttribute('role', 'img')
    expect(ladder.getAttribute('aria-label')).toContain('Long plan. Stop 241.80, entry 248.60–250.20')

    expect(within(card).getByTestId('ta-exp-r')).toHaveTextContent('1.80R')
    const quality = within(card).getByTestId('ta-quality')
    expect(quality).toHaveTextContent('0.72')
    expect(quality).toHaveAccessibleName('Quality 0.72 out of 1')

    // Bias row: every timeframe, each a word as well as an icon.
    const chips = within(within(card).getByTestId('ta-bias')).getAllByRole('listitem')
    expect(chips.map((c) => c.dataset.tf)).toEqual(['1D', '4H', '1H', '15m'])
    expect(chips[3]).toHaveAccessibleName('15m: Range')
    expect(chips[0]!.querySelector('svg')).not.toBeNull()

    expect(within(card).getByTestId('ta-trigger')).toHaveTextContent('1H close above 250.20')
    expect(within(card).getByTestId('ta-inval')).toHaveTextContent('A 1H close back below 244.00')
    // data_as_of in Baghdad (UTC+3) and UTC.
    expect(within(card).getByTestId('ta-asof')).toHaveTextContent(/14:00.*11:00 UTC/)
  })

  it('a short setup reads Short and keeps the ladder risk-left', () => {
    draw(
      solSetupMessage({}, {
        direction: 'short',
        setup_type: 'breakdown',
        entry_zone: ['248.60', '250.20'],
        stop: '256.00',
        targets: [{ price: '240.00', r_multiple: '1.42', reason: null, close_pct: null }],
      }),
    )
    const card = screen.getByTestId('ta-card')
    expect(within(card).getByTestId('ta-headline')).toHaveTextContent('Short breakdown')
    expect(within(within(card).getByTestId('ladder')).getByTestId('ld-target')).toHaveTextContent('at +1.4R')
  })

  it('setup_type none shows "No setup" and its condition, with no entry or stop rows', () => {
    draw(xauNoSetupMessage())
    const card = screen.getByTestId('ta-card')
    expect(card).toHaveAttribute('data-setup', 'none')
    expect(within(card).getByTestId('ta-headline')).toHaveTextContent('No setup')
    expect(within(card).getByTestId('ta-condition')).toHaveTextContent('A 1H close below 4,082.0 turns this into a breakdown short.')
    expect(within(card).queryByTestId('ta-entry')).toBeNull()
    expect(within(card).queryByTestId('ta-stop')).toBeNull()
    expect(within(card).queryByTestId('ladder')).toBeNull()
    expect(within(card).queryByTestId('ta-target')).toBeNull()
    expect(within(card).queryByText('Entry zone')).toBeNull()
    // The bias chips stay.
    expect(within(card).getByTestId('ta-bias')).toBeInTheDocument()
  })

  it('setup_type none without a no_setup_reason falls back to the trigger as the condition', () => {
    draw(xauNoSetupMessage({}, { no_setup_reason: null, trigger: 'A 1H close below 4,082.0 turns this into a breakdown short.' }))
    expect(screen.getByTestId('ta-condition')).toHaveTextContent('A 1H close below 4,082.0')
  })

  it('est_slippage_bps 14 shows the thin-liquidity notice; 6 does not', () => {
    const { unmount } = draw(solSetupMessage({}, { est_slippage_bps: '14' }))
    const thin = screen.getByTestId('ta-thin')
    expect(thin).toHaveTextContent('Too thin for full size: 14 bps estimated slippage')
    expect(thin).toHaveAttribute('role', 'note')
    expect(screen.getByTestId('ta-slip')).toHaveClass('an-warn-ink')
    unmount()

    draw(solSetupMessage({}, { est_slippage_bps: '6' }))
    expect(screen.queryByTestId('ta-thin')).toBeNull()
    expect(screen.getByTestId('ta-slip')).toHaveTextContent('6 bps at the Risk Engine’s max size')
    expect(screen.getByTestId('ta-slip')).not.toHaveClass('an-warn-ink')
  })

  it('prices print at the instrument’s tick precision', () => {
    // tick 0.1 (XAU): every price one decimal, even when the string carries more.
    draw(
      solSetupMessage({}, {
        inst_id: 'XAU-USDT-SWAP',
        entry_zone: ['4117.6', '4120.0'],
        stop: '4100.00',
        targets: [{ price: '4161.0', r_multiple: '2.00', reason: null, close_pct: null }],
        meta: { corrections: [{ rule: 'tick_rounding', tick_sz: '0.1' }] },
      }),
    )
    expect(screen.getByTestId('ta-entry')).toHaveTextContent('4,117.6–4,120.0')
    expect(screen.getByTestId('ta-stop')).toHaveTextContent('4,100.0')
  })

  it('renders Arabic words around left-to-right figures', () => {
    draw(solSetupMessage(), 'ar')
    const card = screen.getByTestId('ta-card')
    expect(within(card).getByTestId('ta-headline')).toHaveTextContent('اختراق للشراء')
    expect(within(card).getByTestId('ta-entry').querySelector('bdi')).toHaveAttribute('dir', 'ltr')
    expect(within(card).getByTestId('ladder')).toHaveAttribute('dir', 'ltr')
  })

  // ATL-191: the card's own price is from analysis time, not now, so it is labelled with that time.
  it('an hours-old card labels its price with the analysis time, never "Now"', () => {
    draw(solSetupMessage({}, { last_price: '249.95', data_as_of: '2026-10-28T11:00:00Z' }))
    const card = screen.getByTestId('ta-card')
    const ladder = within(card).getByTestId('ladder')
    const at = within(ladder).getByTestId('ld-at')
    expect(at).toHaveTextContent('At 14:00249.95')
    expect(ladder.getAttribute('aria-label')).toContain('Price at 14:00: 249.95')
    expect(within(card).queryByTestId('ld-now')).toBeNull()
    expect(within(card).queryByText('Now', { exact: false })).toBeNull()
    expect(ladder.getAttribute('aria-label')).not.toContain('Price now')
  })

  it('labels the same price in Arabic with the time, no English text in the label', () => {
    draw(solSetupMessage({}, { last_price: '249.95', data_as_of: '2026-10-28T11:00:00Z' }), 'ar')
    const card = screen.getByTestId('ta-card')
    const ladder = within(card).getByTestId('ladder')
    const at = within(ladder).getByTestId('ld-at')
    expect(at).toHaveTextContent('الساعة 14:00')
    // The label (word + time), not the R note, carries no Latin letters.
    expect(at.querySelector('.ladder-k')?.textContent).not.toMatch(/[A-Za-z]/)
    expect(within(card).queryByTestId('ld-now')).toBeNull()
    expect(within(card).queryByText('Now', { exact: false })).toBeNull()
  })
})

describe('readTechnicalSetup', () => {
  it('ignores other research_report cards and a plan with no stop', () => {
    expect(readTechnicalSetup({ type: 'history_verdict' })).toBeNull()
    expect(readTechnicalSetup(solSetupMessage({}, { stop: null }).card)).toBeNull()
  })

  it('takes tick precision from tick_decimals first', () => {
    expect(readTechnicalSetup(solSetupMessage({}, { tick_decimals: 3 }).card)?.tickDecimals).toBe(3)
    expect(readTechnicalSetup(solSetupMessage().card)?.tickDecimals).toBe(2)
    expect(readTechnicalSetup(solSetupMessage({}, { meta: {} }).card)?.tickDecimals).toBe(2)
  })
})
