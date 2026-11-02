import {
  ArrowLeftRight,
  CircleAlert,
  CircleCheck,
  ChessKnight,
  CornerUpLeft,
  FileText,
  Info,
  Layers,
  ListChecks,
  Newspaper,
  NotebookPen,
  ShieldCheck,
  TriangleAlert,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Spinner } from '../../ui'
import type { WarroomMessage } from './api'
import { discardMessage, isLocal, retryMessage, type LocalMessage } from './live'
import { MessageActions, MessageMenu } from './MessageActions'
import { useReadOnly } from './readOnly'
import { Medallion, SystemMedallion } from './Medallion'
import { readPositioningReport } from './cards/positioning'
import { readTechnicalSetup } from './cards/technicalSetup'
import { readProposalCard } from './cards/proposal'
import { ProposalCard } from './cards/ProposalCard'
import { TechnicalSetupCard } from './cards/TechnicalSetupCard'
import { DailyReportCard } from './cards/DailyReportCard'
import { PositionUpdateCard } from './cards/PositionUpdateCard'
import { readDailyReport, readPositionUpdate } from './cards/pm'
import { PositioningCard } from './cards/PositioningCard'
import { readPostMortem, readWeeklyReview } from './cards/learning'
import { PostMortemCard } from './cards/PostMortemCard'
import { WeeklyReviewCard } from './cards/WeeklyReviewCard'
import { readDevilsReview, readRiskCheck, readVoteSummary } from './cards/debate'
import { DevilsReviewCard } from './cards/DevilsReviewCard'
import { RiskCheckCard } from './cards/RiskCheckCard'
import { VoteSummaryCard } from './cards/VoteSummaryCard'
import { HistoryVerdictCard } from './HistoryVerdictCard'
import { readHistoryVerdict, readRegimeTable, unmatchedOf } from './historian'
import { NewsAlertCard } from './NewsAlertCard'
import { NewsBriefCard } from './NewsBriefCard'
import { readNewsBrief } from './newsBrief'
import { MessageReasoning } from './Reasoning'
import { useRuns } from './runs'
import { readNewsAlert } from './newsAlert'
import { RegimeTableCard } from './RegimeTableCard'
import { WatchlistCard } from './WatchlistCard'
import { readWatchlistCard } from './watchlist'
import { UntracedScope } from './Untraced'
import { Excerpt, Inline, Markdown } from './text'
import { continues, shapeOf, useAuthorName } from './shape'
import { formatFullTime, formatTime } from './time'

/*
 * Feed renderers (design v4 War Room §7, §12). Agents write documents on the page, the owner
 * speaks in bubbles at the inline end, system `text` is one quiet centred line, `system_alert` a
 * washed card, a kind registered in CARDS its own card (news_alert: design v6 §5.8), and any other
 * kind a GenericCard with its label until its patch builds a card.
 */

const KIND_ICON: Record<string, LucideIcon> = {
  watchlist: ListChecks,
  research_report: FileText,
  regime_table: Layers,
  news_alert: Newspaper,
  proposal: ChessKnight,
  vote_summary: UsersRound,
  risk_check: ShieldCheck,
  order: ArrowLeftRight,
  fill: CircleCheck,
  position_update: Layers,
  post_mortem: NotebookPen,
}

/**
 * Kinds with their own card. Each renderer returns null when the message lacks what its card
 * needs, and the GenericCard draws it instead. A later patch registers its kind here.
 */
interface CardCtx {
  onJump: (id: string) => void
  parent?: WarroomMessage
}

