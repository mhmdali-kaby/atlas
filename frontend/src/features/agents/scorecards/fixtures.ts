import type { AgentScores, ScoreDay, ScoreDays, ScoresReport } from './api'
import { dayRange } from './model'

/*
 * Fixture GET /api/agents/scores responses for the vitest suite and the Playwright spec, shaped
 * like ATL-200's JSON with each scorer's real data keys (ATL-110, ATL-195, ATL-196, ATL-197,
 * ATL-198). Test-only: nothing in the app imports it.
 */

export const FIXTURE_TO = '2026-10-28'
const ROSTER: Array<[string, string]> = [
  ['orchestrator', 'Orchestrator'],
  ['market_scout', 'Market Scout'],
  ['news_intelligence', 'News Intelligence'],
  ['historian', 'Historian'],
  ['technical_analyst', 'Technical Analyst'],
  ['derivatives_sentiment', 'Derivatives & Sentiment'],
  ['strategist', 'Strategist'],
  ['devils_advocate', 'Devil’s Advocate'],
  ['portfolio_manager', 'Portfolio Manager'],
]

/** One agent's `data` on the i-th scored day (0 = oldest). */
function dataFor(slug: string, i: number): { n: number; hit_rate: string | null; brier: string | null; data: Record<string, unknown> } {
  switch (slug) {
    case 'market_scout':
      return { n: 3, hit_rate: '0.6667', brier: null, data: { selection_edge_atr: 0.42, directional_accuracy: 0.6667, directional_n: 3, directional_correct: 2 } }
    case 'news_intelligence':
      return { n: 1, hit_rate: '1.0000', brier: null, data: { latency_p90_s: 74, latency_median_s: 48, within_target_share: 1 } }
    case 'historian':
      return {
        n: 6,
        hit_rate: null,
        brier: '0.218000',
        data: {
          brier_score: 0.218, brier_n: 6, mean_p: 0.58, observed: 0.53,
          verdict_mix: { supports: 1, weakly_supports: 2, does_not_support: 2, contradicts: i % 5 === 0 ? 1 : 0 },
          verdicts_n: i % 5 === 0 ? 6 : 5,
        },
      }
    case 'technical_analyst':
      return {
        n: 1,
        hit_rate: i % 2 ? '1.0000' : '0.0000',
        brier: null,
        data: {
          target_before_stop_rate: i % 2 ? 1 : 0, target_before_stop_n: 1, target_hits: i % 2,
          level_hold_rate: 0.75, level_hold_n: 4, avg_realised_r: 1.1, realised_r_n: 1, avg_expected_r: 1.9, expected_r_n: 1,
          no_setup_accuracy: 0.5, no_setup_n: 2,
        },
      }
    case 'derivatives_sentiment':
      return {
        n: 2,
        hit_rate: '0.5000',
        brier: '0.190000',
        data: {
          supports_accuracy: 0.5, supports_n: 2, supports_correct: 1,
          // Squeeze calls are rare: one every ten days, so the row stays under its minimum of 10.
          squeeze_calls_n: i % 10 === 0 ? 1 : 0, squeeze_call_hits: i % 10 === 0 ? 1 : 0, squeeze_call_hit_rate: i % 10 === 0 ? 1 : null,
          brier_score: 0.19, brier_n: 2, mean_p: 0.38, observed: 0.29,
        },
      }
    case 'strategist': {
      const rejected = i % 10 === 0 ? 1 : 0
      return {
        n: 2,
        hit_rate: '0.4700',
        brier: '0.236000',
        data: {
          avg_realised_r: 0.31, taken_n: 1, rejection_rate: rejected / 2, risk_rejected_n: rejected, submitted_n: 2,
          brier_score: 0.236, mean_p: 0.55, observed: 0.47, brier_n: 2, regret_r: -0.18, regret_n: 1,
        },
      }
    }
    case 'devils_advocate':
      return {
        n: 2,
        hit_rate: '0.5000',
        brier: null,
        data: {
          high_n: 1, high_won: i % 5 === 0 ? 1 : 0, false_alarm_rate: i % 5 === 0 ? 1 : 0,
          low_n: 1, low_lost: i % 6 === 0 ? 1 : 0, miss_rate: i % 6 === 0 ? 1 : 0,
        },
      }
    case 'portfolio_manager':
      return {
        n: 1,
        hit_rate: null,
        brier: null,
        data: {
          r_captured: 0.58, r_captured_n: 1, trades_n: 1, losers_n: 1, losers_within_n: 1, losers_within_rate: 1,
          compliance_rate: 1, compliance_n: 10, compliance_ok: 10,
        },
      }
    case 'orchestrator':
      return {
        n: 30,
        hit_rate: '0.9667',
        brier: null,
        data: {
          on_time_rate: 0.9667, cycles_n: 30, cycles_on_time: 29, ack_n: 2, ack_p90_seconds: 8, ack_median_seconds: 4,
          loops_stopped: i % 7 === 0 ? 1 : 0,
        },
      }
    default:
      return { n: 0, hit_rate: null, brier: null, data: {} }
  }
}

const WEIGHTS: Record<string, { reliability_weight: string; reliability_n: number; reliability_correct: number }> = {
  technical_analyst: { reliability_weight: '1.12', reliability_n: 42, reliability_correct: 26 },
  derivatives_sentiment: { reliability_weight: '1.11', reliability_n: 44, reliability_correct: 27 },
  news_intelligence: { reliability_weight: '1', reliability_n: 8, reliability_correct: 5 },
  historian: { reliability_weight: '1.06', reliability_n: 45, reliability_correct: 25 },
  devils_advocate: { reliability_weight: '0.8', reliability_n: 44, reliability_correct: 13 },
}

const DAY = 86_400_000
const shift = (day: string, d: number) => new Date(Date.parse(`${day}T00:00:00Z`) + d * DAY).toISOString().slice(0, 10)

/**
 * A /api/agents/scores response. Atlas has `history` days of scores (default 62), ending the day
 * before `to`; today is not scored yet.
 */
export function scoresFixture(days: ScoreDays, o: { history?: number; to?: string; empty?: boolean } = {}): ScoresReport {
  const to = o.to ?? FIXTURE_TO
  const from = shift(to, -(days - 1))
  const lastScored = shift(to, -1)
  const firstScored = shift(lastScored, -((o.history ?? 62) - 1))
  const scored = o.empty ? [] : dayRange(from > firstScored ? from : firstScored, lastScored)
  const agents: AgentScores[] = ROSTER.map(([slug, name]) => {
    const rows: ScoreDay[] = scored.map((day) => {
      const i = Math.round((Date.parse(`${day}T00:00:00Z`) - Date.parse(`${firstScored}T00:00:00Z`)) / DAY)
      const d = dataFor(slug, i)
      const data = day === lastScored && WEIGHTS[slug] ? { ...d.data, ...WEIGHTS[slug] } : d.data
      return { day, n: d.n, hit_rate: d.hit_rate, brier_score: d.brier, data }
    })
    const n = rows.reduce((s, r) => s + r.n, 0)
    const w = WEIGHTS[slug]
    return {
      agent: slug,
      name,
      summary: {
        n,
        days_scored: rows.length,
        hit_rate: null,
        brier_score: null,
        reliability_weight: w && rows.length ? w.reliability_weight : null,
        reliability_weight_day: w && rows.length ? lastScored : null,
      },
      days: rows,
    }
  })
  return { data: agents, meta: { env: 'demo', days, from, to, timezone: 'Asia/Baghdad' } }
}
