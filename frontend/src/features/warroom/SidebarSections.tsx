import { Lock, MessagesSquare } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { SidebarSectionProps } from '../../app/feature'
import { Skeleton } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { AGENTS, agentStatus } from './agents'
import type { WarroomThread } from './api'
import { Medallion } from './Medallion'
import { threadPath, useSelectedThread } from './selection'
import { InstrumentId } from './text'
import { dayOrTime } from './time'
import './warroom.css'

/*
 * The War Room's sidebar sections (design v4 War Room §7): the nine agents, then the threads.
 * Rows carry data-nav-item so the shell's ↑/↓ move between them.
 */

/**
 * The agents, under a heading that carries the day's total at its end ("All offline" until the
 * agent runtime reports costs). Drawn here rather than by the shell, which has no slot for the note.
 */
export function AgentList(_props: SidebarSectionProps) {
  const { t } = useTranslation('warroom')
  const allOffline = AGENTS.every((a) => agentStatus(a.slug) === 'offline')
  return (
    <div className="side-section" role="group" aria-labelledby="wr-head-agents" data-testid="wr-agents">
      <h2 className="side-head" id="wr-head-agents">
        <span>{t('sidebar.agents')}</span>
        {allOffline && <span className="side-note">{t('sidebar.allOffline')}</span>}
      </h2>
      {AGENTS.map((agent) => {
        const status = agentStatus(agent.slug)
        const name = t(`agents.${agent.slug}.name`)
        return (
          // The AgentCard popover (Mention, Private thread) arrives with the composer work; the
          // row is already the button that will open it.
          <button
            key={agent.slug}
            type="button"
            className="agent-row"
            data-nav-item=""
            data-slug={agent.slug}
            data-status={status}
            aria-label={t('agentLabel', { name, status: t(`status.${status}`) })}
          >
            <Medallion slug={agent.slug} size={28} status={status} dot />
            <span className="agent-text">
              <span className={agent.hasShort ? 'agent-name has-short' : 'agent-name'}>
                <span className="name-full">{name}</span>
                {agent.hasShort && <span className="name-short">{t(`agents.${agent.slug}.short`)}</span>}
              </span>
              <span className="agent-sub">
                <span className="agent-task">{status === 'offline' ? t(`agents.${agent.slug}.role`) : t(`status.${status}`)}</span>
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}

function ThreadTitle({ thread }: { thread: WarroomThread }) {
  const { t } = useTranslation('warroom')
  if (thread.kind === 'instrument') return <InstrumentId id={thread.key} />
  if (thread.kind === 'agent') return <span>{t(`agents.${thread.key}.name`, { defaultValue: thread.title })}</span>
  return <span>{t('main')}</span>
}

function ThreadLead({ thread }: { thread: WarroomThread }) {
  if (thread.kind === 'main') return <MessagesSquare aria-hidden="true" strokeWidth={1.5} className="thread-lead-icon" />
  if (thread.kind === 'agent') return <Medallion slug={thread.key} size={22} />
  return (
    <span className="mono-tile" aria-hidden="true">
      {thread.key.split('-')[0]}
    </span>
  )
}

export function ThreadList({ onNavigate }: SidebarSectionProps) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const now = useNow(60_000)
  const { threads, list, selected } = useSelectedThread()

  if (threads.isPending) {
    return (
      <div className="side-section" aria-busy="true" aria-label={t('sidebar.loadingThreads')}>
        {[70, 110, 96].map((w) => (
          <div key={w} className="side-skel">
            <Skeleton style={{ width: 20, height: 20, borderRadius: 6 }} />
            <Skeleton style={{ width: w, height: 12 }} />
          </div>
        ))}
      </div>
    )
  }
  if (threads.isError) {
    return <p className="side-error">{t('sidebar.threadsError')}</p>
  }
  return (
    <div className="side-section" data-testid="wr-threads">
      {list.map((thread) => {
        const current = thread.id === selected?.id
        return (
          <Link
            key={thread.id}
            to={threadPath(thread)}
            className="thread-row"
            data-nav-item=""
            data-thread-kind={thread.kind}
            aria-current={current ? 'page' : undefined}
            onClick={onNavigate}
          >
            <span className="thread-lead">
              <ThreadLead thread={thread} />
            </span>
            <span className="thread-text">
              <span className="thread-title">
                <ThreadTitle thread={thread} />
                {thread.kind === 'agent' && <Lock aria-hidden="true" strokeWidth={1.5} className="thread-lock" />}
              </span>
              {thread.last_message_at && (
                <time className="thread-time num" dateTime={thread.last_message_at}>
                  {dayOrTime(thread.last_message_at, lang, now, t('yesterday'))}
                </time>
              )}
            </span>
          </Link>
        )
      })}
    </div>
  )
}
