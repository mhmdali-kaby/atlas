import { ChevronRight, CircleCheck, ListChecks, TrendingDown, TrendingUp } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, formatSigned, type Lang } from '../../../lib/format'
import { proposalHref } from '../../approvals/api'
import { Duration } from '../../portfolio/parts'
import { fillSlots, slot } from '../slots'
import { Ladder } from './Ladder'
import { CardHead, Fig, Signed, sgnClass, useRuleText } from './pmParts'
import { ruleKey, type PmAction, type PositionUpdate } from './pm'
import { rText } from './rText'
import { InstrumentButton } from './TechnicalSetupCard'
import './pm.css'

/*
 * The Portfolio Manager's position check (brief §7.8 "EVERY CHECK", design v14 wr-analysts.js
 * position_update): instrument and side, PnL in USD and R, time in trade, funding paid, the trade on
 * the compact ladder, the distance to the stop and TP1 in ATR(1H), then each rule that fired with
 * its proposed action and a link to the proposal card. Every figure is code's (status_card).
 *
 * The ladder carries no "Now" marker: this is a record of one check, and the design labels its price
 * with the check's time (priceAt), which the shared ladder does not draw yet.
 */

const shortId = (id: string) => id.slice(0, 8)

function ActionRow({ action, card, lang }: { action: PmAction; card: PositionUpdate; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const ruleText = useRuleText()
  const pct = action.closePct ? `${formatFixed(action.closePct, Number(action.closePct) % 1 ? 1 : 0, lang)}%` : null
  const words =
    action.kind === 'amend_stop' && action.stop
      ? t('pm.act.amend_stop', { stop: formatFixed(action.stop, card.tick, lang) })
      : action.kind === 'partial_close' && pct
        ? t('pm.act.partial_close', { pct })
        : action.kind === 'close' || action.kind === 'amend_targets'
          ? t(`pm.act.${action.kind}`)
          : action.kind
  return (
    <div className="an-rule" data-testid="pu-rule" data-rule={action.rule}>
      <span className="an-rule-chip">
        <ListChecks className="icon" aria-hidden="true" />
        <span data-testid="pu-rule-name">{t('pm.rule', { rule: ruleText(action.rule, ruleKey(action.rule)) })}</span>
      </span>
      <p className="an-rule-act" dir="auto">
        {words}
      </p>
      {action.proposalId ? (
        <Link className="an-link" to={proposalHref(action.proposalId)} aria-label={t('pm.openProposal', { id: shortId(action.proposalId) })} data-testid="pu-proposal-link">
          <span>{fillSlots(t('pm.proposal', { id: slot('id') }), { id: <Fig>{shortId(action.proposalId)}</Fig> })}</span>
          <ChevronRight className="icon icon-dir" aria-hidden="true" />
        </Link>
      ) : (
        <span className="an-pst">{t('pm.notSubmitted')}</span>
      )}
    </div>
  )
}

export function PositionUpdateCard({ card, lang }: { card: PositionUpdate; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const ruleText = useRuleText()
  const DirIcon = card.side === 'long' ? TrendingUp : TrendingDown
  const atr = (v: string) => `${formatFixed(v, 1, lang)} ATR`
  const minutes = card.minutesInTrade
  return (
    <div className="hv-card an-card an-pu" role="group" aria-label={t('pm.kindPu')} data-testid="pu-card">
      <CardHead kind="pu" asOf={card.asOf} lang={lang} />
      <p className={`an-title is-${card.side}`}>
        <InstrumentButton instId={card.instId} />
        <span className="an-dir" data-testid="pu-side">
          <DirIcon className="icon" aria-hidden="true" />
          <span>{t(`pm.${card.side}`)}</span>
        </span>
      </p>
      <p className="an-status">
        <span className="an-pnl" data-testid="pu-pnl">
          <Signed value={card.pnlUsd} lang={lang} big />
          {card.r !== null && <Fig className={`an-r ${sgnClass(card.r)}`}>{rText(card.r)}</Fig>}
        </span>
        {minutes !== null && (
          <span className="an-fact" data-testid="pu-in-trade">
            {fillSlots(t('pm.inTrade', { d: slot('d') }), {
              d: (
                <span className="an-dur">
                  <Duration ms={minutes * 60_000} lang={lang} />
                </span>
              ),
            })}
          </span>
        )}
        <span className="an-fact" data-testid="pu-funding">
          {card.fundingPaidUsd === null
            ? t('pm.fundingNone')
            : fillSlots(t('pm.fundingPaid', { v: slot('v') }), { v: <Fig>{formatSigned(card.fundingPaidUsd, 'USD', lang)}</Fig> })}
        </span>
      </p>
      {card.stop && (
        <div className="an-ladder is-compact">
          <Ladder
            side={card.side}
            entry={[card.entry, card.entry]}
            stop={card.stop}
            riskStop={card.initialStop ?? undefined}
            targets={card.targets.map((price) => ({ price }))}
            decimals={card.tick}
            compact
            lang={lang}
          />
        </div>
      )}
      {card.stopAtr !== null && (
        <p className="an-dist" data-testid="pu-dist">
          {card.tp1Atr !== null ? t('pm.dist', { s: atr(card.stopAtr), tp: atr(card.tp1Atr) }) : t('pm.distStop', { s: atr(card.stopAtr) })}
        </p>
      )}
      {card.actions.map((a, i) => (
        <ActionRow key={`${a.kind}-${a.rule}-${i}`} action={a} card={card} lang={lang} />
      ))}
      {card.advisories.map((a, i) => (
        <div key={`adv-${a.rule}-${i}`} className="an-rule" data-testid="pu-advisory">
          <span className="an-rule-chip">
            <ListChecks className="icon" aria-hidden="true" />
            <span>{t('pm.rule', { rule: ruleText(a.rule, ruleKey(a.rule)) })}</span>
          </span>
          <p className="an-rule-act" dir="auto">
            {a.reason}
          </p>
        </div>
      ))}
      {card.actions.length === 0 && card.advisories.length === 0 && (
        <p className="an-ok">
          <CircleCheck className="icon" aria-hidden="true" />
          <span>{t('pm.thesisIntact')}</span>
        </p>
      )}
    </div>
  )
}
