import { Check, CircleCheck, CircleX, Hourglass, LogOut, MessageSquareText, Send, ShieldCheck, TrendingDown, TrendingUp, X, type LucideIcon } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../../lib/api'
import { formatFixed, formatMoney, type Lang } from '../../../lib/format'
import { Button } from '../../../ui'
import { useNow } from '../../portfolio/hooks'
import { fillSlots, slot } from '../slots'
import { InstrumentId, Markdown } from '../text'
import { formatTime } from '../time'
import { AutonomyNoteCard } from './AutonomyNoteCard'
import { LiveOrderSheet, needsLiveConfirm } from './LiveOrderSheet'
import { Band } from './ProposalCard'
import { ReasonSheet } from './ProposalSheets'
import {
  autonomyValuesOf,
  decimalsOf,
  directionOf,
  mergeSummary,
  proposalApi,
  proposalKey,
  refusalOf,
  shownStatus,
  useProposal,
  type ProposalCardRef,
  type ProposalDetail,
  type ShownStatus,
} from './proposal'
import { useReadOnly } from '../readOnly'
import './proposal.css'
import './autonomyNote.css'

/*
 * The Portfolio Manager's action card (brief §7.8, ATL-169/184/185; design v18 §4.6 "The Portfolio
 * Manager's action card"): a stop change, a target change, a partial close or a close. Compact,
 * built the same way as the entry ProposalCard — the owner's approval moment when it waits, the
 * AutonomyNote when autonomy decided it (its own check 7, on the whole position's notional for a
 * stop or target change), Approve / Reject / Ask the team.
 */

const KIND_ICON: Record<string, LucideIcon> = { amend_stop: ShieldCheck, amend_targets: ShieldCheck, close: LogOut, partial_close: LogOut }
const STATUS_ICON: Record<ShownStatus, LucideIcon> = {
  awaiting_owner: ShieldCheck,
  advice: ShieldCheck,
  sent: Send,
  partially_filled: CircleCheck,
  filled: CircleCheck,
  closed: CircleCheck,
  rejected: CircleX,
  expired: Hourglass,
  withdrawn: CircleX,
  replaced: CircleX,
  under_review: ShieldCheck,
  voting: ShieldCheck,
  passed: ShieldCheck,
  risk_checked: ShieldCheck,
}

function Fig({ children }: { children: string }) {
  return <bdi dir="ltr" className="num fig">{children}</bdi>
}

const shortId = (id: string) => id.slice(0, 8)

type Sheet = 'reject' | 'ask' | 'live' | null

