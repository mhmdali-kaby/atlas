import {
  ChartCandlestick,
  Check,
  ChessKnight,
  ChevronRight,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleX,
  Eye,
  FileText,
  Hand,
  Hourglass,
  Info,
  MessageSquareText,
  Newspaper,
  Pencil,
  Radar,
  Replace,
  Send,
  ShieldAlert,
  Sigma,
  Timer,
  TrendingDown,
  TrendingUp,
  Undo2,
  Users,
  Briefcase,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { ApiError } from '../../../lib/api'
import { formatDecimal, formatFixed, formatMoney, type Lang } from '../../../lib/format'
import { Button, toast } from '../../../ui'
import { useNow } from '../../portfolio/hooks'
import { Medallion } from '../Medallion'
import { fillSlots, slot } from '../slots'
import { InstrumentId, Inline, Markdown } from '../text'
import { formatTime, mmss } from '../time'
import { dec, decStr, div } from './dec'
import { Ladder } from './Ladder'
import {
  daOf,
  decimalsOf,
  evidenceChips,
  directionOf,
  entryOrderOf,
  followOwnerEdits,
  freshMark,
  MARK_LIVE,
  proposalApi,
  proposalKey,
  readBasis,
  refusalOf,
  replacedBy,
  shownStatus,
  liqOf,
  mergeSummary,
  stakeOf,
  useProposal,
  useProposalMark,
  zoneOf,
  type ProposalCardRef,
  type EvidenceChip,
  type EvidenceLabel,
  type ProposalDetail,
  type ShownStatus,
} from './proposal'
import { EditSheet, ReasonSheet } from './ProposalSheets'
import { rText } from './rText'
import './proposal.css'

/*
 * The War Room proposal card (brief §12 and §7.9 step 5, design v11 wr-proposal): the owner's
 * approval moment. Direction and instrument as the headline, the deadline band while it waits for
 * the owner, the trade ladder, the money at stake after the risk check, the odds and plan, why,
 * the Devil's Advocate's strongest objection with its rating, and Approve / Reject / Edit / Ask the
 * team. The card reads GET /api/proposals/{id} and refetches on every `proposal.*` broadcast, so
 * the buttons leave as soon as the status leaves awaiting_owner, in this tab or another.
 */

const STATUS_ICON: Record<ShownStatus, LucideIcon> = {
  awaiting_owner: Hand,
  advice: Eye,
  sent: Send,
  partially_filled: CircleDashed,
  filled: CircleCheck,
  closed: CircleCheck,
  rejected: CircleX,
  expired: Hourglass,
  withdrawn: Undo2,
  replaced: Replace,
  under_review: CircleDot,
  voting: CircleDot,
  passed: CircleDot,
  risk_checked: CircleDot,
}

function Fig({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={`num fig ${className}`}>
      {children}
    </bdi>
  )
}

/** "+1.2R" for a target, from the geometry: (price − mid entry) / (mid entry − stop). */
export function targetR(price: string, zone: [string, string], stop: string): string | null {
  const p = dec(price)
  const lo = dec(zone[0])
  const hi = dec(zone[1])
  const s = dec(stop)
  if (p === null || lo === null || hi === null || s === null) return null
  const mid = (lo + hi) / 2n
  if (mid === s) return null
  return decStr(div(p - mid, mid - s), 4)
}

/** A time stop in hours: "24", "1.5". */
function hoursText(minutes: number, lang: Lang): string {
  return formatFixed(String(minutes / 60), minutes % 60 === 0 ? 0 : 1, lang)
}

export interface Conflict {
  title: string
  body: string
}

type Sheet = 'reject' | 'edit' | 'ask' | null

export function ProposalCard({ cardRef, lang, onJump }: { cardRef: ProposalCardRef; lang: Lang; onJump: (id: string) => void }) {
  const { t } = useTranslation('proposal')
  const qc = useQueryClient()
  const base = useProposal(cardRef.proposalId)
  const follow = base.data ? followOwnerEdits(base.data) : null
  const edit = useProposal(follow?.edited ? follow.id : '')
  const loaded: ProposalDetail | undefined = follow?.edited ? (edit.data ?? undefined) : base.data
  const p: ProposalDetail = loaded ?? base.data ?? cardRef.snapshot
  const live = Boolean(loaded)
  const now = useNow(1000)
  const shown = shownStatus(p, now)
  // The price now (ATL-188): polled every 10 s while the card is on screen and the trade is still
  // ahead or under way; shown only while at most 30 s old, judged on this clock tick.
  const markLive = live && MARK_LIVE.has(shown)
  const markQ = useProposalMark(p.id, markLive)
  const mark = markLive ? freshMark([p, markQ.data], now) : null
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const [sheet, setSheet] = useState<Sheet>(null)
  const [announce, setAnnounce] = useState('')
  const said = useRef({ oneMinute: false, expired: false })

  const refetch = () => {
    void qc.invalidateQueries({ queryKey: proposalKey(cardRef.proposalId) })
    if (p.id !== cardRef.proposalId) void qc.invalidateQueries({ queryKey: proposalKey(p.id) })
  }

  // The deadline passing while the owner watches: say so once, and ask the server what it did.
  const expiresAt = p.status === 'awaiting_owner' && p.approval_expires_at ? Date.parse(p.approval_expires_at) : null
  const left = expiresAt === null ? null : expiresAt - now
  useEffect(() => {
    if (left === null) return
    if (left <= 60_000 && left > 0 && !said.current.oneMinute) {
      said.current.oneMinute = true
      setAnnounce(t('oneMinute'))
    }
    if (left <= 0 && !said.current.expired) {
      said.current.expired = true
      setAnnounce(t('expiredLive'))
      void qc.invalidateQueries({ queryKey: proposalKey(p.id) })
    }
  }, [left, p.id, qc, t])

  const reason409 = (err: unknown, title: string): Conflict => {
    const { code, message } = refusalOf(err)
    const known = code ? t(`why409.${code}`, { defaultValue: '' }) : ''
    return { title, body: known || message || t('why409.unknown') }
  }

  const approve = async () => {
    if (busy) return
    setBusy(true)
    setConflict(null)
    try {
      const res = await proposalApi.approve(p.id)
      const next = res.proposal
      if (next) qc.setQueryData<ProposalDetail>(proposalKey(p.id), (old) => (old ? mergeSummary(old, next) : old))
      toast({ title: t('t.sent'), body: t('t.sentBody', { inst: p.inst_id }) })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setConflict(reason409(err, t('c409.title')))
      else {
        const { message } = refusalOf(err)
        setConflict({ title: t('t.failed'), body: message ?? t('t.error') })
      }
    } finally {
      setBusy(false)
      refetch()
    }
  }

  const dir = directionOf(p.side)
  const DirIcon = dir === 'short' ? TrendingDown : TrendingUp
  const StatusIcon = STATUS_ICON[shown]
  const zone = zoneOf(p)
  const d = decimalsOf(p)
  const P = (v: string | null) => (v === null ? '—' : formatFixed(v, d, lang))
  const money = (v: string | null) => formatMoney(v, 'USD', lang)
  const pct = (v: string | null, digits = 2) => (v === null ? '—' : `${formatFixed(v, digits, lang)}%`)
  const stake = stakeOf(p)
  const basis = readBasis(p.probability_basis)
  const da = live ? daOf(p) : null
  const chips = live ? evidenceChips(p) : []
  const statusWord = t(`st.${shown}`)
  const awaiting = live && shown === 'awaiting_owner'
  const advice = live && shown === 'advice'
  const label = t('aria', { dir: t(`dir.${dir}`), inst: p.inst_id, v: p.version, status: statusWord })
  const targets = p.take_profits.filter((x): x is { price: string; pct: string | null } => x.price !== null)

  return (
    <div className={`pr-card is-${shown}`} role="group" aria-label={label} data-testid="pr-card" data-status={shown} data-proposal-id={p.id}>
      <div className="hv-head pr-head">
        <span className="hv-tile">
          <ChessKnight aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{fillSlots(t('title', { v: slot('v') }), { v: <Fig>{String(p.version)}</Fig> })}</span>
        <span className={`pr-status is-${shown}`} data-testid="pr-status">
          <StatusIcon className="icon" aria-hidden="true" />
          <span>{statusWord}</span>
        </span>
      </div>

      <div className="pr-title">
        <span className={`pr-dir is-${dir}`} data-testid="pr-dir">
          <span className="sr-only">{t('dirSr')}: </span>
          <DirIcon className="icon" aria-hidden="true" />
          <span>{t(`dir.${dir}`)}</span>
        </span>
        <InstrumentId id={p.inst_id} />
      </div>

      {follow?.edited && live && (
        <p className="pr-edited" data-testid="pr-edited">
          <Pencil className="icon" aria-hidden="true" />
          <span>{fillSlots(t('l.edited', { v: slot('v'), time: slot('time') }), { v: <Fig>{String(p.version)}</Fig>, time: <Fig>{formatTime(p.risk_check?.created_at ?? p.created_at ?? now, lang)}</Fig> })}</span>
        </p>
      )}

      {conflict && (
        <div className="pr-notice" role="alert" data-testid="pr-conflict">
          <Info className="icon" aria-hidden="true" />
          <div>
            <p className="pr-notice-title">{conflict.title}</p>
            <p className="pr-notice-body" dir="auto">
              {conflict.body}
            </p>
          </div>
        </div>
      )}

      {awaiting && expiresAt !== null && left !== null && <Band expiresAt={expiresAt} left={left} createdAt={p.risk_check?.created_at ?? p.created_at} lang={lang} />}

      <Outcome p={p} shown={shown} live={live} lang={lang} P={P} />

      {zone && p.stop && (
        <div className="pr-ladder">
          <div className="pr-ladder-full">
            <Ladder side={dir} entry={zone} stop={p.stop} targets={targets.map((x) => ({ price: x.price, pct: x.pct }))} live={markLive} mark={mark} liq={liqOf(p)} decimals={d} lang={lang} />
          </div>
          <div className="pr-ladder-compact">
            <Ladder side={dir} entry={zone} stop={p.stop} targets={targets.map((x) => ({ price: x.price, pct: x.pct }))} live={markLive} mark={mark} decimals={d} lang={lang} compact nowOnly />
            <ul className="pr-tps" aria-label={t('tpsSr')}>
              {targets.map((x, i) => {
                const r = targetR(x.price, zone, p.stop!)
                return (
                  <li key={i} className="is-tp">
                    <span className="pr-tps-k">{`TP${i + 1}`}</span>
                    <Fig>{P(x.price)}</Fig>
                    <span className="pr-tps-s">{fillSlots(t('tpRow', { pct: slot('pct'), r: slot('r') }), { pct: <Fig>{pct(x.pct, 0)}</Fig>, r: <Fig>{r ? rText(r) : '—'}</Fig> })}</span>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}

      <div className="pr-stake" data-testid="pr-stake">
        {stake.risk !== null && (
          <div className="pr-stake-item is-risk">
            <span className="pr-stake-k">{t('atRisk')}</span>
            <span className="pr-stake-v" data-testid="pr-risk">
              <Fig>{money(stake.risk)}</Fig>
            </span>
            {stake.riskPct !== null && <span className="pr-stake-s">{fillSlots(t('atRiskSub', { pct: slot('pct') }), { pct: <Fig>{pct(stake.riskPct)}</Fig> })}</span>}
          </div>
        )}
        <div className="pr-stake-item">
          <span className="pr-stake-k">{t('position')}</span>
          {stake.notional !== null ? (
            <>
              <span className="pr-stake-v" data-testid="pr-notional">
                <Fig>{money(stake.notional)}</Fig>
              </span>
              <span className="pr-stake-s" data-testid="pr-size">
                {fillSlots(t('positionSub', { c: slot('c'), lev: slot('lev'), margin: slot('margin') }), {
                  c: <Fig>{stake.contracts ?? '—'}</Fig>,
                  lev: <Fig>{`${stake.leverage ?? p.leverage_suggestion ?? 1}×`}</Fig>,
                  margin: <Fig>{money(stake.margin)}</Fig>,
                })}
              </span>
            </>
          ) : (
            <span className="pr-stake-s" data-testid="pr-size">
              {fillSlots(t('positionSuggest', { pct: slot('pct'), lev: slot('lev') }), { pct: <Fig>{pct(p.size_suggestion_pct, 1)}</Fig>, lev: <Fig>{`${p.leverage_suggestion ?? 1}×`}</Fig> })}
            </span>
          )}
        </div>
      </div>

      <dl className="pr-facts">
        {p.probability !== null && (
          <div className="pr-fact">
            <dt>{t('odds')}</dt>
            <dd>
              <span className="pr-fact-v" data-testid="pr-odds">
                {fillSlots(t('oddsV', { p: slot('p') }), { p: <Fig>{`${formatFixed(decStr(dec(p.probability)! * 100n, 0), 0, lang)}%`}</Fig> })}
              </span>
              {basis && (
                <span className="pr-fact-note" data-testid="pr-odds-basis">
                  {fillSlots(t('oddsBasis', { rate: slot('rate'), adj: slot('adj'), reason: slot('reason') }), {
                    rate: <Fig>{`${formatFixed(decStr(dec(basis.analog_hit_rate)! * 100n, 0), 0, lang)}%`}</Fig>,
                    adj: <Fig>{formatDecimal(basis.adjustment_pts, lang, 0, 'exceptZero')}</Fig>,
                    // Model text: a text node, isolated so its direction cannot reorder the line.
                    reason: <bdi dir="auto">{basis.reason}</bdi>,
                  })}
                </span>
              )}
            </dd>
          </div>
        )}
        {p.expected_r !== null && (
          <div className="pr-fact">
            <dt>{t('plan')}</dt>
            <dd>
              <span className="pr-fact-v">
                {fillSlots(t(p.time_stop_minutes ? 'planV' : 'planNoTime', { r: slot('r'), order: slot('order'), time: slot('time') }), {
                  r: <Fig>{`${formatFixed(p.expected_r, 2, lang)}R`}</Fig>,
                  order: t(`order.${p.order_type ?? 'limit'}`, { defaultValue: p.order_type ?? '' }),
                  time: <Fig>{t('hours', { n: hoursText(p.time_stop_minutes ?? 0, lang) })}</Fig>,
                })}
              </span>
            </dd>
          </div>
        )}
      </dl>

      {(p.rationale_md || da) && (
        <div className="pr-reasons">
          {p.rationale_md && (
            <div className="pr-why">
              <span className="pr-k">{t('why')}</span>
              <div dir="auto" data-testid="pr-why">
                <Markdown text={p.rationale_md} />
              </div>
            </div>
          )}
          {da && (da.objection || da.rating !== null) && <DaBlock da={da} onJump={onJump} />}
        </div>
      )}

      {chips.length > 0 && <Evidence chips={chips} onJump={onJump} />}

      {awaiting && (
        <div className="pr-actions" role="group" aria-label={t('actionsSr')} data-testid="pr-actions">
          <Button
            variant="primary"
            size="sm"
            className="pr-approve"
            onClick={() => void approve()}
            disabled={busy || !p.actions.approve}
            icon={<Check aria-hidden="true" strokeWidth={1.5} />}
            loading={busy}
            title={p.actions.approve_blocked_reason ? t(`why409.${p.actions.approve_blocked_reason}`, { defaultValue: '' }) || undefined : undefined}
            data-testid="pr-approve"
          >
            {busy ? t('sending') : t('approve')}
          </Button>
          <Button size="sm" icon={<X aria-hidden="true" strokeWidth={1.5} />} onClick={() => setSheet('reject')} disabled={busy || !p.actions.reject} data-testid="pr-reject">
            {t('reject')}
          </Button>
          <Button size="sm" icon={<Pencil aria-hidden="true" strokeWidth={1.5} />} onClick={() => setSheet('edit')} disabled={busy || !p.actions.edit} data-testid="pr-edit">
            {t('edit')}
          </Button>
          <span className="pr-actions-gap" />
          <Button variant="plain" size="sm" icon={<MessageSquareText aria-hidden="true" strokeWidth={1.5} />} onClick={() => setSheet('ask')} disabled={busy} data-testid="pr-ask">
            {t('ask')}
          </Button>
        </div>
      )}
      {advice && (
        <div className="pr-actions" role="group" aria-label={t('actionsSr')} data-testid="pr-actions">
          <Button size="sm" onClick={() => setSheet('ask')} data-testid="pr-ask">
            {t('ask')}
          </Button>
        </div>
      )}

      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      {live && (
        <>
          <ReasonSheet
            kind="reject"
            open={sheet === 'reject'}
            onOpenChange={(o) => setSheet(o ? 'reject' : null)}
            onSubmit={async (reason) => {
              try {
                const res = await proposalApi.reject(p.id, reason)
                qc.setQueryData<ProposalDetail>(proposalKey(p.id), (old) => (old ? mergeSummary(old, res.proposal) : old))
                toast({ title: t('t.rejected'), body: t('t.rejectedBody') })
              } catch (err) {
                setConflict(err instanceof ApiError && err.status === 409 ? reason409(err, t('c409.titleReject')) : { title: t('c409.titleReject'), body: refusalOf(err).message ?? t('t.error') })
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
                toast({ title: t('t.asked'), body: t('t.askedBody') })
              } catch (err) {
                toast({ tone: 'error', title: t('t.error'), body: refusalOf(err).message ?? undefined })
              } finally {
                setSheet(null)
              }
            }}
          />
          {sheet === 'edit' && zone && p.stop && (
            <EditSheet
              proposal={p}
              zone={zone}
              stake={stake}
              mark={mark}
              live={markLive}
              decimals={d}
              lang={lang}
              onClose={() => setSheet(null)}
              onConflict={(err) => {
                setSheet(null)
                setConflict(reason409(err, t('c409.titleEdit')))
                refetch()
              }}
              onSaved={(next) => {
                setSheet(null)
                qc.setQueryData(proposalKey(next.id), next)
                refetch()
                const passed = next.status === 'awaiting_owner'
                toast(
                  passed
                    ? { title: t('t.edited', { v: next.version }), body: next.approval_expires_at ? t('t.editedBody', { time: formatTime(next.approval_expires_at, lang) }) : undefined }
                    : { tone: 'warning', title: t('t.editRejected', { v: next.version }), body: next.risk_check?.reason ?? undefined },
                )
              }}
            />
          )}
        </>
      )}
    </div>
  )
}

function Band({ expiresAt, left, createdAt, lang }: { expiresAt: number; left: number; createdAt: string | null; lang: Lang }) {
  const { t } = useTranslation('proposal')
  const opened = createdAt ? Date.parse(createdAt) : expiresAt - 15 * 60_000
  const total = Math.max(1, expiresAt - opened)
  const from = Math.max(0, Math.min(1, left / total))
  const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  return (
    <div className={`pr-band${left < 60_000 ? ' is-last' : ''}`} data-testid="pr-band">
      <span className="pr-band-ic" aria-hidden="true">
        <Timer className="icon" />
      </span>
      <span className="pr-band-t">{fillSlots(t('by', { time: slot('time') }), { time: <Fig>{formatTime(expiresAt, lang)}</Fig> })}</span>
      <span className="pr-left" role="timer" aria-label={t('timerSr')} data-testid="pr-countdown">
        {fillSlots(t('left', { t: slot('t') }), { t: <Fig>{mmss(left)}</Fig> })}
      </span>
      {!reduce && <i className="pr-drain" aria-hidden="true" style={{ '--from': from.toFixed(4), animationDuration: `${Math.max(0, left)}ms` } as CSSProperties} />}
    </div>
  )
}

function Outcome({ p, shown, live, lang, P }: { p: ProposalDetail; shown: ShownStatus; live: boolean; lang: Lang; P: (v: string | null) => string }) {
  const { t } = useTranslation('proposal')
  if (!live) return null
  const at = (ts: string | null | undefined) => <Fig>{ts ? formatTime(ts, lang) : '—'}</Fig>
  const line = (Icon: LucideIcon, body: ReactNode, cls = '') => (
    <p className={`pr-outcome ${cls}`} role="status" data-testid="pr-outcome">
      <Icon className="icon" aria-hidden="true" />
      <span>{body}</span>
    </p>
  )
  const order = entryOrderOf(p)
  const contracts = order?.sz ?? p.risk_check?.final_contracts ?? null
  switch (shown) {
    case 'sent': {
      const market = order?.ord_type === 'market' || !order?.px
      return line(
        Send,
        fillSlots(t(market ? 'l.sentMarket' : 'l.sent', { decided: slot('decided'), sent: slot('sent'), price: slot('price') }), {
          decided: at(p.decided_at),
          sent: at(order?.created_at ?? p.decided_at),
          price: <Fig>{P(order?.px ?? null)}</Fig>,
        }),
      )
    }
    case 'partially_filled':
      return line(CircleDashed, fillSlots(t('l.partial', { f: slot('f'), c: slot('c'), price: slot('price') }), { f: <Fig>{order?.filled_sz ?? '—'}</Fig>, c: <Fig>{contracts ?? '—'}</Fig>, price: <Fig>{P(order?.avg_px ?? null)}</Fig> }))
    case 'filled':
      return line(CircleCheck, fillSlots(t('l.filled', { c: slot('c'), price: slot('price'), stop: slot('stop') }), { c: <Fig>{order?.filled_sz ?? contracts ?? '—'}</Fig>, price: <Fig>{P(order?.avg_px ?? null)}</Fig>, stop: <Fig>{P(p.stop)}</Fig> }), 'is-done')
    case 'closed':
      return line(CircleCheck, t('l.closed'), 'is-done')
    case 'rejected': {
      const byRisk = p.status_reason === 'risk_rejected'
      const why = byRisk ? (p.risk_check?.reason ?? '') : (p.owner_note ?? '')
      return line(
        CircleX,
        <>
          {fillSlots(t(byRisk ? 'l.riskRejected' : 'l.rejected', { time: slot('time') }), { time: at(p.decided_at ?? p.updated_at) })}{' '}
          {why && (
            <q dir="auto" data-testid="pr-reject-reason">
              {why}
            </q>
          )}
        </>,
      )
    }
    case 'expired':
      return line(Hourglass, fillSlots(t('l.expired', { time: slot('time') }), { time: at(p.approval_expires_at) }), 'is-warn')
    case 'advice':
      return line(
        Eye,
        <>
          {t('l.advice')}{' '}
          <Link className="pr-link" to="/settings/autonomy">
            {t('l.adviceLink')}
          </Link>
        </>,
      )
    case 'withdrawn':
      return line(Undo2, fillSlots(t('l.withdrawn', { time: slot('time') }), { time: at(p.decided_at ?? p.updated_at) }))
    case 'replaced': {
      const next = replacedBy(p)
      return line(Replace, fillSlots(t('l.replaced', { v: slot('v'), time: slot('time') }), { v: <Fig>{String(next?.version ?? p.version + 1)}</Fig>, time: at(next?.created_at ?? p.updated_at) }))
    }
    default:
      return null
  }
}

const EVIDENCE_ICON: Record<EvidenceLabel, LucideIcon> = {
  technical: ChartCandlestick,
  positioning: Users,
  historian: Sigma,
  news: Newspaper,
  scout: Radar,
  strategist: ChessKnight,
  devils_advocate: ShieldAlert,
  portfolio_manager: Briefcase,
  other: FileText,
}

/** Evidence chips (design v14 pr-evidence): each jumps to the War Room message that posted it. */
function Evidence({ chips, onJump }: { chips: EvidenceChip[]; onJump: (id: string) => void }) {
  const { t } = useTranslation('proposal')
  return (
    <div className="pr-evidence" data-testid="pr-evidence">
      <span className="pr-k">{t('evidence')}</span>
      <div className="pr-chips">
        {chips.map((c) => {
          const Icon = EVIDENCE_ICON[c.label]
          const name = t(`ev.${c.label}`)
          return (
            <a
              key={c.key}
              className="pr-chip"
              href={`#${c.messageId}`}
              aria-label={t('ev.jump', { name })}
              data-testid="pr-chip"
              onClick={(e) => {
                e.preventDefault()
                onJump(c.messageId)
              }}
            >
              <Icon className="icon" aria-hidden="true" strokeWidth={1.5} />
              <span>{name}</span>
            </a>
          )
        })}
      </div>
    </div>
  )
}

function DaBlock({ da, onJump }: { da: NonNullable<ReturnType<typeof daOf>>; onJump: (id: string) => void }) {
  const { t } = useTranslation('proposal')
  const rating = da.rating
  const jump = (e: MouseEvent) => {
    e.preventDefault()
    if (da.messageId) onJump(da.messageId)
  }
  return (
    <div className="pr-da" data-testid="pr-da">
      <span className="pr-da-head">
        <Medallion slug="devils_advocate" size={22} />
        <b>{t('da')}</b>
        {rating !== null && (
          <>
            <span className="pr-pips" aria-hidden="true">
              {Array.from({ length: 5 }, (_, i) => (
                <i key={i} className={i < rating ? 'is-on' : ''} />
              ))}
            </span>
            <span className="pr-da-r" data-testid="pr-da-rating">
              {fillSlots(t('daRating', { n: slot('n') }), { n: <Fig>{String(rating)}</Fig> })}
            </span>
          </>
        )}
        {da.version !== null && <span className="pr-da-after">{fillSlots(t('daAfter', { v: slot('v') }), { v: <Fig>{String(da.version)}</Fig> })}</span>}
      </span>
      {da.objection && (
        <p dir="auto" data-testid="pr-da-objection">
          <Inline text={da.objection} flat />{' '}
          {da.messageId && (
            <a className="pr-link" href={`#${da.messageId}`} onClick={jump}>
              {t('daRead')}
              <ChevronRight className="icon icon-dir" aria-hidden="true" />
            </a>
          )}
        </p>
      )}
    </div>
  )
}

