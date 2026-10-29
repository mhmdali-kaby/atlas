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
import type { MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button, Spinner } from '../../ui'
import type { WarroomMessage } from './api'
import { discardMessage, isLocal, retryMessage, type LocalMessage } from './live'
import { Medallion, SystemMedallion } from './Medallion'
import { Excerpt, Inline, Markdown } from './text'
import { continues, shapeOf, useAuthorName } from './shape'
import { formatFullTime, formatTime } from './time'

/*
 * Feed renderers (design v4 War Room §7, §12). Agents write documents on the page, the owner
 * speaks in bubbles at the inline end, system `text` is one quiet centred line, `system_alert` a
 * washed card, and any other kind a GenericCard with its label until its patch builds a card.
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
}

export function Message({ message: m, prev, parent, lang, active, tabbable, flash, onJump }: MessageProps) {
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
      </article>
    )
  }

  const KindIcon = KIND_ICON[m.kind] ?? FileText
  const kindLabel = t(`kind.${m.kind}`, { defaultValue: m.kind })
  return (
    <article className={cls(cont && 'is-cont')} {...common}>
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
    </article>
  )
}
