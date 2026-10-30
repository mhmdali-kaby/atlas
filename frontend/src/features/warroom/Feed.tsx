import { useVirtualizer } from '@tanstack/react-virtual'
import { motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Spinner } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { AGENTS } from './agents'
import { flattenMessages, type useMessages, type WarroomMessage, type WarroomThread } from './api'
import { JumpToLatest } from './JumpToLatest'
import type { LocalMessage } from './live'
import { LiveRunRow } from './LiveRunRow'
import { Medallion } from './Medallion'
import { Message } from './Message'
import type { LiveRun } from './runs'
import { ARRIVE, useArrivals } from './useArrivals'
import { InstrumentId } from './text'
import { around } from './tokens'
import { baghdadDay, dayLabel, formatDate } from './time'

/*
 * The feed (design v4 War Room §7, §8): newest at the bottom, older pages loaded when the reader
 * reaches the top, virtualised with dynamic measurement so a 10,000-message day keeps a few dozen
 * nodes in the DOM (brief §19). Rows are the top block (spinner / origin), Baghdad day separators
 * and messages, keyed by message id.
 */

type Row =
  | { kind: 'top'; key: 'top' }
  | { kind: 'day'; key: string; ts: string }
  | { kind: 'msg'; key: string; message: WarroomMessage; prev: WarroomMessage | undefined }
  | { kind: 'run'; key: string; run: LiveRun }

/** Starts loading the next older page this close to the top (px). */
const LOAD_OLDER_WITHIN = 240
/** "At the bottom" tolerance (px): new rows keep the feed pinned only from here. */
const BOTTOM_SLACK = 48
const FLASH_MS = 1600

/** Messages oldest first, with each live run placed by its first event's time (design v7 §9). */
function buildRows(messages: WarroomMessage[], runs: LiveRun[]): Row[] {
  const rows: Row[] = [{ kind: 'top', key: 'top' }]
  let day: string | null = null
  let prev: WarroomMessage | undefined
  let r = 0
  const dayRow = (ts: string) => {
    const d = baghdadDay(ts)
    if (d === day) return
    rows.push({ kind: 'day', key: `day-${d}`, ts })
    day = d
    prev = undefined
  }
  const pushRunsBefore = (ts: string | null) => {
    while (r < runs.length && (ts === null || Date.parse(runs[r]!.startedAt) < Date.parse(ts))) {
      const run = runs[r++]!
      dayRow(run.startedAt)
      rows.push({ kind: 'run', key: `run-${run.id}`, run })
      prev = undefined
    }
  }
  for (const m of messages) {
    pushRunsBefore(m.created_at)
    dayRow(m.created_at)
    rows.push({ kind: 'msg', key: m.id, message: m, prev })
    prev = m
  }
  pushRunsBefore(null)
  return rows
}

function Origin({ thread, threadName }: { thread: WarroomThread; threadName: string }) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const [before, after] = around(t('origin.title', { thread: '\u0000' }))
  return (
    <div className="wr-origin" data-testid="wr-origin">
      {thread.kind === 'agent' ? (
        <Medallion slug={thread.key} size={40} />
      ) : (
        <span className="wr-origin-medals">
          {AGENTS.map((a) => (
            <Medallion key={a.slug} slug={a.slug} size={30} />
          ))}
        </span>
      )}
      <p className="text-headline">
        {before}
        {thread.kind === 'instrument' ? <InstrumentId id={thread.key} /> : threadName}
        {after}
      </p>
      <p>{t('origin.body', { date: formatDate(thread.created_at, lang, true) })}</p>
    </div>
  )
}

export interface FeedProps {
  thread: WarroomThread
  threadName: string
  query: ReturnType<typeof useMessages>
  /** The owner's messages that are sending or failed, drawn after the stored ones. */
  pending?: LocalMessage[]
  /** Live agent runs in this thread (ATL-97), each drawn as the message being written. */
  runs?: LiveRun[]
  /** Starts a reply to a message: the ReplyChip appears above the composer (ATL-42). */
  onReply: (message: WarroomMessage) => void
}

const NO_PENDING: LocalMessage[] = []
const NO_RUNS: LiveRun[] = []

