import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { getEcho, hasConnected, reconnectEcho, useLiveStatus } from '../../lib/echo'
import { threadsKey, type WarroomMessage, type WarroomThread } from './api'
import { cachedIds, upsertMessage } from './live'
import { runMatches, toolLabel, useRuns, type ThinkingEnvelope } from './runs'
import { useAuthorName } from './shape'
import { plainText } from './tokens'
import { invalidateWatchlists } from './watchlist'

/*
 * The War Room's live side (brief §12, design v4 War Room §7 ConnectionBanner, §11):
 * - `.warroom.message` on private-warroom goes into the query cache by id;
 * - an arrival from someone else in the open thread is announced in a polite live region;
 * - while the socket is down the banner says so, and on reconnect the env's message queries are
 *   refetched, so anything broadcast while offline appears (Postgres is the record, the socket a hint).
 */

/** How long a first connection may take before the banner calls it down. */
const FIRST_CONNECT_GRACE_MS = 4000
const BACK_MS = 2600
const ANNOUNCE_CHARS = 140
/** A run that ended `ok` but whose message never reached this browser is dropped after this. */
const OK_WITHOUT_MESSAGE_MS = 15_000

export type BannerState = { kind: 'down' } | { kind: 'back'; missed: number } | null

export function useLiveFeed(env: TradingEnv, threadId: string | undefined) {
  const { t } = useTranslation('warroom')
  const qc = useQueryClient()
  const status = useLiveStatus()
  const authorName = useAuthorName()
  const [announcement, setAnnouncement] = useState('')
  const [graceOver, setGraceOver] = useState(false)
  const [back, setBack] = useState<number | null>(null)
  const wasDown = useRef(false)

  // The handler reads the latest thread and translations without resubscribing.
  const latest = useRef({ threadId, authorName, t })
  useEffect(() => {
    latest.current = { threadId, authorName, t }
  })

  useEffect(() => {
    const channel = getEcho().private('warroom')
    const mainThreadId = () => qc.getQueryData<{ data: WarroomThread[] }>(threadsKey(env))?.data.find((th) => th.kind === 'main')?.id
    const onMessage = (m: WarroomMessage) => {
      // A live run's answer replaces its row (ATL-97); focus on the row or its toggle moves along.
      const runs = useRuns.getState()
      const run = Object.values(runs.runs).find((r) => runMatches(r, m, mainThreadId()))
      if (run) {
        const focused = document.activeElement
        const row = focused?.closest(`[data-run-id="${CSS.escape(run.id)}"]`)
        runs.resolve(m, mainThreadId(), row ? (focused?.matches('[data-testid="wr-reason-toggle"]') ? 'toggle' : 'row') : null)
      }
      const fresh = upsertMessage(qc, env, m)
      // A new watchlist version replaces the day's current one (design v9 §A5, "Replaced").
      if (m.kind === 'watchlist') void invalidateWatchlists(qc, env)
      const cur = latest.current
      if (fresh && m.thread_id === cur.threadId && m.author_type !== 'human') {
        const body = plainText(m.content_md, (tag) => cur.t(`tag.${tag}`))
        // A news alert (design v6 §5.8) is announced as "Atlas: News alert. <headline>".
        const text = (m.kind === 'news_alert' ? `${cur.t('kind.news_alert')}. ${body}` : body).slice(0, ANNOUNCE_CHARS)
        setAnnouncement(cur.t('liveAnnounce', { name: cur.authorName(m), text }))
      }
    }
    const timers = new Set<number>()
    const onThinking = (e: ThinkingEnvelope) => {
      if (!e?.payload?.run_id || e.env !== env) return
      const applied = useRuns.getState().apply(e)
      if (!applied) return
      const { run, started } = applied
      const p = e.payload
      if (p.delta_type === 'run_end' && run.status === 'ok') {
        const timer = window.setTimeout(() => {
          timers.delete(timer)
          useRuns.getState().dismiss(run.id)
        }, OK_WITHOUT_MESSAGE_MS)
        timers.add(timer)
      }
      // Announced politely in the open thread, never the streamed text (design v7 §9).
      const cur = latest.current
      if ((run.threadId ?? mainThreadId()) !== cur.threadId) return
      const name = cur.t(`agents.${run.agent}.name`, { defaultValue: run.agent })
      if (p.delta_type === 'run_end') {
        if (run.status !== 'ok' && run.status !== 'running') {
          setAnnouncement(cur.t('run.ann.stopped', { name, why: cur.t(`run.why.${run.status}`, { name }) }))
        }
      } else if (p.delta_type === 'tool_use' && p.tool) {
        setAnnouncement(cur.t('run.ann.tool', { name, tool: toolLabel(p.tool) }))
      } else if (p.delta_type === 'text' && run.text === (p.text ?? '')) {
        setAnnouncement(cur.t('run.ann.writing', { name }))
      } else if (started) {
        setAnnouncement(cur.t('run.ann.start', { name }))
      }
    }
    // ATL-103: an owner edit (remove, restore, pin) from any tab; the cards refetch the day.
    const onWatchlist = (e: { env?: string } | undefined) => {
      if (!e?.env || e.env === env) void invalidateWatchlists(qc, env)
    }
    channel.listen('.warroom.message', onMessage)
    channel.listen('.warroom.thinking', onThinking)
    channel.listen('.watchlist.updated', onWatchlist)
    return () => {
      channel.stopListening('.warroom.message', onMessage)
      channel.stopListening('.warroom.thinking', onThinking)
      channel.stopListening('.watchlist.updated', onWatchlist)
      for (const timer of timers) window.clearTimeout(timer)
    }
  }, [qc, env])

  // Live runs belong to one env: switching env starts from none.
  useEffect(() => () => useRuns.getState().reset(), [env])

  useEffect(() => {
    const timer = window.setTimeout(() => setGraceOver(true), FIRST_CONNECT_GRACE_MS)
    return () => window.clearTimeout(timer)
  }, [])

  const down = status !== 'connected' && (hasConnected() || graceOver)

  useEffect(() => {
    if (status !== 'connected') {
      if (down) wasDown.current = true
      return
    }
    if (!wasDown.current) return
    wasDown.current = false
    let cancelled = false
    const before = cachedIds(qc, env)
    void Promise.all([
      qc.refetchQueries({ queryKey: threadsKey(env) }),
      qc.refetchQueries({ queryKey: ['warroom', 'messages', env], type: 'active' }),
    ]).then(() => {
      if (cancelled) return
      let missed = 0
      for (const id of cachedIds(qc, env)) if (!before.has(id)) missed += 1
      setBack(missed)
    })
    return () => {
      cancelled = true
    }
  }, [status, down, qc, env])

  useEffect(() => {
    if (back === null) return
    const timer = window.setTimeout(() => setBack(null), BACK_MS)
    return () => window.clearTimeout(timer)
  }, [back])

  const banner: BannerState = down ? { kind: 'down' } : back !== null ? { kind: 'back', missed: back } : null
  return { banner, announcement, reconnect: reconnectEcho }
}
