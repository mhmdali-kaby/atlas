import {
  ArrowLeftRight,
  CircleAlert,
  CircleCheck,
  ChessKnight,
  CornerUpLeft,
  FileText,
  History,
  Info,
  Layers,
  ListChecks,
  Newspaper,
  ShieldCheck,
  TriangleAlert,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useState, type MouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Spinner } from '../../ui'
import type { WarroomMessage } from './api'
import { discardMessage, isLocal, retryMessage, type LocalMessage } from './live'
import { MessageActions, MessageMenu } from './MessageActions'
import { Medallion, SystemMedallion } from './Medallion'
import { NewsAlertCard } from './NewsAlertCard'
import { readNewsAlert } from './newsAlert'
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
  news_alert: Newspaper,
  proposal: ChessKnight,
  vote_summary: UsersRound,
  risk_check: ShieldCheck,
  order: ArrowLeftRight,
  fill: CircleCheck,
  position_update: Layers,
  post_mortem: History,
}

/**
 * Kinds with their own card. Each renderer returns null when the message lacks what its card
 * needs, and the GenericCard draws it instead. A later patch registers its kind here.
 */
const CARDS: Partial<Record<string, (m: WarroomMessage, lang: Lang) => ReactNode>> = {
  news_alert: (m, lang) => {
    const alert = readNewsAlert(m.card)
    return alert ? <NewsAlertCard alert={alert} lang={lang} /> : null
  },
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
  onJump: (id: string) => void
  /** Starts a reply to this message: the ReplyChip appears above the composer. */
  onReply: (message: WarroomMessage) => void
  /** While this message's context menu is open, it stays active (Feed pins it). */
  onMenuChange: (open: boolean) => void
}

/** How far the menu stays clear of the viewport edge, so it never renders off-screen. */
const MENU_MARGIN = { x: 200, y: 90 }

export function Message({ message: m, prev, parent, lang, active, tabbable, flash, onJump, onReply, onMenuChange }: MessageProps) {
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
    ['msg', `is-${shape}`, active && shape !== 'event' && shape !== 'alert' && 'is-active', flash && 'is-flash', ...extra].filter(Boolean).join(' ')

  // MessageActions (design v4 War Room §7): Reply and Copy text, on agent and owner messages that
  // are not still sending or failed. The context menu is the same two items, opened by right-click,
  // the context-menu key or Shift-F10 (all of which the browser turns into a native "contextmenu"
  // event on the focused element) — so one handler on the article covers all three.
  const canAct = (shape === 'agent' || shape === 'owner') && !isLocal(m)
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

  const KindIcon = KIND_ICON[m.kind] ?? FileText
  const kindLabel = t(`kind.${m.kind}`, { defaultValue: m.kind })
  const card = m.kind === 'text' ? null : (CARDS[m.kind]?.(m, lang) ?? null)
  return (
    <article className={cls(cont && 'is-cont')} {...common} onContextMenu={onContextMenu}>
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
      </div>
      {active && canAct && <MessageActions message={m} onReply={() => onReply(m)} />}
      {menuAt && <MessageMenu message={m} label={label} x={menuAt.x} y={menuAt.y} onReply={() => onReply(m)} onClose={closeMenu} />}
    </article>
  )
}
