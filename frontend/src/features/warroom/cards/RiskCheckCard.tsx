import { ArrowRightToLine, CircleCheck, CircleDashed, CircleX, Eye, Hand, Send, ShieldCheck, Shrink, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatMoney, type Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import { Inline } from '../text'
import { AutonomyGauge, AutonomyReason } from './AutonomyNoteCard'
import { gaugeOf, resolveAutonomy } from './autonomyNote'
import { MONEY_CAPS, type RiskCheck, type RiskResult, type StepState } from './debate'
import { Fig, Head } from './debateParts'
import './debate.css'
import './autonomyNote.css'

/*
 * The Risk Engine's result (brief §10, design v11 Phase 2 §6): the result badge, the nine checks as
 * one pipeline in the order they ran (Pass / Resized / Failed / Not run / Awaiting you, each with its
 * reason), the final contracts, notional, leverage and margin, the cap that set the size, and the
 * settings version the check read. A rejection names the check and its reason first.
 *
 * Step 9 (Autonomy, decision 254, ATL-219) is never a plain pass: its state is send_now, advise_only
 * or awaiting_owner, and its name and reason come from AutonomyReason, built only from the check's
 * own stored values (card.autonomy), never from the mock.
 */

const STEP_ICON: Record<StepState, LucideIcon> = { pass: CircleCheck, resize: Shrink, fail: CircleX, not_run: CircleDashed, awaiting_owner: Hand, send_now: Send, advise_only: Eye }
const RESULT: Record<RiskResult, [string, LucideIcon]> = { approved: ['is-pass', CircleCheck], resized: ['is-resize', Shrink], rejected: ['is-fail', CircleX] }

export function RiskCheckCard({ card, target, onJump, ts, lang }: { card: RiskCheck; target: string | null; onJump: (id: string) => void; ts: string; lang: Lang }) {
  const { t } = useTranslation('debate')
  const { t: ta } = useTranslation('autonomy-note')
  const usd = (s: string) => formatMoney(s, 'USD', lang)
  const autonomy = card.autonomy ? resolveAutonomy(card.autonomy, 'entry') : null
  const autonomyGauge = autonomy?.gauge && card.autonomy ? gaugeOf(card.autonomy) : null
  const [cls, ResultIcon] = RESULT[card.result]
  const failed = card.failedCheck
  const failedStep = failed === null ? null : card.steps[failed - 1]
  const capSrc = card.bindingCap === null ? null : card.bindingCap === 'strategist_suggestion' ? 'strategist' : MONEY_CAPS.has(card.bindingCap) ? 'money' : 'profile'
  const capName = card.bindingCap === null ? '' : t(`rk.cap.${card.bindingCap}`, { defaultValue: card.bindingCap })

  return (
    <div className={`hv-card db-card db-risk is-${card.result}`} role="group" aria-label={t('rk.kind')} data-testid="db-risk" data-result={card.result}>
      <Head icon={ShieldCheck} kind={t('rk.kind')} version={null} target={target} onJump={onJump} ts={ts} lang={lang} />
      <div className="db-rk-result">
        <span className={`db-pill ${cls}`} data-testid="db-rk-result">
          <ResultIcon className="icon" aria-hidden="true" />
          <span>{t(`rk.${card.result}`)}</span>
        </span>
        {card.result === 'resized' && card.requestedUsd && (
          <span className="db-rk-from">{fillSlots(t('rk.from', { asked: slot('a') }), { a: <Fig>{usd(card.requestedUsd)}</Fig> })}</span>
        )}
      </div>

      {card.result === 'rejected' && failed !== null && (
        <p className="db-stopped" role="note" data-testid="db-rk-stopped">
          <CircleX className="icon" aria-hidden="true" />
          <span>
            <b>{fillSlots(t('rk.stopped', { n: slot('n') }), { n: <Fig>{String(failed)}</Fig> })}</b>{' '}
            {failedStep?.reason && (
              <span dir="auto">
                <Inline text={failedStep.reason} flat />
              </span>
            )}{' '}
            {t('rk.noOrder')}
          </span>
        </p>
      )}

      {card.contracts !== null && (
        <dl className="db-figs" data-testid="db-rk-final">
          <div>
            <dt>{t('rk.size')}</dt>
            <dd>{fillSlots(t('rk.contracts', { n: slot('n') }), { n: <Fig>{card.contracts}</Fig> })}</dd>
          </div>
          {card.notionalUsd !== null && (
            <div>
              <dt>{t('rk.notional')}</dt>
              <dd>
                <Fig>{usd(card.notionalUsd)}</Fig>
              </dd>
            </div>
          )}
          {card.leverage !== null && (
            <div>
              <dt>{t('rk.leverage')}</dt>
              <dd>
                <Fig>{`${formatFixed(card.leverage, 0, lang)}×`}</Fig>
              </dd>
            </div>
          )}
          {card.marginUsd !== null && (
            <div>
              <dt>{t('rk.margin')}</dt>
              <dd>
                <Fig>{usd(card.marginUsd)}</Fig>
              </dd>
            </div>
          )}
        </dl>
      )}

      {card.bindingCap !== null && capSrc !== null && (
        <p className="db-binding" data-testid="db-rk-binding">
          <ArrowRightToLine className="icon icon-dir" aria-hidden="true" />
          <span>
            {fillSlots(t('rk.binding', { cap: slot('cap') }), {
              cap: (
                <b>
                  {card.bindingValueUsd
                    ? fillSlots(t('rk.capValue', { cap: slot('c'), v: slot('v') }), { c: capName, v: <Fig>{usd(card.bindingValueUsd)}</Fig> })
                    : capName}
                </b>
              ),
            })}
          </span>
          <span className="db-binding-src">{t(`rk.src.${capSrc}`)}</span>
        </p>
      )}

      <ol className="db-steps" aria-label={t('rk.checks')} data-testid="db-rk-steps">
        {card.steps.map((s) => {
          const Icon = STEP_ICON[s.state]
          const isAutonomy = s.n === 9 && card.autonomy !== null && autonomy !== null
          return (
            <li key={s.n} className={`db-step is-${s.state}`} data-testid="db-rk-step" data-n={s.n} data-state={s.state}>
              <span className="db-step-n" aria-hidden="true">
                <Fig>{String(s.n)}</Fig>
              </span>
              <span className="db-step-body">
                <span className="db-step-head">
                  <span className="db-step-name">{isAutonomy ? fillSlots(ta('step', { n: slot('n') }), { n: <Fig>{String(autonomy!.level)}</Fig> }) : t(`rk.c.${s.n}`)}</span>
                  <span className="db-step-st" data-testid="db-rk-step-state">
                    <Icon className="icon" aria-hidden="true" />
                    <span>{t(`rk.st.${s.state}`)}</span>
                  </span>
                  {isAutonomy && autonomyGauge && <AutonomyGauge gauge={autonomyGauge} />}
                </span>
                <span className="db-step-why" dir="auto" data-testid={isAutonomy ? 'db-rk-autonomy-reason' : undefined}>
                  {isAutonomy ? (
                    <AutonomyReason values={card.autonomy!} resolved={autonomy!} lang={lang} />
                  ) : s.reason ? (
                    <Inline text={s.reason} flat />
                  ) : s.state === 'not_run' && failed !== null ? (
                    fillSlots(t('rk.notRunWhy', { n: slot('n') }), { n: <Fig>{String(failed)}</Fig> })
                  ) : null}
                </span>
              </span>
            </li>
          )
        })}
      </ol>

      {card.settingsVersion !== null && (
        <p className="db-foot db-rk-foot" data-testid="db-rk-settings">
          <SlidersHorizontal className="icon" aria-hidden="true" />
          <span>{fillSlots(t('rk.settings', { v: slot('v') }), { v: <Fig>{String(card.settingsVersion)}</Fig> })}</span>
        </p>
      )}
    </div>
  )
}