const CARDS: Partial<Record<string, (m: WarroomMessage, lang: Lang, ctx: CardCtx) => ReactNode>> = {
  news_alert: (m, lang) => {
    const alert = readNewsAlert(m.card)
    return alert ? <NewsAlertCard alert={alert} lang={lang} /> : null
  },
  // The Historian's answer (design v7 Historian cards §5): its content_md above the card, with
  // untraced numbers marked in both (brief §7.11, ATL-91 unmatched_numbers).
  research_report: (m, lang, ctx) => {
    const { parent } = ctx
    // The Historian's Sunday weekly review (brief §7.3, design v16 §9, ATL-199 card, ATL-203).
    const weekly = readWeeklyReview(m.card)
    if (weekly) {
      return (
        <>
          <AgentBody message={m} />
          <WeeklyReviewCard review={weekly} lang={lang} />
        </>
      )
    }
    // News Intelligence's brief (design v9 §B): its content_md above the card, as for the Historian.
    const brief = readNewsBrief(m.card, parent?.kind)
    if (brief) {
      return (
        <>
          <AgentBody message={m} />
          <NewsBriefCard brief={brief} unmatched={unmatchedOf(m)} lang={lang} />
        </>
      )
    }
    // The Portfolio Manager's 23:30 daily report (brief §7.8, ATL-170 card; design v14, ATL-178).
    const daily = readDailyReport(m.card)
    if (daily) {
      return (
        <>
          <AgentBody message={m} />
          <DailyReportCard report={daily} lang={lang} />
        </>
      )
    }
    // The Technical Analyst's setup (design v11 Phase 2 §7, ATL-135 card): its words above the card.
    const setup = readTechnicalSetup(m.card)
    if (setup) {
      return (
        <>
          <AgentBody message={m} />
          <TechnicalSetupCard setup={setup} lang={lang} />
        </>
      )
    }
    // The Derivatives & Sentiment Analyst's positioning (design v11 Phase 2 §7, ATL-136 card).
    const positioning = readPositioningReport(m.card)
    if (positioning) {
      return (
        <>
          <AgentBody message={m} />
          <PositioningCard report={positioning} lang={lang} />
        </>
      )
    }
    // The Devil's Advocate's review (design v11 Phase 2 §6, ATL-165). A re-rate replies under the
    // round-1 review, which gives it the rating it was and the items now OK.
    const review = readDevilsReview(m.card)
    if (review) {
      return (
        <>
          <AgentBody message={m} />
          <DevilsReviewCard review={review} previous={readDevilsReview(parent?.card)} target={m.reply_to} onJump={ctx.onJump} ts={m.created_at} lang={lang} />
        </>
      )
    }
    const card = readHistoryVerdict(m.card)
    if (!card) return null
    const unmatched = unmatchedOf(m)
    return (
      <>
        <AgentBody message={m} />
        <HistoryVerdictCard card={card} unmatched={unmatched} lang={lang} />
      </>
    )
  },
  // Market Scout's watchlist (design v9 §A): the Scout's words above, the day's agenda under them.
  watchlist: (m, lang, ctx) => {
    const card = readWatchlistCard(m.card)
    if (!card) return null
    return (
      <>
        <AgentBody message={m} />
        <WatchlistCard card={card} lang={lang} onJump={ctx.onJump} />
      </>
    )
  },
  // A trade proposal (brief §12, §7.9 step 5; ATL-164): the Strategist's words, then the card the
  // owner approves, rejects, edits or asks the team about.
  proposal: (m, lang, ctx) => {
    const ref = readProposalCard(m.card)
    if (!ref) return null
    return (
      <>
        <AgentBody message={m} />
        <ProposalCard cardRef={ref} lang={lang} onJump={ctx.onJump} />
      </>
    )
  },
  // The Portfolio Manager's position check (brief §7.8, ATL-178): its words, then code's figures and
  // the rule actions with links to their proposal cards.
  position_update: (m, lang) => {
    const card = readPositionUpdate(m.card)
    if (!card) return null
    return (
      <>
        <AgentBody message={m} />
        <PositionUpdateCard card={card} lang={lang} />
      </>
    )
  },
  // The weighted vote and the Risk Engine's result (design v11 Phase 2 §6, ATL-165).
  vote_summary: (m, lang, ctx) => {
    const card = readVoteSummary(m.card)
    if (!card) return null
    return (
      <>
        <AgentBody message={m} />
        <VoteSummaryCard card={card} target={m.reply_to} onJump={ctx.onJump} ts={m.created_at} lang={lang} />
      </>
    )
  },
  risk_check: (m, lang, ctx) => {
    const card = readRiskCheck(m.card)
    if (!card) return null
    return (
      <>
        <AgentBody message={m} />
        <RiskCheckCard card={card} target={m.reply_to} onJump={ctx.onJump} ts={m.created_at} lang={lang} />
      </>
    )
  },
  // The Portfolio Manager's post-mortem of a closed trade (brief §7.8, design v16 §9, ATL-194 card,
  // ATL-203), replying to the proposal's message.
  post_mortem: (m, lang) => {
    const pm = readPostMortem(m.card)
    if (!pm) return null
    return (
      <>
        <AgentBody message={m} />
        <PostMortemCard pm={pm} lang={lang} />
      </>
    )
  },
  // The Historian's 07:30 regime table (design v7 §7).
  regime_table: (m, lang) => {
    const card = readRegimeTable(m.card)
    if (!card) return null
    return (
      <>
        <AgentBody message={m} />
        <RegimeTableCard card={card} lang={lang} />
      </>
    )
  },
}