export function Feed({ thread, threadName, query, pending = NO_PENDING, runs = NO_RUNS, onReply }: FeedProps) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const now = useNow(60_000)
  const reduceMotion = useReducedMotion()
  const messages = useMemo<WarroomMessage[]>(() => [...flattenMessages(query.data), ...pending], [query.data, pending])
  const rows = useMemo(() => buildRows(messages, runs), [messages, runs])
  const byId = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages])
  const scrollRef = useRef<HTMLDivElement>(null)

  // TanStack Virtual returns fresh functions each render by design (its own docs); nothing here
  // memoises them, so the compiler warning does not apply.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === 'day' ? 56 : rows[i]?.kind === 'top' ? 48 : 72),
    getItemKey: (i) => rows[i]!.key,
    overscan: 6,
    paddingStart: 18,
    paddingEnd: 148,
  })

  // ---- Pinning to the bottom, and keeping the place when older pages arrive ----------------
  const atBottom = useRef(true)
  const firstKey = useRef<string | undefined>(undefined)
  const lastTotal = useRef(0)
  const initialised = useRef(false)
  const total = virtualizer.getTotalSize()

  // Live arrivals (the fade-in, the pill, scroll-to-latest). Before the pinning effect below.
  const scrollToEnd = useCallback(
    (smooth: boolean) => virtualizer.scrollToIndex(rows.length - 1, { align: 'end', behavior: smooth ? 'smooth' : 'auto' }),
    [rows.length, virtualizer],
  )
  const isAtBottom = useCallback(() => atBottom.current, [])
  const pinToBottom = useCallback(() => {
    atBottom.current = true
  }, [])
  const arrivals = useArrivals({ messages, isAtBottom, pinToBottom, scrollToEnd, reduceMotion: Boolean(reduceMotion) })

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const first = rows[1]?.key
    if (!initialised.current && rows.length > 1) {
      initialised.current = true
      virtualizer.scrollToIndex(rows.length - 1, { align: 'end' })
    } else if (first && firstKey.current && first !== firstKey.current && lastTotal.current > 0) {
      // An older page was prepended: move down by what was added, so the reader stays put.
      el.scrollTop += virtualizer.getTotalSize() - lastTotal.current
    } else if (atBottom.current) {
      el.scrollTop = el.scrollHeight
    }
    firstKey.current = first
    lastTotal.current = virtualizer.getTotalSize()
  }, [rows, total, virtualizer])

  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = query
  const maybeLoadOlder = useCallback(() => {
    const el = scrollRef.current
    if (!el || !hasNextPage || isFetchingNextPage || isFetchNextPageError) return
    if (el.scrollTop <= LOAD_OLDER_WITHIN) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage])

  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK
    arrivals.onScrolled(atBottom.current)
    maybeLoadOlder()
  }
  // A short first page may not fill the viewport; keep loading until it does.
  useEffect(() => {
    maybeLoadOlder()
  }, [maybeLoadOlder, rows.length])

  // ---- Jumping to a quoted parent ------------------------------------------------------------
  const [flashId, setFlashId] = useState<string | null>(null)
  const onJump = useCallback(
    (id: string) => {
      const index = rows.findIndex((r) => r.key === id)
      if (index < 0) return
      virtualizer.scrollToIndex(index, { align: 'center' })
      setFlashId(id)
      window.setTimeout(() => setFlashId((cur) => (cur === id ? null : cur)), FLASH_MS)
    },
    [rows, virtualizer],
  )

  // ---- Roving focus and the one active message ----------------------------------------------
  const messageIndexes = useMemo(() => rows.flatMap((r, i) => (r.kind === 'msg' ? [i] : [])), [rows])
  const [focusId, setFocusId] = useState<string | null>(null)
  const tabbableId = focusId && byId.has(focusId) ? focusId : messages[messages.length - 1]?.id
  const pendingFocus = useRef<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const lastPointer = useRef<{ x: number; y: number } | null>(null)
  // While a message's context menu is open, it stays active no matter what else moves or
  // focuses (design v4 War Room §7, MessageActions). Two id refs pin against the "latest input
  // wins" rule below; `trySetActive` is the only setter that respects them.
  const menuOpenId = useRef<string | null>(null)

  const activatable = useCallback((el: Element | null) => {
    const node = el?.closest<HTMLElement>('[data-msg-id]')
    return node && (node.dataset.shape === 'agent' || node.dataset.shape === 'owner') ? node.dataset.msgId! : null
  }, [])

  /** The message whose own action bar or menu currently holds visible keyboard focus, if any. */
  const barFocusedId = useCallback(() => {
    const el = document.activeElement
    if (!el || !scrollRef.current?.contains(el) || !el.matches(':focus-visible')) return null
    return el.closest('.msg-actions, .msg-menu') ? activatable(el) : null
  }, [activatable])

  const trySetActive = useCallback(
    (id: string | null) => {
      if (menuOpenId.current && id !== menuOpenId.current) return
      setActiveId(id)
    },
    [],
  )

  /** Recomputes activeId from the current focus, e.g. after a pointer leaves or a menu closes. */
  const recomputeActive = useCallback(() => {
    const focused = document.activeElement
    trySetActive(focused && scrollRef.current?.contains(focused) && focused.matches(':focus-visible') ? activatable(focused) : null)
  }, [activatable, trySetActive])

  const onMenuChange = useCallback(
    (id: string, open: boolean) => {
      if (open) {
        menuOpenId.current = id
        setActiveId(id)
      } else {
        menuOpenId.current = null
        recomputeActive()
      }
    },
    [recomputeActive],
  )

  useEffect(() => {
    const id = pendingFocus.current
    if (!id) return
    const el = scrollRef.current?.querySelector<HTMLElement>(`[data-msg-id="${CSS.escape(id)}"]`)
    if (el) {
      pendingFocus.current = null
      el.focus({ preventScroll: true })
    }
  })

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    const target = e.target as HTMLElement
    const onMessage = target.matches('[data-msg-id]')
    if (!onMessage && target !== scrollRef.current) return
    const currentId = onMessage ? target.dataset.msgId : tabbableId
    const pos = messageIndexes.findIndex((i) => rows[i]!.key === currentId)
    if (pos < 0) return
    const nextPos = target === scrollRef.current ? pos : e.key === 'ArrowDown' ? Math.min(pos + 1, messageIndexes.length - 1) : Math.max(pos - 1, 0)
    const rowIndex = messageIndexes[nextPos]!
    const id = rows[rowIndex]!.key
    e.preventDefault()
    virtualizer.scrollToIndex(rowIndex, { align: 'auto' })
    setFocusId(id)
    trySetActive(id)
    pendingFocus.current = id
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    // Content scrolling under a still pointer is not a pointer move.
    const last = lastPointer.current
    if (last && last.x === e.clientX && last.y === e.clientY) return
    lastPointer.current = { x: e.clientX, y: e.clientY }
    // Keyboard focus on a bar's own button holds the bar until focus leaves it.
    if (barFocusedId()) return
    trySetActive(activatable(e.target as Element))
  }

  function onPointerLeave() {
    lastPointer.current = null
    recomputeActive()
  }

  function onFocus(e: FocusEvent<HTMLDivElement>) {
    const id = (e.target as HTMLElement).closest<HTMLElement>('[data-msg-id]')?.dataset.msgId
    if (id) setFocusId(id)
    if ((e.target as HTMLElement).matches(':focus-visible')) trySetActive(activatable(e.target as Element))
  }

  const items = virtualizer.getVirtualItems()

  return (
    <>
    <div
      ref={scrollRef}
      className="wr-scroller"
      role="log"
      aria-label={t('feed', { thread: threadName })}
      tabIndex={0}
      onScroll={onScroll}
      onKeyDown={onKeyDown}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      onFocus={onFocus}
      data-testid="wr-feed"
    >
      <div className="wr-column">
        <div className="wr-sizer" style={{ height: total }}>
        {items.map((item) => {
          const row = rows[item.index]!
          return (
            <div
              key={item.key}
              data-index={item.index}
              ref={virtualizer.measureElement}
              className="wr-row"
              style={{ transform: `translateY(${item.start}px)` }}
            >
              {row.kind === 'top' ? (
                hasNextPage || isFetchNextPageError ? (
                  <div className="wr-top" data-testid="wr-top">
                    {isFetchNextPageError ? (
                      <p className="wr-top-line" role="alert">
                        <span>{t('olderError')}</span>
                        <Button variant="plain" size="sm" onClick={() => void fetchNextPage()}>
                          {t('tryAgain')}
                        </Button>
                      </p>
                    ) : isFetchingNextPage ? (
                      <p className="wr-top-line" role="status">
                        <Spinner />
                        <span>{t('loadingOlder')}</span>
                      </p>
                    ) : (
                      <p className="wr-top-line wr-top-idle" />
                    )}
                  </div>
                ) : (
                  <Origin thread={thread} threadName={threadName} />
                )
              ) : row.kind === 'day' ? (
                <div className="wr-day" role="separator" data-day={baghdadDay(row.ts)}>
                  <span>{dayLabel(row.ts, lang, now, { today: t('today'), yesterday: t('yesterday') })}</span>
                </div>
              ) : row.kind === 'run' ? (
                <LiveRunRow run={row.run} parent={row.run.replyTo ? byId.get(row.run.replyTo) : undefined} lang={lang} onJump={onJump} />
              ) : (
                <motion.div
                  initial={arrivals.fresh.has(row.key) ? { opacity: 0, y: 8 } : false}
                  animate={{ opacity: 1, y: 0 }}
                  transition={ARRIVE}
                >
                  <Message
                    message={row.message}
                    prev={row.prev}
                    parent={row.message.reply_to ? byId.get(row.message.reply_to) : undefined}
                    lang={lang}
                    active={activeId === row.key}
                    tabbable={tabbableId === row.key}
                    flash={flashId === row.key}
                    onJump={onJump}
                    onReply={onReply}
                    onMenuChange={(open) => onMenuChange(row.key, open)}
                  />
                </motion.div>
              )}
            </div>
          )
        })}
        </div>
      </div>
    </div>
    <JumpToLatest unread={arrivals.unread} scrolledUp={arrivals.scrolledUp} onJump={arrivals.scrollToLatest} />
    </>
  )
}

