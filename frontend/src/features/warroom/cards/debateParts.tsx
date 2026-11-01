import { ChevronRight, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import { formatFullTime, formatTime } from '../time'
import { pctOf, VOTE_ICON, type Vote } from './debate'

/* Shared pieces of the debate cards (design v11 Phase 2 §6): figures as LTR isolates, the card head
   with the proposal it answers, the vote chip. */

export function Fig({ children, className }: { children: string; className?: string }) {
  return (
    <bdi dir="ltr" className={`fig num${className ? ` ${className}` : ''}`}>
      {children}
    </bdi>
  )
}

/** The proposal this card answers: a link to its card when the feed has it, else plain words. */
function Ref({ version, target, onJump }: { version: number | null; target: string | null; onJump: (id: string) => void }) {
  const { t } = useTranslation('debate')
  const words = version === null ? t('refPlain') : fillSlots(t('ref', { v: slot('v') }), { v: <Fig>{String(version)}</Fig> })
  if (!target) return <span className="db-ref is-plain">{words}</span>
  return (
    <a
      className="db-ref"
      href={`#${target}`}
      aria-label={version === null ? t('refPlainAria') : t('refAria', { v: version })}
      onClick={(e) => {
        e.preventDefault()
        onJump(target)
      }}
      data-testid="db-ref"
    >
      <span>{words}</span>
      <ChevronRight className="icon icon-dir" aria-hidden="true" />
    </a>
  )
}

export function Head({
  icon: Icon,
  kind,
  version,
  target,
  onJump,
  ts,
  lang,
}: {
  icon: LucideIcon
  kind: string
  version: number | null
  target: string | null
  onJump: (id: string) => void
  ts: string
  lang: Lang
}) {
  return (
    <div className="hv-head db-head">
      <span className="hv-tile">
        <Icon aria-hidden="true" strokeWidth={1.5} />
      </span>
      <span className="hv-kind">{kind}</span>
      <Ref version={version} target={target} onJump={onJump} />
      <time className="hv-asof num" dateTime={ts} title={formatFullTime(ts, lang)}>
        {formatTime(ts, lang)}
      </time>
    </div>
  )
}

export function VoteChip({ vote, confidence, lang }: { vote: Vote; confidence: number | null; lang: Lang }) {
  const { t } = useTranslation('debate')
  const Icon = VOTE_ICON[vote]
  const word = t(`v.${vote}`)
  const p = confidence === null ? null : pctOf(confidence, lang)
  const label: ReactNode = p === null ? null : fillSlots(t('conf', { p: slot('p') }), { p: <Fig>{p}</Fig> })
  return (
    <span className={`db-vote is-${vote}`} role="img" aria-label={p === null ? word : t('voteAria', { vote: word, p })} data-testid="db-vote">
      <Icon className="icon" aria-hidden="true" />
      <b aria-hidden="true">{word}</b>
      {label && <span aria-hidden="true">{label}</span>}
    </span>
  )
}
