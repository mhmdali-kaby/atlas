import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../../i18n'
import { API_URL, resetCsrfForTests } from '../../../lib/api'
import type { ScoreDay, ScoreDays } from './api'
import { scoresFixture } from './fixtures'
import { METRICS, calVerdict, figure, trend, weightOf } from './model'
import { ScorecardsPage } from './ScorecardsPage'

function fakeBackend(o: { fail?: boolean; empty?: boolean } = {}) {
  const requests: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(API_URL, '')
      const reply = (status: number, json?: unknown) =>
        new Response(json === undefined ? null : JSON.stringify(json), { status, headers: { 'Content-Type': 'application/json' } })
      if (path.startsWith('/api/agents/scores')) {
        requests.push(path)
        if (o.fail) return reply(500, { message: 'Server Error' })
        const days = Number(new URL(path, 'http://x').searchParams.get('days')) as ScoreDays
        return reply(200, scoresFixture(days, { empty: o.empty }))
      }
      return reply(404)
    }),
  )
  return requests
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ScorecardsPage />
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

describe('ScorecardsPage', () => {
  it('shows every agent’s card with its metrics and n; the Strategist shows its rejection rate against the 10% target', async () => {
    const requests = fakeBackend()
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
    expect(requests).toEqual(['/api/agents/scores?days=30'])

    const strat = within(card('strategist'))
    const reject = strat.getByTestId('sc-row-reject')
    expect(reject).toHaveTextContent('Rejected by the Risk Engine')
    // 3 of 58 proposals rejected = 5%, against the "under 10%" target.
    expect(reject).toHaveTextContent('3 of 58 proposals')
    expect(reject).toHaveTextContent('5%')
    expect(within(reject).getByTestId('sc-pill-reject')).toHaveTextContent('Meets the under 10% target')
    // Average R headline with its n, and the calibration pair in words.
    expect(strat.getByTestId('sc-head-avgR')).toHaveTextContent('+0.31R')
    expect(strat.getByTestId('sc-head-avgR')).toHaveTextContent('Across 29 trades it proposed')
    expect(strat.getByTestId('sc-row-calStrat')).toHaveTextContent('Overconfident by 8 points')
    expect(strat.getByTestId('sc-row-calStrat')).toHaveTextContent('n = 58')

    // Every other card's headline with its n.
    expect(within(card('market_scout')).getByTestId('sc-head-selection_edge')).toHaveTextContent('+0.42 ATR')
    expect(within(card('market_scout')).getByTestId('sc-head-selection_edge')).toHaveTextContent('n = 87')
    expect(within(card('news_intelligence')).getByTestId('sc-head-latency')).toHaveTextContent('74')
    expect(within(card('news_intelligence')).getByTestId('sc-head-latency')).toHaveTextContent('Median 48, over 29 alerts')
    expect(within(card('news_intelligence')).getByTestId('sc-pill-latency')).toHaveTextContent('Meets the under 90 s target')
    expect(within(card('historian')).getByTestId('sc-head-calHist')).toHaveTextContent('Said 58%')
    expect(within(card('historian')).getByTestId('sc-head-calHist')).toHaveTextContent('53% happened')
    expect(within(card('historian')).getByTestId('sc-head-calHist')).toHaveTextContent('Brier 0.218')
    expect(within(card('technical_analyst')).getByTestId('sc-head-tbs')).toHaveTextContent('of 29 triggered setups')
    expect(within(card('technical_analyst')).getByTestId('sc-row-rr')).toHaveTextContent('Stated +1.90R on average, against a +1.80R or more target, 29 setups')
    expect(within(card('derivatives_sentiment')).getByTestId('sc-head-supports')).toHaveTextContent('29 of 58 reads')
    expect(within(card('devils_advocate')).getByTestId('sc-head-falseAlarm')).toHaveTextContent('of 29')
    expect(within(card('portfolio_manager')).getByTestId('sc-row-rules')).toHaveTextContent('290 of 290 actions')
    expect(within(card('orchestrator')).getByTestId('sc-head-onTime')).toHaveTextContent('841 of 870 cycles')

    // Voters carry their vote weight; a weight under 10 scored votes is the outlined default.
    expect(within(card('technical_analyst')).getByTestId('sc-weight')).toHaveTextContent('Vote 1.12×')
    expect(within(card('news_intelligence')).getByTestId('sc-weight')).toHaveClass('is-default')
    expect(within(card('strategist')).queryByTestId('sc-weight')).toBeNull()
    const weights = screen.getAllByTestId('sc-w-row')
    expect(weights.map((r) => r.dataset.slug)).toEqual(['technical_analyst', 'derivatives_sentiment', 'news_intelligence', 'historian', 'devils_advocate'])
    expect(weights[0]).toHaveTextContent('26 of 42 votes right')
    expect(weights[2]).toHaveTextContent('8 scored votes, needs 10')
  })

  it('shows "Not enough data" and the n, never a value, when n is under the minimum', async () => {
    fakeBackend()
    renderPage()
    await screen.findAllByTestId('sc-card')
    // 3 squeeze calls in 30 days; the row needs 10.
    const squeeze = within(card('derivatives_sentiment')).getByTestId('sc-nd-squeeze')
    expect(squeeze).toHaveTextContent('Not enough data')
    expect(squeeze).toHaveTextContent('n = 3, needs 10')
    expect(squeeze).toHaveAttribute('aria-label', 'Squeeze calls that followed through: not enough data, 3 cases of the 10 it needs.')
    expect(squeeze).not.toHaveTextContent('%')
  })

  it('refetches with days=7 and days=90 when the period changes', async () => {
    const requests = fakeBackend()
    renderPage()
    await screen.findAllByTestId('sc-card')
    const user = userEvent.setup()
    await user.click(screen.getByRole('radio', { name: '7 days' }))
    await waitFor(() => expect(requests).toContain('/api/agents/scores?days=7'))
    // A week's sample: Market Scout's 18 picks are under its minimum of 20.
    await waitFor(() => expect(within(card('market_scout')).getByTestId('sc-nd-selection_edge')).toHaveTextContent('n = 18, needs 20'))
    await user.click(screen.getByRole('radio', { name: '90 days' }))
    await waitFor(() => expect(requests).toContain('/api/agents/scores?days=90'))
    // 62 days of history: the head says so.
    expect(await screen.findByTestId('sc-shorter')).toHaveTextContent('Atlas has 62 days of scores, so 90 days shows those 62.')
    await user.click(screen.getByRole('radio', { name: '30 days' }))
    expect(requests).toEqual(['/api/agents/scores?days=30', '/api/agents/scores?days=7', '/api/agents/scores?days=90'])
  })

  it('hides the period control when scores fail to load, and retries', async () => {
    const requests = fakeBackend({ fail: true })
    renderPage()
    expect(await screen.findByTestId('sc-error')).toHaveTextContent('Scorecards didn’t load')
    expect(screen.queryByRole('radiogroup')).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(requests).toHaveLength(2))
  })

  it('shows the no-scores state with no period control', async () => {
    fakeBackend({ empty: true })
    renderPage()
    expect(await screen.findByTestId('sc-empty')).toHaveTextContent('The first scorecards appear tomorrow morning.')
    expect(screen.queryByRole('radiogroup')).toBeNull()
    expect(screen.getByTestId('sc-see-team')).toHaveAttribute('href', '/agents')
  })

  it('renders in Arabic', async () => {
    await i18n.changeLanguage('ar')
    fakeBackend()
    renderPage()
    await screen.findAllByTestId('sc-card')
    expect(within(card('strategist')).getByTestId('sc-row-reject')).toHaveTextContent('رفضها محرّك المخاطر')
    expect(within(card('derivatives_sentiment')).getByTestId('sc-nd-squeeze')).toHaveTextContent('لا تكفي البيانات')
  })
})

