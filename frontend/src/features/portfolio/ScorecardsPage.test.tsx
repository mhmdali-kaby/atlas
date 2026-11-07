import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { API_URL, resetCsrfForTests } from '../../lib/api'
import { scoresFixture } from '../agents/scorecards/fixtures'
import { PortfolioScorecardsPage } from './ScorecardsPage'

/*
 * Portfolio → Scorecards (ATL-204; design v16 §6): the same ScorecardsView body as Agents →
 * Scorecards (ATL-201), with no vote-weight panel and each agent's pnl_attributed_usd shown
 * on its card when the API sends it. Reuses ATL-201's fixture, grafting on the one field it
 * doesn't set, since GET /api/agents/scores is otherwise identical for both pages.
 */

function fakeBackend(build: () => unknown) {
  const requests: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(API_URL, '')
      if (path.startsWith('/api/agents/scores')) {
        requests.push(path)
        return new Response(JSON.stringify(build()), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(null, { status: 404 })
    }),
  )
  return requests
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PortfolioScorecardsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const card = (slug: string) => screen.getAllByTestId('sc-card').find((c) => c.dataset.slug === slug)!

beforeEach(async () => {
  resetCsrfForTests()
  await i18n.changeLanguage('en')
})
afterEach(() => vi.unstubAllGlobals())

describe('PortfolioScorecardsPage', () => {
  it('renders ATL-201\'s ScorecardsView, portfolio variant: one card per agent, no weights panel, the portfolio intro', async () => {
    const report = scoresFixture(30)
    fakeBackend(() => report)
    renderPage()

    const cards = await screen.findAllByTestId('sc-card')
    expect(cards.map((c) => c.dataset.slug)).toEqual([
      'market_scout',
      'news_intelligence',
      'historian',
      'technical_analyst',
      'derivatives_sentiment',
      'strategist',
      'devils_advocate',
      'portfolio_manager',
      'orchestrator',
    ])
    // Same figures as the Agents page's fixture (same component, same data).
    expect(within(card('market_scout')).getByTestId('sc-head-selection_edge')).toHaveTextContent('n = 87')
    expect(screen.getByText('The same scorecards as on the Agents page, with the P&L of the trades each agent led.')).toBeInTheDocument()
    expect(screen.queryByTestId('sc-weights')).toBeNull()
    expect(within(card('strategist')).queryByTestId('sc-weight')).toBeNull()
    expect(within(card('technical_analyst')).queryByTestId('sc-weight')).toBeNull()
  })

  it('shows the signed P&L with its icon when the API sends pnl_attributed_usd, and omits the line when it is null', async () => {
    const report = scoresFixture(30)
    const by = new Map(report.data.map((a) => [a.agent, a]))
    by.get('strategist')!.pnl_attributed_usd = '420.75'
    by.get('portfolio_manager')!.pnl_attributed_usd = '-80'
    fakeBackend(() => report)
    renderPage()
    await screen.findAllByTestId('sc-card')

    const strat = within(card('strategist')).getByTestId('sc-pnl')
    expect(strat).toHaveTextContent('P&L of trades it led')
    expect(strat).toHaveTextContent('$420.75')
    expect(strat.querySelector('[data-sign="profit"]')).not.toBeNull()
    expect(strat.querySelector('[data-sign-icon]')).not.toBeNull()

    const pm = within(card('portfolio_manager')).getByTestId('sc-pnl')
    expect(pm).toHaveTextContent('$80.00')
    expect(pm.querySelector('[data-sign="loss"]')).not.toBeNull()

    // Not sent for this agent (fixture leaves it undefined): the line is omitted entirely.
    expect(within(card('historian')).queryByTestId('sc-pnl')).toBeNull()
    expect(within(card('market_scout')).queryByTestId('sc-pnl')).toBeNull()
  })
})