export function PmActionCard({ cardRef, lang }: { cardRef: ProposalCardRef; lang: Lang; onJump: (id: string) => void }) {
  const { t } = useTranslation('proposal')
  const qc = useQueryClient()
  const readOnly = useReadOnly()
  const base = useProposal(cardRef.proposalId, !readOnly)
  const p: ProposalDetail = readOnly ? cardRef.snapshot : (base.data ?? cardRef.snapshot)
  const live = Boolean(base.data) && !readOnly
  const now = useNow(1000)
  const shown = shownStatus(p, now)
  const [busy, setBusy] = useState<'approve' | false>(false)
  const [sheet, setSheet] = useState<Sheet>(null)

  const refetch = () => void qc.invalidateQueries({ queryKey: proposalKey(p.id) })

  const dir = directionOf(p.side)
  const DirIcon = dir === 'short' ? TrendingDown : TrendingUp
  const d = decimalsOf(p)
  const P = (v: string | null) => (v === null ? '—' : formatFixed(v, d, lang))
  const money = (v: string | null) => (v === null ? '—' : formatMoney(v, 'USD', lang))
  const kind = p.kind === 'amend_targets' || p.kind === 'close' || p.kind === 'partial_close' ? p.kind : 'amend_stop'
  const KindIcon = KIND_ICON[kind] ?? ShieldCheck
  const StatusIcon = STATUS_ICON[shown]
  const autonomyValues = live ? autonomyValuesOf(p.risk_check) : null
  const sentByAutonomy = shown === 'sent' && Boolean(p.decided_by?.startsWith('autonomy'))
  const awaiting = live && shown === 'awaiting_owner'
  const statusWord = t(`st.${shown}`)
  const title = `${t(`pm.kind.${kind}`)} ${shortId(p.id)}`
  const label = `${title}, ${sentByAutonomy ? t('st.autoSent') : statusWord}`

  const expiresAt = p.status === 'awaiting_owner' && p.approval_expires_at ? Date.parse(p.approval_expires_at) : null
  const left = expiresAt === null ? null : expiresAt - now

  const reason409 = (err: unknown, titleText: string) => {
    const { code, message } = refusalOf(err)
    const known = code ? t(`why409.${code}`, { defaultValue: '' }) : ''
    return { title: titleText, body: known || message || t('why409.unknown') }
  }
  const [conflict, setConflict] = useState<{ title: string; body: string } | null>(null)

  // Live (ATL-240): the real-order sheet first; only its confirm sends live_confirmed: true.
  const isLiveProposal = needsLiveConfirm(p)
  const approve = async (liveConfirmed = false) => {
    if (busy) return
    if (isLiveProposal && !liveConfirmed) {
      setSheet('live')
      return
    }
    setSheet(null)
    setBusy('approve')
    setConflict(null)
    try {
      const res = await proposalApi.approve(p.id, { liveConfirmed: isLiveProposal })
      if (res.proposal) qc.setQueryData<ProposalDetail>(proposalKey(p.id), (old) => (old ? mergeSummary(old, res.proposal!) : old))
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(reason409(err, t('c409.title')))
      else setConflict({ title: t('t.failed'), body: refusalOf(err).message ?? t('t.error') })
    } finally {
      setBusy(false)
      refetch()
    }
  }

  const what = (): ReactNode => {
    if (kind === 'amend_stop') return fillSlots(t('pm.what.amend_stop', { stop: slot('stop') }), { stop: <Fig>{P(p.stop)}</Fig> })
    if (kind === 'amend_targets') {
      const list = p.take_profits
        .filter((x) => x.price !== null)
        .map((x) => P(x.price))
        .join(', ')
      return fillSlots(t('pm.what.amend_targets', { list: slot('list') }), { list: <Fig>{list || '—'}</Fig> })
    }
    const contracts = p.risk_check?.final_contracts
    const notional = p.risk_check?.final_notional_usd
    if (kind === 'close' && contracts && notional) return fillSlots(t('pm.what.closeSized', { c: slot('c'), notional: slot('notional') }), { c: <Fig>{contracts}</Fig>, notional: <Fig>{money(notional)}</Fig> })
    if (kind === 'close') return t('pm.what.close')
    return fillSlots(t('pm.what.partial_close', { pct: slot('pct') }), { pct: <Fig>{formatFixed(p.close_pct ?? '0', 0, lang)}</Fig> })
  }

  return (
    <div className={`pr-card is-compact is-pm is-${shown}`} role="group" aria-label={label} data-testid="pm-prop-card" data-status={shown} data-proposal-id={p.id}>
      <div className="hv-head pr-head">
        <span className="hv-tile">
          <KindIcon aria-hidden="true" strokeWidth={1.5} className={kind === 'close' || kind === 'partial_close' ? 'icon icon-dir' : undefined} />
        </span>
        <span className="hv-kind">{title}</span>
        {sentByAutonomy ? (
          <span className="pr-status is-sent is-auto" data-testid="pr-status">
            <Send className="icon" aria-hidden="true" />
            <span>{t('st.autoSent')}</span>
          </span>
        ) : (
          <span className={`pr-status is-${shown}`} data-testid="pr-status">
            <StatusIcon className="icon" aria-hidden="true" />
            <span>{statusWord}</span>
          </span>
        )}
      </div>

      <div className="pr-title is-small">
        <span className={`pr-dir is-${dir}`} data-testid="pr-dir">
          <DirIcon className="icon" aria-hidden="true" />
          <span>{t(`dir.${dir}`)}</span>
        </span>
        <InstrumentId id={p.inst_id} />
      </div>

      <p className="pr-pm-what">{what()}</p>
      {p.rationale_md && (
        <div dir="auto" className="pr-pm-sub">
          <Markdown text={p.rationale_md} />
        </div>
      )}

      {conflict && (
        <div className="pr-notice" role="alert" data-testid="pr-conflict">
          <p className="pr-notice-title">{conflict.title}</p>
          <p className="pr-notice-body" dir="auto">
            {conflict.body}
          </p>
        </div>
      )}

      {awaiting && expiresAt !== null && left !== null && <Band expiresAt={expiresAt} left={left} createdAt={p.risk_check?.created_at ?? p.created_at} lang={lang} />}

      {live && autonomyValues !== null && (sentByAutonomy || (awaiting && autonomyValues.autonomyLevel >= 3)) && <AutonomyNoteCard values={autonomyValues} kind={kind} sent={sentByAutonomy ? { time: p.decided_at } : null} lang={lang} />}

      {live && shown === 'sent' && !sentByAutonomy && p.decided_by === 'owner' && (
        <p className="pr-outcome is-done" role="status" data-testid="pr-outcome">
          <CircleCheck className="icon" aria-hidden="true" />
          <span>{fillSlots(t('pm.byYou', { time: slot('time') }), { time: <Fig>{p.decided_at ? formatTime(p.decided_at, lang) : '—'}</Fig> })}</span>
        </p>
      )}
      {live && shown === 'rejected' && (
        <p className="pr-outcome" role="status" data-testid="pr-outcome">
          <CircleX className="icon" aria-hidden="true" />
          <span>
            {fillSlots(t('l.rejected', { time: slot('time') }), { time: <Fig>{p.decided_at ? formatTime(p.decided_at, lang) : '—'}</Fig> })}{' '}
            {p.owner_note && <q dir="auto">{p.owner_note}</q>}
          </span>
        </p>
      )}
      {live && shown === 'expired' && (
        <p className="pr-outcome is-warn" role="status" data-testid="pr-outcome">
          <Hourglass className="icon" aria-hidden="true" />
          <span>{fillSlots(t('l.expired', { time: slot('time') }), { time: <Fig>{p.approval_expires_at ? formatTime(p.approval_expires_at, lang) : '—'}</Fig> })}</span>
        </p>
      )}

      {awaiting && (
        <div className="pr-actions" role="group" aria-label={t('actionsSr')} data-testid="pr-actions">
          <Button variant="primary" size="sm" onClick={() => void approve()} disabled={busy !== false || !p.actions.approve} icon={<Check aria-hidden="true" strokeWidth={1.5} />} loading={busy === 'approve'} data-testid="pr-approve">
            {busy === 'approve' ? t('sending') : t('approve')}
          </Button>
          <Button size="sm" icon={<X aria-hidden="true" strokeWidth={1.5} />} onClick={() => setSheet('reject')} disabled={busy !== false || !p.actions.reject} data-testid="pr-reject">
            {t('reject')}
          </Button>
          <span className="pr-actions-gap" />
          <Button variant="plain" size="sm" icon={<MessageSquareText aria-hidden="true" strokeWidth={1.5} />} onClick={() => setSheet('ask')} disabled={busy !== false} data-testid="pr-ask">
            {t('ask')}
          </Button>
        </div>
      )}

      {live && (
        <>
          {isLiveProposal && (
            <LiveOrderSheet
              p={p}
              open={sheet === 'live'}
              onOpenChange={(o) => setSheet(o ? 'live' : null)}
              onConfirm={() => void approve(true)}
              lang={lang}
              band={expiresAt !== null && left !== null ? <Band expiresAt={expiresAt} left={left} createdAt={p.risk_check?.created_at ?? p.created_at} lang={lang} /> : undefined}
            />
          )}
          <ReasonSheet
            kind="reject"
            open={sheet === 'reject'}
            onOpenChange={(o) => setSheet(o ? 'reject' : null)}
            onSubmit={async (reason) => {
              try {
                const res = await proposalApi.reject(p.id, reason)
                qc.setQueryData<ProposalDetail>(proposalKey(p.id), (old) => (old ? mergeSummary(old, res.proposal) : old))
              } catch {
                // the card refetches below regardless
              } finally {
                setSheet(null)
                refetch()
              }
            }}
          />
          <ReasonSheet
            kind="ask"
            open={sheet === 'ask'}
            onOpenChange={(o) => setSheet(o ? 'ask' : null)}
            onSubmit={async (question) => {
              try {
                await proposalApi.askTeam(p.id, question)
              } finally {
                setSheet(null)
              }
            }}
          />
        </>
      )}
    </div>
  )
}
