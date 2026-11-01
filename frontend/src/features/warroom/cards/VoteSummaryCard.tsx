import { CircleCheck, CircleDashed, CircleX, ShieldCheck, ShieldX, UsersRound } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../../lib/format'
import { Medallion } from '../Medallion'
import { agentBySlug } from '../agents'
import { fillSlots, slot } from '../slots'
import { VOTE_ICON, type VoteSummary, type Voter } from './debate'
import { Fig, Head } from './debateParts'
import './debate.css'

/*
 * The weighted vote (brief §7.9, design v11 Phase 2 §6): the score and the result first, then each
 * voter's vote, weight (confidence × reliability) and contribution, with its 0–1 bar crossing the
 * threshold line; the total row, the sum as one LTR island, the Devil's Advocate gate and the rule.
 * A missing voter is drawn as "Skip (no vote)" at confidence 0.5, the way the code counted it.
 */

const clamp01 = (s: string) => Math.min(1, Math.max(0, Number(s) || 0))

const voteKey = (v: Voter) => (v.missing ? 'missing' : v.vote === 'change' && v.fixApplied ? 'changeApplied' : v.vote)

export function VoteSummaryCard({ card, target, onJump, ts, lang }: { card: VoteSummary; target: string | null; onJump: (id: string) => void; ts: string; lang: Lang }) {
  const { t } = useTranslation('debate')
  const { t: tw } = useTranslation('warroom')
  const n2 = (s: string) => formatFixed(s, 2, lang)
  const th = n2(card.threshold)
  const thStyle = { '--th': String(clamp01(card.threshold) * 100) } as CSSProperties
  const gate = card.daRating === null ? 'missing' : card.daRating <= card.daMaxRating ? 'ok' : 'fail'
  const GateIcon = gate === 'ok' ? ShieldCheck : ShieldX

  return (
    <div className={`hv-card db-card db-votes ${card.passed ? 'is-passed' : 'is-failed'}`} role="group" aria-label={t('vt.kind')} data-testid="db-votes">
      <Head icon={UsersRound} kind={t('vt.kind')} version={card.version} target={target} onJump={onJump} ts={ts} lang={lang} />
      <div className="db-vt-result">
        <Fig className="db-vt-score">{n2(card.ratio)}</Fig>
        <span className={`db-pill ${card.passed ? 'is-pass' : 'is-fail'}`} data-testid="db-vt-result">
          {card.passed ? <CircleCheck className="icon" aria-hidden="true" /> : <CircleX className="icon" aria-hidden="true" />}
          <span>{t(card.passed ? 'vt.passed' : 'vt.failed')}</span>
        </span>
        <span className="db-vt-needs" data-testid="db-vt-needs">
          {fillSlots(t('vt.needs', { th: slot('th') }), { th: <Fig>{th}</Fig> })}
        </span>
      </div>

      <div className="db-vt" role="table" aria-label={t('vt.kind')} style={thStyle}>
        <div className="db-vt-row is-head" role="row">
          <span role="columnheader">{t('vt.voter')}</span>
          <span role="columnheader">{t('vt.vote')}</span>
          <span role="columnheader" className="db-vt-scale" aria-label={t('vt.counts')}>
            <Fig>0</Fig>
            <span className="db-vt-thlabel" aria-hidden="true">
              <Fig>{th}</Fig>
            </span>
            <Fig>1</Fig>
          </span>
          <span role="columnheader">
            <span className="db-tipped" tabIndex={0} title={t('vt.weightTip')}>
              {t('vt.weight')}
            </span>
          </span>
          <span role="columnheader">{t('vt.adds')}</span>
        </div>
        {card.voters.map((v) => {
          const name = agentBySlug(v.agent) ? tw(`agents.${v.agent}.name`) : v.agent
          const VIcon = v.missing ? CircleDashed : VOTE_ICON[v.vote]
          const cls = v.missing ? 'missing' : v.vote
          const word = t(`v.${voteKey(v)}`)
          return (
            <div
              key={v.agent}
              className={`db-vt-row${v.missing ? ' is-missing' : ''}`}
              role="row"
              aria-label={t('vt.rowAria', { name, vote: word, w: n2(v.weight), c: n2(v.contribution) })}
              data-testid="db-voter"
              data-agent={v.agent}
            >
              <span className="db-vt-who" role="cell">
                <Medallion slug={v.agent} size={22} />
                <span className="db-vt-name">{name}</span>
              </span>
              <span className={`db-vt-vote is-${cls}`} role="cell" data-testid="db-voter-vote">
                <VIcon className="icon" aria-hidden="true" />
                <span>{word}</span>
              </span>
              <span className="db-vt-bar" role="cell" style={{ '--v': String(clamp01(v.points)) } as CSSProperties}>
                <i className="db-vt-fill" />
                <i className="db-vt-th" />
              </span>
              <span className="db-vt-w" role="cell">
                <Fig className="db-vt-math">{`${n2(v.confidence)} × ${n2(v.reliability)}`}</Fig>
                <Fig className="db-vt-eq">{n2(v.weight)}</Fig>
              </span>
              <span className="db-vt-c" role="cell">
                <Fig>{n2(v.contribution)}</Fig>
              </span>
            </div>
          )
        })}
        <div className="db-vt-row is-total" role="row">
          <span role="cell" className="db-vt-who">
            <span className="db-vt-name">{t('vt.total')}</span>
          </span>
          <span role="cell" />
          <span className="db-vt-bar is-total" role="cell" style={{ '--v': String(clamp01(card.ratio)) } as CSSProperties}>
            <i className="db-vt-fill" />
            <i className="db-vt-th" />
          </span>
          <span role="cell" className="db-vt-w">
            <Fig>{n2(card.totalWeight)}</Fig>
          </span>
          <span role="cell" className="db-vt-c">
            <Fig>{n2(card.weightedScore)}</Fig>
          </span>
        </div>
      </div>

      <p className="db-vt-mathline" data-testid="db-vt-math">
        <Fig>{`${n2(card.weightedScore)} ÷ ${n2(card.totalWeight)} = ${n2(card.ratio)}`}</Fig>
      </p>
      <p className={`db-gate is-${gate}`} data-testid="db-gate">
        <GateIcon className="icon" aria-hidden="true" />
        <span>
          {gate === 'missing'
            ? t('vt.gateMissing')
            : fillSlots(t(gate === 'ok' ? 'vt.gateOk' : 'vt.gateFail', { r: slot('r'), max: slot('max') }), {
                r: <Fig>{String(card.daRating)}</Fig>,
                max: <Fig>{String(card.daMaxRating)}</Fig>,
              })}
        </span>
      </p>
      <p className="db-foot">{fillSlots(t('vt.foot', { th: slot('th'), max: slot('max') }), { th: <Fig>{th}</Fig>, max: <Fig>{String(card.daMaxRating)}</Fig> })}</p>
    </div>
  )
}
