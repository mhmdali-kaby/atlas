import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../../../i18n'
import { Ladder } from './Ladder'

/*
 * ATL-191: the shared trade ladder (ATL-139 docs/handoff/ATL-139-trade-ladder.md) gained
 * `priceAt` (a price from some other instant, labelled with its time, never "Now") alongside
 * the existing `mark` (price now, always "Now"). This covers the Ladder component directly,
 * the one shared by the proposal card (ATL-164) and the TechnicalSetup card (ATL-139).
 */

const PROPS = {
  side: 'long' as const,
  entry: ['248.60', '250.20'] as [string, string],
  stop: '241.80',
  targets: [{ price: '258.50', pct: '100' }],
  decimals: 2,
}

afterEach(() => {
  void i18n.changeLanguage('en')
})

describe('Ladder', () => {
  it('a fresh mark still renders ld-now, unaffected by the priceAt addition', () => {
    render(<Ladder {...PROPS} mark="249.95" lang="en" />)
    const ladder = screen.getByTestId('ladder')
    const now = within(ladder).getAllByTestId('ld-now')
    expect(now.length).toBeGreaterThan(0)
    expect(now.map((x) => x.textContent).join('')).toContain('Now')
    expect(ladder.getAttribute('aria-label')).toContain('Price now 249.95')
    expect(within(ladder).queryByTestId('ld-at')).toBeNull()
  })

  it('priceAt renders ld-at with its time, never ld-now', () => {
    render(<Ladder {...PROPS} priceAt={{ price: '249.95', at: '2026-10-28T11:00:00Z' }} lang="en" />)
    const ladder = screen.getByTestId('ladder')
    const at = within(ladder).getAllByTestId('ld-at')
    expect(at.length).toBeGreaterThan(0)
    expect(at.map((x) => x.textContent).join('')).toContain('At 14:00')
    expect(within(ladder).queryByTestId('ld-now')).toBeNull()
    expect(ladder.getAttribute('aria-label')).not.toContain('Price now')
  })
})
