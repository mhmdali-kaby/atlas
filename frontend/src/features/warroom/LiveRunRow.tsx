import { CircleAlert, CornerUpLeft, Hourglass, RefreshCw } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { memo, useEffect, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Button } from '../../ui'
import type { WarroomMessage } from './api'
import { sendMessage } from './live'
import { Medallion } from './Medallion'
import { ReasonDisclosure } from './Reasoning'
import { lastSentence, runKey, toolLabel, useRuns, type LiveRun } from './runs'
import { Excerpt, Markdown } from './text'
import { useAuthorName } from './shape'
import { formatFullTime, formatTime } from './time'

/*
 * The live run row (ATL-97; design v7 §9): the message being written. It sits where the answer
 * will land, under the question it answers, and turns into that message. While it runs only the
 * phase and the body change: the medallion is a memoised sibling, so its turning ring and the
 * pulse never restart. A stopped run (error, rate_limited, degraded) keeps what it streamed and
 * says why; it is never stored, so a reload drops it.
 */

const TICK_MS = 1000

function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Ticks once a second on its own, so the rest of the row is not re-rendered by the clock. */
function Elapsed({ since }: { since: number }) {
  const { t } = useTranslation('warroom')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    return () => window.clearInterval(timer)
  }, [])
  const text = mmss(now - since)
  return (
    <span className="run-elapsed num" role="timer" aria-label={t('run.elapsedSr', { t: text })}>
      <bdi dir="ltr">{text}</bdi>
    </span>
  )
}

const LiveMedallion = memo(function LiveMedallion({ slug, live }: { slug: string; live: boolean }) {
  return <Medallion slug={slug} size={32} status={live ? 'thinking' : 'idle'} />
})

function Phase({ run }: { run: LiveRun }) {
  const { t } = useTranslation('warroom')
  if (run.phase === 'calling' && run.calling) {
    const [before, after] = t('run.calling', { tool: '\u0000' }).split('\u0000')
    return (
      <span className="run-phase" data-phase="calling">
        {before}
        <span className="tool-name">{toolLabel(run.calling)}</span>
        {after}
      </span>
    )
  }
  return (
    <span className="run-phase" data-phase={run.phase}>
      {t(run.phase === 'writing' ? 'run.writing' : 'run.thinking')}
    </span>
  )
}

function Quote({ parent, onJump }: { parent: WarroomMessage; onJump: (id: string) => void }) {
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

export interface LiveRunRowProps {
  run: LiveRun
  parent: WarroomMessage | undefined
  lang: Lang
  onJump: (id: string) => void
}

export function LiveRunRow({ run, parent, lang, onJump }: LiveRunRowProps) {
  const { t } = useTranslation('warroom')
  const qc = useQueryClient()
  const name = t(`agents.${run.agent}.name`, { defaultValue: run.agent })
  const key = runKey(run.id)
  const open = useRuns((s) => Boolean(s.open[key]))
  // `ok` without its message yet still reads as the answer being finished.
  const live = run.status === 'running' || run.status === 'ok'
  const stopped = !live
  const ticker = live && !open && !run.text ? lastSentence(run.thinking) : ''
  const StopIcon = run.status === 'rate_limited' ? Hourglass : CircleAlert

  return (
    <article
      className={['msg', 'is-agent', 'is-run', live ? 'is-live' : 'is-stopped'].join(' ')}
      data-run-id={run.id}
      data-status={run.status}
      tabIndex={-1}
      aria-busy={live || undefined}
      aria-label={live ? t('run.busy', { name }) : `${name}, ${t(`run.st.${run.status}`)}`}
      data-testid="wr-run"
    >
      <div className="msg-gutter">
        <LiveMedallion slug={run.agent} live={live} />
      </div>
      <div className="msg-main">
        <header className="msg-head">
          <span className="msg-author">{name}</span>
          {live ? (
            <>
              <span className="run-state">
                <span className="run-pulse" aria-hidden="true" />
                <Phase run={run} />
              </span>
              <Elapsed since={run.receivedAt} />
            </>
          ) : (
            <>
              <span className="run-state is-stopped">
                <StopIcon aria-hidden="true" strokeWidth={1.5} />
                <span>{t(`run.st.${run.status}`)}</span>
              </span>
              {run.endedAt && (
                <time className="msg-time num" dateTime={run.endedAt} title={formatFullTime(run.endedAt, lang)}>
                  {formatTime(run.endedAt, lang)}
                </time>
              )}
            </>
          )}
        </header>
        <div className="run-body">
          {parent && <Quote parent={parent} onJump={onJump} />}
          {run.text && <Markdown text={run.text} className={run.status === 'running' ? 'run-text has-caret' : 'run-text'} />}
          {ticker && (
            <p className="run-ticker" dir="auto" data-testid="wr-run-ticker">
              {ticker}
            </p>
          )}
          {stopped && (
            <div className="run-why" role="note">
              <StopIcon aria-hidden="true" strokeWidth={1.5} />
              <p>{t(`run.why.${run.status}`, { name })}</p>
              {run.status === 'error' && parent && parent.author_type === 'human' && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    useRuns.getState().dismiss(run.id)
                    void sendMessage(qc, { env: run.env, threadId: parent.thread_id, content: parent.content_md, replyTo: parent.reply_to })
                  }}
                >
                  <RefreshCw aria-hidden="true" strokeWidth={1.5} />
                  <span>{t('run.askAgain')}</span>
                </Button>
              )}
            </div>
          )}
          {(run.thinking || run.tools.length > 0) && (
            <ReasonDisclosure
              panelKey={key}
              reasoning={run.thinking}
              tools={run.tools}
              showLabel={t(live ? 'run.live' : 'run.soFar')}
              hideLabel={t(live ? 'run.hideLive' : 'run.hideSoFar')}
              live={{ caret: run.status === 'running' && run.phase === 'thinking', cut: run.cut, joinedAt: run.joinedAt }}
              lang={lang}
            />
          )}
        </div>
      </div>
    </article>
  )
}