/** An agent's content_md, with the figures the number guard could not trace marked. */
function AgentBody({ message: m }: { message: WarroomMessage }) {
  if (!m.content_md.trim()) return null
  const raws = unmatchedOf(m)
    .filter((u) => u.location === 'text')
    .map((u) => u.raw)
  return (
    <UntracedScope raws={raws}>
      <Markdown text={m.content_md} />
    </UntracedScope>
  )
}

function Time({ ts, lang }: { ts: string; lang: Lang }) {
  return (
    <time className="msg-time num" dateTime={ts} title={formatFullTime(ts, lang)}>
      {formatTime(ts, lang)}
    </time>
  )
}

function ReplyQuote({ parent, onJump }: { parent: WarroomMessage; onJump: (id: string) => void }) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  const name = authorName(parent)
  const jump = (e: MouseEvent) => {
    e.preventDefault()
    onJump(parent.id)
  }
  return (
    <a className="msg-quote" href={`#${parent.id}`} onClick={jump} aria-label={t('jumpTo', { name })}>
      <CornerUpLeft aria-hidden="true" strokeWidth={1.5} className="icon-dir" />
      <b>{name}</b>
      <span className="excerpt" dir="auto">
        <Excerpt text={parent.content_md} />
      </span>
    </a>
  )
}

/** The owner's message while it is sending, or after it failed (design v4 War Room §8, Owner send). */
function SendMeta({ message }: { message: LocalMessage }) {
  const { t } = useTranslation('warroom')
  const qc = useQueryClient()
  const { tempId, state } = message.local
  if (state === 'sending') {
    return (
      <p className="msg-meta" data-testid="wr-sending">
        <Spinner className="msg-spinner" />
        <span>{t('send.sending')}</span>
      </p>
    )
  }
  return (
    <p className="msg-meta" role="alert" data-testid="wr-failed">
      <span className="is-fail">
        <CircleAlert aria-hidden="true" strokeWidth={1.5} />
        {t('send.notSent')}
      </span>
      <Button variant="plain" size="sm" onClick={() => void retryMessage(qc, tempId)} data-testid="wr-retry">
        {t('tryAgain')}
      </Button>
      <Button variant="plain-destructive" size="sm" onClick={() => discardMessage(tempId)}>
        {t('send.delete')}
      </Button>
    </p>
  )
}

