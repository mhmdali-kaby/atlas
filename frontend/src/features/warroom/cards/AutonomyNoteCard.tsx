import { ChevronRight, CircleDashed, CirclePause, Eye, Hand, Lock, Send, type LucideIcon } from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatMoney, type Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import { formatTime } from '../time'
import {
  gaugeOf,
  resolveAutonomy,
  type AutonomyGauge as Gauge,
  type AutonomyNoteValues,
  type AutonomyResolved,
  type ProposalActionKind,
} from './autonomyNote'
import { Fig } from './debateParts'
import './autonomyNote.css'

/*
 * AutonomyNote (decision 254, ATL-212/213, design v18 §4.6): the War Room's one explanation of
 * every autonomy decision, built only from the risk check's own stored autonomy values and the
 * proposal's kind. Used on the risk check card's Autonomy step (RiskCheckCard, via AutonomyReason
 * and AutonomyGauge below) and as the full note on the proposal card and the Portfolio Manager's
 * action card (PmActionCard).
 */

const ICON: Record<string, LucideIcon> = { send: Send, hand: Hand, eye: Eye, 'circle-pause': CirclePause, 'circle-dashed': CircleDashed, lock: Lock }

const money = (v: string | null, lang: Lang) => formatMoney(v ?? '0', 'USD', lang)

/** The reason sentence alone (no lead), as the risk check step shows it. */
export function AutonomyReason({ values, resolved, lang }: { values: AutonomyNoteValues; resolved: AutonomyResolved; lang: Lang }) {
  const { t } = useTranslation('autonomy-note')
  const str = t(resolved.key, { notional: slot('notional'), ask: slot('ask'), n: slot('n') })
  return (
    <span dir="auto">
      {fillSlots(str, {
        notional: <Fig>{money(values.notionalUsd, lang)}</Fig>,
        ask: <Fig>{money(values.askAboveUsd, lang)}</Fig>,
        n: <Fig>{String(resolved.level)}</Fig>,
      })}
    </span>
  )
}

/** The mini ask line (aria-hidden; the sentence beside it carries the figures). */
export function AutonomyGauge({ gauge }: { gauge: Gauge }) {
  const style = { '--cut': gauge.cut.toFixed(4), '--at': gauge.at.toFixed(4) } as CSSProperties
  return (
    <span className={`au-gauge ${gauge.above ? 'is-above' : 'is-within'}`} aria-hidden="true" style={style} data-testid="au-gauge">
      <span className="au-gauge-track">
        <span className="au-gauge-auto" />
      </span>
      <span className="au-gauge-tick" />
      <span className="au-gauge-dot" />
    </span>
  )
}

export interface AutonomySent {
  time: string | null
  price?: string | null
}

/** The full note: icon, lead, reason, the mini gauge, an optional "what happened" line, and the
 *  link to Settings → Autonomy. `kind` is the proposal's kind (entry by default). */
export function AutonomyNoteCard({
  values,
  kind = 'entry',
  sent,
  lang,
  className = '',
}: {
  values: AutonomyNoteValues
  kind?: ProposalActionKind
  sent?: AutonomySent | null
  lang: Lang
  className?: string
}) {
  const { t } = useTranslation('autonomy-note')
  const o = resolveAutonomy(values, kind)
  const Icon = ICON[o.icon] ?? Hand
  const leadKey = o.key === 'notEnabled' ? 'notEnabled' : o.decision
  const lead: ReactNode = fillSlots(t(`lead.${leadKey}`, { n: slot('n') }), { n: <Fig>{String(o.level)}</Fig> })
  const gauge = o.gauge ? gaugeOf(values) : null
  const sentLine: ReactNode =
    sent && o.decision === 'send_now'
      ? fillSlots(t(`sent.${kind}`, { time: slot('time'), price: slot('price') }), {
          time: <Fig>{sent.time ? formatTime(sent.time, lang) : '—'}</Fig>,
          price: <Fig>{sent.price ?? ''}</Fig>,
        })
      : null
  return (
    <div className={`au-note is-${o.decision} ${o.halted ? 'is-halted' : ''} ${className}`} role="note" data-testid="au-note" data-au-note={o.key}>
      <span className="au-note-ic">
        <Icon className="icon" aria-hidden="true" />
      </span>
      <p className="au-note-text">
        <b>{lead}</b> <AutonomyReason values={values} resolved={o} lang={lang} />
      </p>
      {gauge && <AutonomyGauge gauge={gauge} />}
      <p className="au-note-meta">
        {sentLine && <>{sentLine} </>}
        <Link className="pr-link au-note-link" to="/settings/autonomy">
          <span>{t('link')}</span>
          <ChevronRight className="icon icon-dir" aria-hidden="true" />
        </Link>
      </p>
    </div>
  )
}

export { resolveAutonomy }
export type { AutonomyNoteValues, AutonomyResolved, ProposalActionKind }
