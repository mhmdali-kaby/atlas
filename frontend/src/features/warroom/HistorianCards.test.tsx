import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../i18n'
import type { Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { changedVerdictMessage, noDataVerdictMessage, regimeTableMessage, verdictMessage } from './fixtures/historian'
import { Message } from './Message'

/* ATL-96: the Historian's research_report (HistoryVerdict) and regime_table cards (design v7 §5–§7). */

function draw(message: WarroomMessage, lang: Lang = 'en') {
  void i18n.changeLanguage(lang)
  const noop = () => {}
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Message message={message} prev={undefined} parent={undefined} lang={lang} active={false} tabbable flash={false} onJump={noop} onReply={noop} onMenuChange={noop} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('HistoryVerdict card', () => {
  it('shows the question, n, every horizon row, the verdict pill and data_as_of in Baghdad time', () => {
    draw(verdictMessage())
    const card = screen.getByTestId('hv-card')
    expect(within(card).getByTestId('hv-question')).toHaveTextContent('BTC after a 3-day 10% drop')
    expect(within(card).getByTestId('hv-n')).toHaveTextContent('12 cases')
    // Weak evidence under 20 cases.
    expect(within(card).getByTestId('hv-weak')).toHaveTextContent('Weak evidence')

    // Every horizon, sorted by length, each with its median, middle 80% and % rose.
    const horizons = within(card).getAllByTestId('hv-horizon')
    expect(horizons.map((h) => h.dataset.horizon)).toEqual(['1h', '4h', '24h', '72h'])
    const day = horizons[2]!
    expect(within(day).getByRole('rowheader', { name: '24 hours' })).toBeInTheDocument()
    expect(day).toHaveTextContent('+1.20%')
    expect(day).toHaveTextContent('−3.10% to +5.40%')
    expect(day).toHaveTextContent('62%')
    expect(day).toHaveTextContent('Base rate')
    expect(day).toHaveTextContent('+0.20%')
    // 72h has no base rate.
    expect(horizons[3]).toHaveTextContent('No base rate')

    // The verdict pill: icon + words, with the screen-reader prefix.
    const pill = within(card).getByTestId('hv-verdict')
    expect(pill).toHaveTextContent('History’s verdict: Weakly supports')
    expect(pill.querySelector('svg')).not.toBeNull()
    expect(within(card).getByTestId('hv-corr')).toHaveTextContent('Capped from Supports: 12 cases')

    // 04:00Z is 07:00 in Baghdad.
    const asOf = within(card).getByTestId('hv-asof')
    expect(asOf).toHaveAttribute('datetime', '2026-10-30T04:00:00Z')
    expect(asOf).toHaveTextContent(/Data as of (30 Oct, )?07:00/)
    expect(within(card).getByTestId('hv-regime')).toHaveTextContent('Trending down, 71% likely, for 3 days')
  })

  it('shows no weak-evidence badge at 20 cases or more', () => {
    draw(verdictMessage({}, { n: 41, verdict: 'supports', meta: { unmatched_numbers: [], checked: 4, verdict_corrections: [] } }))
    expect(screen.getByTestId('hv-n')).toHaveTextContent('41 cases')
    expect(screen.queryByTestId('hv-weak')).toBeNull()
    expect(screen.queryByTestId('hv-corr')).toBeNull()
    expect(screen.getByTestId('hv-verdict')).toHaveAttribute('data-verdict', 'supports')
  })

  it('says "Changed from Contradicts" for a small-sample change that is not a cap (ATL-99), with stop, overfit and notes', () => {
    draw(changedVerdictMessage())
    expect(screen.getByTestId('hv-corr')).toHaveTextContent('Changed from Contradicts: 5 cases')
    expect(screen.getByTestId('hv-verdict')).toHaveTextContent('Doesn’t support')
    expect(screen.getByTestId('hv-stop')).toHaveTextContent('1.1 ATR from entry. The median 4-hour range is 1.5 ATR.Inside normal noise')
    expect(screen.getByTestId('hv-overfit')).toHaveTextContent('Probably overfit')
    expect(within(screen.getByTestId('hv-notes')).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByTestId('hv-regime')).toHaveTextContent('Ranging, Rule-based, for 10 hours')
  })

  it('renders "No matching cases" in place of "0 cases" and Weak evidence on a no_data card', () => {
    draw(noDataVerdictMessage())
    expect(screen.getByTestId('hv-n')).toHaveTextContent('No matching cases')
    expect(screen.queryByTestId('hv-weak')).toBeNull()
    expect(screen.queryByTestId('hv-dist')).toBeNull()
  })

  it('renders in Arabic with Arabic plurals and no English labels', () => {
    draw(verdictMessage(), 'ar')
    const card = screen.getByTestId('hv-card')
    expect(within(card).getByTestId('hv-n')).toHaveTextContent('12 حالة')
    expect(within(card).getByTestId('hv-verdict')).toHaveTextContent('يدعم بشكل ضعيف')
    expect(within(card).getByTestId('hv-weak')).toHaveTextContent('دليل ضعيف')
    expect(card).toHaveTextContent('−3.10% إلى +5.40%')
    expect(card).not.toHaveTextContent(/Base rate|These cases|Weak evidence|Data as of/)
  })

  it('falls back to the generic card for a research_report without a history_verdict card', () => {
    draw(verdictMessage({ card: { type: 'something_else' } }))
    expect(screen.queryByTestId('hv-card')).toBeNull()
    expect(screen.getByText('Research report')).toBeInTheDocument()
  })
})

describe('untraced numbers', () => {
  it('highlights "4.7%" in the body with an accessible description', () => {
    draw(verdictMessage())
    const article = screen.getByRole('article')
    const marks = within(article).getAllByTestId('unv')
    const body = marks.find((m) => m.textContent === '4.7%')
    expect(body).toBeDefined()
    expect(body!.tagName).toBe('MARK')
    expect(body).toHaveAccessibleDescription('Not found in any tool output')
    expect(body).toHaveAttribute('title', 'Atlas didn’t find this number in any tool output from this answer.')
    // The card's key_stat figure is marked by path, and the footer counts both.
    const key = within(screen.getByTestId('hv-key')).getByTestId('unv')
    expect(key).toHaveTextContent('+7.7%')
    expect(key).toHaveAccessibleDescription('Not found in any tool output')
    expect(screen.getByTestId('hv-unv-note')).toHaveTextContent('2 numbers in this answer aren’t in the tools’ data.')
    // Traced figures are not marked.
    expect(marks.map((m) => m.textContent)).not.toContain('+1.2%')
  })

  it('marks a card statistic by its path', () => {
    const m = verdictMessage({
      unmatched_numbers: [{ raw: '0.054', value: 0.054, kind: 'number', location: 'card', span: null, path: 'distribution.24h.p90' }],
    })
    draw(m)
    const day = screen.getAllByTestId('hv-horizon')[2]!
    expect(within(day).getByTestId('unv')).toHaveTextContent('+5.40%')
  })
})

describe('regime table card', () => {
  const probs = () => screen.getAllByTestId('rt-row').map((r) => r.dataset.inst)

  it('shows ten of 60 rows in volume order, then all of them', () => {
    draw(regimeTableMessage())
    expect(screen.getByTestId('rt-card')).toHaveTextContent('60 instruments, by 24-hour volume')
    expect(screen.getAllByTestId('rt-row')).toHaveLength(10)
    expect(probs()[0]).toBe('BTC-USDT-SWAP')
    fireEvent.click(screen.getByTestId('rt-more'))
    expect(screen.getAllByTestId('rt-row')).toHaveLength(60)
    expect(screen.getByTestId('rt-more')).toHaveAttribute('aria-expanded', 'true')
  })

  it('sorts by probability descending on the first click, nulls last, and reverses on the second', () => {
    const message = regimeTableMessage()
    const rows = (message.card!.rows as Array<{ inst_id: string; probability: number | null }>).map((r, i) => ({ ...r, rank: i + 1 }))
    draw(message)
    fireEvent.click(screen.getByTestId('rt-more'))
    fireEvent.click(screen.getByTestId('rt-sort-prob'))
    const header = screen.getByTestId('rt-sort-prob').closest('th')!
    expect(header).toHaveAttribute('aria-sort', 'descending')

    const valued = rows.filter((r) => r.probability !== null)
    const expected = [
      ...[...valued].sort((a, b) => b.probability! - a.probability! || a.rank - b.rank),
      ...rows.filter((r) => r.probability === null),
    ].map((r) => r.inst_id)
    expect(probs()).toEqual(expected)

    fireEvent.click(screen.getByTestId('rt-sort-prob'))
    expect(header).toHaveAttribute('aria-sort', 'ascending')
    const asc = probs()
    expect(asc[0]).toBe([...valued].sort((a, b) => a.probability! - b.probability! || a.rank - b.rank)[0]!.inst_id)
    expect(asc.slice(-rows.filter((r) => r.probability === null).length)).toEqual(rows.filter((r) => r.probability === null).map((r) => r.inst_id))
  })

  it('filters by regime with pressed chips', () => {
    draw(regimeTableMessage())
    fireEvent.click(screen.getByTestId('rt-chip-range'))
    expect(screen.getByTestId('rt-chip-range')).toHaveAttribute('aria-pressed', 'true')
    const shown = screen.getAllByTestId('rt-row')
    expect(shown.length).toBeGreaterThan(0)
    for (const r of shown) expect(r).toHaveTextContent('Ranging')
  })
})