export interface MessageProps {
  message: WarroomMessage
  prev: WarroomMessage | undefined
  /** The replied-to message, when it is loaded. */
  parent: WarroomMessage | undefined
  lang: Lang
  active: boolean
  tabbable: boolean
  flash: boolean
  /** Reduced motion's still ring in place of the flash (ATL-220, design v18 §5.6). */
  marked?: boolean
  onJump: (id: string) => void
  /** Starts a reply to this message: the ReplyChip appears above the composer. */
  onReply: (message: WarroomMessage) => void
  /** While this message's context menu is open, it stays active (Feed pins it). */
  onMenuChange: (open: boolean) => void
}

/** How far the menu stays clear of the viewport edge, so it never renders off-screen. */
const MENU_MARGIN = { x: 200, y: 90 }

export function Message({ message: m, prev, parent, lang, active, tabbable, flash, marked, onJump, onReply, onMenuChange }: MessageProps) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  const shape = shapeOf(m)
  const time = formatTime(m.created_at, lang)
  const label = t('messageLabel', { name: authorName(m), time })
  const common = {
    'data-msg-id': m.id,
    'data-shape': shape,
    tabIndex: tabbable ? 0 : -1,
    'aria-label': label,
  }
  const cls = (...extra: Array<string | false>) =>
    ['msg', `is-${shape}`, active && shape !== 'event' && shape !== 'alert' && 'is-active', flash && 'is-flash', marked && 'is-marked', ...extra]
      .filter(Boolean)
      .join(' ')

  // MessageActions (design v4 War Room §7): Reply and Copy text, on agent and owner messages that
  // are not still sending or failed. The context menu is the same two items, opened by right-click,
  // the context-menu key or Shift-F10 (all of which the browser turns into a native "contextmenu"
  // event on the focused element) — so one handler on the article covers all three.
  const readOnly = useReadOnly()
  const canAct = (shape === 'agent' || shape === 'owner') && !isLocal(m) && !readOnly
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null)
  const closeMenu = () => {
    setMenuAt(null)
    onMenuChange(false)
  }
  const onContextMenu = canAct
    ? (e: MouseEvent) => {
        e.preventDefault()
        setMenuAt({ x: Math.min(e.clientX, window.innerWidth - MENU_MARGIN.x), y: Math.min(e.clientY, window.innerHeight - MENU_MARGIN.y) })
        onMenuChange(true)
      }
    : undefined

  if (shape === 'event') {
    return (
      <div role="article" className={cls()} {...common}>
        <p className="event-line">
          <Info aria-hidden="true" strokeWidth={1.5} />
          <span dir="auto">
            <Inline text={m.content_md} />
          </span>
          <time className="num" dateTime={m.created_at} title={formatFullTime(m.created_at, lang)}>
            {time}
          </time>
        </p>
      </div>
    )
  }

  if (shape === 'alert') {
    const severity = m.card?.severity === 'critical' || m.card?.severity === 'warning' ? m.card.severity : 'info'
    const Icon = severity === 'critical' ? CircleAlert : severity === 'warning' ? TriangleAlert : Info
    return (
      <article className={cls()} {...common}>
        <div className={`alert-card sev-${severity}`} role="group">
          <span className="alert-icon">
            <Icon aria-hidden="true" strokeWidth={1.5} />
          </span>
          <div className="min-w-0">
            <p className="alert-title" dir="auto">
              {m.card?.title ? <Inline text={m.card.title} flat /> : t('kind.system_alert')}
            </p>
            <div className="alert-body">
              <Markdown text={m.content_md} />
            </div>
          </div>
          <span className="alert-meta">
            <bdi dir="ltr">{m.author}</bdi> <Time ts={m.created_at} lang={lang} />
          </span>
        </div>
      </article>
    )
  }

  const cont = continues(m, prev)

  if (shape === 'owner') {
    const local = isLocal(m) ? m : null
    return (
      <article
        className={cls(cont && 'is-cont', local?.local.state === 'failed' && 'is-failed')}
        {...common}
        data-local={local?.local.state}
        aria-busy={local?.local.state === 'sending' || undefined}
        onContextMenu={onContextMenu}
      >
        <div className="msg-main">
          {parent && <ReplyQuote parent={parent} onJump={onJump} />}
          <Markdown text={m.content_md} className="bubble" />
          {local ? (
            <SendMeta message={local} />
          ) : (
            <p className="msg-meta">
              <Time ts={m.created_at} lang={lang} />
            </p>
          )}
        </div>
        {active && canAct && <MessageActions message={m} onReply={() => onReply(m)} />}
        {menuAt && <MessageMenu message={m} label={label} x={menuAt.x} y={menuAt.y} onReply={() => onReply(m)} onClose={closeMenu} />}
      </article>
    )
  }

  return <AgentMessage {...{ m, cont, cls, common, parent, lang, label, time, onJump, onReply, onContextMenu, active, canAct, menuAt, closeMenu }} />
}

