import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import { AutonomyNoteCard } from './AutonomyNoteCard'
import { resolveAutonomy, type AutonomyNoteValues } from './autonomyNote'

/*
 * AutonomyNote (decision 254, ATL-212/213, design v18 §4.6; ATL-219): one explanation built only
 * from the risk check's own stored autonomy values and the proposal's kind, never from a mock
 * figure. Covers the cases the risk check card's entry-only step 9 cannot reach: a risk-reducing
 * close, the whole-position wording for a stop/target change, halted, unknown size and a level the
 * account does not have yet.
 */

function draw(values: AutonomyNoteValues, kind: Parameters<typeof AutonomyNoteCard>[0]['kind'] = 'entry') {
  return render(
    <MemoryRouter>
      <AutonomyNoteCard values={values} kind={kind} lang="en" />
    </MemoryRouter>,
  )
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('AutonomyNote', () => {
  it('level 3, sent: the lead and reason are built from the check’s own values, not a hard-coded figure', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '2997.788', askAboveUsd: '3000' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveAttribute('data-au-note', 'within')
    expect(note).toHaveTextContent('Autonomy 3 didn’t ask.')
    expect(note).toHaveTextContent('$2,997.79')
    expect(note).toHaveTextContent('$3,000.00')
    expect(note).toHaveTextContent('is within your')
    expect(screen.getByTestId('au-gauge')).toBeInTheDocument()
  })

  it('level 3, above the ask-above amount: it waits for the owner, with both amounts', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '1000', note: 'above_ask_threshold' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveAttribute('data-au-note', 'above')
    expect(note).toHaveTextContent('Autonomy 3 asks you.')
    expect(note).toHaveTextContent('$2,997.79')
    expect(note).toHaveTextContent('$1,000.00')
    expect(note).toHaveTextContent('is above your')
  })

  it('level 4: sent without asking, with the level-4 line', () => {
    draw({ autonomyLevel: 4, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '3000' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveAttribute('data-au-note', 'l4')
    expect(note).toHaveTextContent('Autonomy 4 didn’t ask.')
    expect(note).toHaveTextContent('Every trade the Risk Engine approves goes straight to OKX.')
  })

  it('a risk-reducing close at level 3: sent because closing never waits, whatever its size', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '262830', askAboveUsd: '1000', note: 'risk_reducing' }, 'close')
    const note = screen.getByTestId('au-note')
    expect(note).toHaveAttribute('data-au-note', 'closing')
    expect(note).toHaveTextContent('Autonomy 3 didn’t ask.')
    expect(note).toHaveTextContent('Closing never waits, whatever its size.')
  })

  it('a stop change above the ask-above amount compares the whole position, not a hard-coded figure', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '163848', askAboveUsd: '3000', note: 'above_ask_threshold' }, 'amend_stop')
    const note = screen.getByTestId('au-note')
    expect(note).toHaveAttribute('data-au-note', 'abovePos')
    expect(note).toHaveTextContent('The whole position')
    expect(note).toHaveTextContent('$163,848.00')
    expect(note).toHaveTextContent('$3,000.00')
  })

  it('halted: autonomy never acts past a pause or a breaker', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '3000', note: 'halted' }, 'close')
    const note = screen.getByTestId('au-note')
    expect(note).toHaveClass('is-halted')
    expect(note).toHaveTextContent('Autonomy 3 asks you.')
    expect(note).toHaveTextContent('Trading is paused or a breaker is on')
  })

  it('a size Atlas could not work out: asks the owner instead of guessing', () => {
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: null, askAboveUsd: '3000', note: 'notional_unknown' }, 'amend_stop')
    expect(screen.getByTestId('au-note')).toHaveTextContent('couldn’t work out its size in dollars')
  })

  it('a level the account does not have yet', () => {
    draw({ autonomyLevel: 4, maxEnabledLevel: 3, notionalUsd: '2997.79', askAboveUsd: '3000', note: 'level_not_enabled' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveTextContent('Atlas asks you.')
    expect(note).toHaveTextContent('Autonomy 4 isn’t available on this account yet.')
  })

  it('level 1: the trade stays a suggestion', () => {
    draw({ autonomyLevel: 1, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '3000' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveTextContent('Autonomy 1 never sends.')
    expect(note).toHaveTextContent('This trade stays a suggestion.')
  })

  it('level 2: unchanged — the owner approves every trade', () => {
    draw({ autonomyLevel: 2, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '3000' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveTextContent('Autonomy 2 asks you.')
    expect(note).toHaveTextContent('You approve every trade.')
  })

  it('Arabic: the lead and reason translate, figures stay LTR isolates', () => {
    void i18n.changeLanguage('ar')
    draw({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '2997.79', askAboveUsd: '3000' })
    const note = screen.getByTestId('au-note')
    expect(note).toHaveTextContent('لم تسألك الاستقلالية 3.')
    expect(note).toHaveTextContent('$2,997.79')
  })
})

describe('resolveAutonomy', () => {
  it('trusts the check’s own note: no "above_ask_threshold" note means it sends, whatever the two figures are', () => {
    const o = resolveAutonomy({ autonomyLevel: 3, maxEnabledLevel: 4, notionalUsd: '1000.00', askAboveUsd: '1000.00' })
    expect(o.decision).toBe('send_now')
    expect(o.key).toBe('within')
  })
})
