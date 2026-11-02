import { Scale } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SignedValue } from '../../../ui'
import { Medallion } from '../../warroom/Medallion'
import { Tip } from '../parts'
import type { AgentScores, ScoreDays } from './api'
import { fmtWeight, weightDetail } from './format'
import { CARDS, METRICS, figure, trend, type Weight } from './model'
import { Headline, MetricRow } from './rows'
import { SC_NS, useSc } from './i18n'
import { T } from './shared'
import { Trend } from './Trend'
import './scorecards.css'

/*
 * One agent's scorecard (design v16 §5 "The card"): its headline figure against its target with
 * the n it rests on, a 7-day trend that mirrors in Arabic, the rows under it, and the vote-weight
 * chip for voters. Under a metric's minimum, "Not enough data" and the n replace the figure.
 * Exported for Portfolio → Scorecards (ATL-204), which passes `pnl` to add the P&L line.
 */

/** The `⚖ Vote 1.12×` chip. A default 1.00× (under 10 scored votes) is outlined and grey. */
export function WeightChip({ w }: { w: Weight }) {
  const { t, lang } = useSc()
  const detail = weightDetail(w, t, lang)
  const val = fmtWeight(w.weight, lang)
  return (
    <Tip label={t('voteTip', { w: val, detail })}>
      <span className={w.few ? 'sc-weight is-default' : 'sc-weight'} tabIndex={0} data-testid="sc-weight">
        <Scale className="icon" aria-hidden="true" />
        <span>
          <T k="vote" values={{ w: val }} />
        </span>
      </span>
    </Tip>
  )
}

export interface ScorecardCardProps {
  slug: string
  /** The agent's entry from GET /api/agents/scores; missing = no rows. */
  scores: AgentScores | undefined
  days: ScoreDays
  /** The trend's Baghdad days: meta.from, and the newest scored day (today is not scored yet). */
  from: string
  to: string
  /** Voters only: the 30-day reliability weight. */
  weight?: Weight | null
  /** Portfolio → Scorecards: show "P&L of trades it led" when the API sends it. */
  pnl?: boolean
}

/** One agent's scorecard. Needs a radix Tooltip.Provider above it (for the weight chip). */
export function ScorecardCard({ slug, scores, days, from, to, weight, pnl }: ScorecardCardProps) {
  const { t } = useTranslation([SC_NS, 'warroom'])
  const spec = CARDS.find((c) => c.slug === slug)
  if (!spec) return null
  const rows = scores?.days ?? []
  const hs = METRICS[spec.head]!
  const hf = figure(hs, rows)
  const points = trend(hs, rows, from, to)
  const pnlUsd = pnl ? scores?.pnl_attributed_usd : null
  return (
    <article className="sc-card" id={`sc-${slug}`} tabIndex={-1} aria-labelledby={`sc-name-${slug}`} data-testid="sc-card" data-slug={slug}>
      <header className="sc-card-head">
        <Medallion slug={slug} size={36} status="idle" />
        <div className="sc-who">
          <h3 className="sc-name" id={`sc-name-${slug}`}>
            {t(`warroom:agents.${slug}.name`)}
          </h3>
          <p className="sc-role">{t(`warroom:agents.${slug}.role`)}</p>
        </div>
        {weight ? <WeightChip w={weight} /> : <span />}
      </header>
      {pnlUsd !== null && pnlUsd !== undefined && (
        <div className="sc-pnl" data-testid="sc-pnl">
          <span className="sc-pnl-label">{t('pnl')}</span>
          <SignedValue value={pnlUsd} unit="USD" />
        </div>
      )}
      <Headline spec={hs} f={hf} />
      {hf.enough ? (
        <Trend spec={hs} points={points} label={t(`m.${hs.id}`)} days={days} />
      ) : hs.unit !== 'mix' && hs.unit !== 'count' ? (
        <Trend spec={hs} points={[]} label={t(`m.${hs.id}`)} days={days} />
      ) : null}
      {spec.rows.length > 0 && (
        <div className="sc-rows">
          {spec.rows.map((id) => {
            const ms = METRICS[id]!
            return <MetricRow key={id} spec={ms} f={figure(ms, rows)} />
          })}
        </div>
      )}
    </article>
  )
}