type AgentMessageProps = {
  m: WarroomMessage
  cont: boolean
  cls: (...extra: Array<string | false>) => string
  common: { 'data-msg-id': string; 'data-shape': string; tabIndex: number; 'aria-label': string }
  parent: WarroomMessage | undefined
  lang: Lang
  label: string
  time: string
  onJump: (id: string) => void
  onReply: (message: WarroomMessage) => void
  onContextMenu: ((e: MouseEvent) => void) | undefined
  active: boolean
  canAct: boolean
  menuAt: { x: number; y: number } | null
  closeMenu: () => void
}

/** An agent's or system's document, with "Show reasoning" under it (ATL-97). */
function AgentMessage({ m, cont, cls, common, parent, lang, label, time, onJump, onReply, onContextMenu, active, canAct, menuAt, closeMenu }: AgentMessageProps) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  const ref = useRef<HTMLElement>(null)
  // A live run this message replaced may hand focus over: to the new toggle, or to the message.
  useEffect(() => {
    const handoff = useRuns.getState().takeFocus(m.id)
    if (!handoff || !ref.current) return
    const toggle = handoff.part === 'toggle' ? ref.current.querySelector<HTMLElement>('[data-testid="wr-reason-toggle"]') : null
    ;(toggle ?? ref.current).focus({ preventScroll: true })
  }, [m.id])
  const KindIcon = KIND_ICON[m.kind] ?? FileText
  const kindLabel = t(`kind.${m.kind}`, { defaultValue: m.kind })
  const card = m.kind === 'text' ? null : (CARDS[m.kind]?.(m, lang, { onJump, parent }) ?? null)
  return (
    <article ref={ref} className={cls(cont && 'is-cont')} {...common} onContextMenu={onContextMenu}>
      <div className="msg-gutter">
        {cont ? (
          <span className="msg-time-hover num">{time}</span>
        ) : m.author_type === 'system' ? (
          <SystemMedallion size={32} />
        ) : (
          <Medallion slug={m.author} size={32} />
        )}
      </div>
      <div className="msg-main">
        {!cont && (
          <header className="msg-head">
            <span className="msg-author">{authorName(m)}</span>
            <Time ts={m.created_at} lang={lang} />
          </header>
        )}
        {parent && <ReplyQuote parent={parent} onJump={onJump} />}
        {m.kind === 'text' ? (
          <Markdown text={m.content_md} />
        ) : card ? (
          card
        ) : (
          <div className="kind-card" data-kind={m.kind}>
            <div className="kind-head">
              <KindIcon aria-hidden="true" strokeWidth={1.5} />
              <span className="kind-label">{kindLabel}</span>
            </div>
            <Markdown text={m.content_md} />
          </div>
        )}
        {m.author_type === 'agent' && <MessageReasoning message={m} lang={lang} />}
      </div>
      {active && canAct && <MessageActions message={m} onReply={() => onReply(m)} />}
      {menuAt && <MessageMenu message={m} label={label} x={menuAt.x} y={menuAt.y} onReply={() => onReply(m)} onClose={closeMenu} />}
    </article>
  )
}