const row = (day: string, n: number, data: Record<string, unknown>): ScoreDay => ({ day, n, hit_rate: null, brier_score: null, data })

describe('scorecard model', () => {
  it('n-weights daily values by the metric’s own sample key and sums hits', () => {
    const f = figure(METRICS.reject!, [
      row('2026-10-01', 9, { rejection_rate: 0.5, submitted_n: 2, risk_rejected_n: 1 }),
      row('2026-10-02', 9, { rejection_rate: 0, submitted_n: 8, risk_rejected_n: 0 }),
    ])
    expect(f.value).toBeCloseTo(0.1)
    expect(f.n).toBe(10)
    expect(f.k).toBe(1)
    expect(f.enough).toBe(false)
  })

  it('skips days without the value and rows that only carry reliability keys', () => {
    const f = figure(METRICS.tbs!, [
      row('2026-10-01', 0, { reliability_weight: '1.2', reliability_n: 12 }),
      row('2026-10-02', 3, { target_before_stop_rate: null, target_before_stop_n: 0 }),
    ])
    expect(f.value).toBeNull()
    expect(f.n).toBe(0)
  })

  it('names the calibration gap in words', () => {
    expect(calVerdict(0.55, 0.47)).toEqual({ kind: 'over', points: 8, wide: false })
    expect(calVerdict(0.4, 0.52)).toEqual({ kind: 'under', points: 12, wide: true })
    expect(calVerdict(0.5, 0.52).kind).toBe('ok')
  })

  it('leaves out trend points whose 7 days are under the minimum', () => {
    const rows = Array.from({ length: 10 }, (_, i) => row(`2026-10-${String(i + 1).padStart(2, '0')}`, 5, { selection_edge_atr: 0.3 }))
    const pts = trend(METRICS.selection_edge!, rows, '2026-10-01', '2026-10-10')
    expect(pts[2]!.value).toBeNull() // 15 picks
    expect(pts[3]!.value).toBeCloseTo(0.3) // 20 picks
    expect(pts).toHaveLength(10)
  })

  it('reads the newest reliability weight, defaulting under 10 votes', () => {
    const w = weightOf({
      agent: 'historian',
      name: 'Historian',
      summary: { n: 0, days_scored: 2, hit_rate: null, brier_score: null, reliability_weight: '1', reliability_weight_day: '2026-10-02' },
      days: [
        row('2026-10-01', 0, { reliability_weight: '1.3', reliability_n: 20, reliability_correct: 16 }),
        row('2026-10-02', 0, { reliability_weight: '1', reliability_n: 6, reliability_correct: 4 }),
      ],
    })
    expect(w).toEqual({ weight: 1, n: 6, right: 4, few: true })
  })
})
