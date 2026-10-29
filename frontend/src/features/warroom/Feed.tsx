import { useVirtualizer } from '@tanstack/react-virtual'
import { CloudOff, MessagesSquare, ChartCandlestick, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FocusEvent, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Skeleton, Spinner } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { AGENTS } from './agents'
import { flattenMessages, type useMessages, type WarroomMessage, type WarroomThread } from './api'
import { Medallion } from './Medallion'
import { Message } from './Message'
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

/** Starts loading the next older page this close to the top (px). */
const LOAD_OLDER_WITHIN = 240
/** "At the bottom" tolerance (px): new rows keep the feed pinned only from here. */
const BOTTOM_SLACK = 48
const FLASH_MS = 1600

function buildRows(messages: WarroomMessage[]): Row[] {
  const rows: Row[] = [{ kind: 'top', key: 'top' }]
  let day: string | null = null
  let prev: WarroomMessage | undefined
  for (const m of messages) {
    const d = baghdadDay(m.created_at)
    if (d !== day) {
      rows.push({ kind: 'day', key: `day-${d}`, ts: m.created_at })
      day = d
      prev = undefined
    }
    rows.push({ kind: 'msg', key: m.id, message: m, prev })
    prev = m
  }
  return rows
}

export function FeedSkeleton() {
  const { t } = useTranslation('warroom')
  const agent = (w1: number, w2: number) => (
    <div className="msg-skel">
      <Skeleton className="skel-round" />
      <span className="lines">
        <Skeleton style={{ width: 120, height: 12 }} />
        <Skeleton style={{ width: `${w1}%`, height: 12 }} />
        {w2 > 0 && <Skeleton style={{ width: `${w2}%`, height: 12 }} />}
      </span>
    </div>
  )
  const owner = (w: number) => (
    <div className="msg-skel is-owner">
      <Skeleton style={{ width: w, height: 38, borderRadius: 18 }} />
    </div>
  )
  return (
    <div className="wr-column" aria-busy="true" aria-label={t('loading')} role="status" data-testid="wr-feed-loading">
      {agent(88, 64)}
      {agent(76, 40)}
      {owner(260)}
      {agent(92, 70)}
      {agent(58, 0)}
      {owner(180)}
      {agent(84, 52)}
    </div>
  )
}

export function FeedError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('warroom')
  return (
    <div className="wr-column">
      <div className="state-block" role="alert" data-testid="wr-feed-error">
        <div className="state-icon is-error">
          <CloudOff aria-hidden="true" strokeWidth={1.5} />
        </div>
        <h2 className="text-title-3">{t('error.title')}</h2>
        <p className="state-body">
          {t('error.bodyBefore')}{' '}
          <code className="cmd" dir="ltr">
            ./atlas up
          </code>
          {t('error.bodyAfter')}
        </p>
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw aria-hidden="true" strokeWidth={1.5} className="size-4" />
          <span>{t('tryAgain')}</span>
        </Button>
      </div>
    </div>
  )
}

export function FeedEmpty({ thread }: { thread: WarroomThread }) {
  const { t } = useTranslation('warroom')
  const inst = thread.kind === 'instrument'
  const Icon = inst ? ChartCandlestick : MessagesSquare
  return (
    <div className="wr-column">
      <div className="state-block" data-testid="wr-feed-empty">
        <div className="state-icon">
          <Icon aria-hidden="true" strokeWidth={1.5} />
        </div>
        <h2 className="text-title-3">
          {inst ? (
            <>
              {around(t('empty.instTitle', { inst: '\u0000' }))[0]}
              <InstrumentId id={thread.key} />
              {around(t('empty.instTitle', { inst: '\u0000' }))[1]}
            </>
          ) : (
            t('empty.title')
          )}
        </h2>
        <p className="state-body">{t(inst ? 'empty.instBody' : 'empty.body')}</p>
      </div>
    </div>
  )
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
}

export function Feed({ thread, threadName, query }: FeedProps) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const now = useNow(60_000)
  const messages = useMemo(() => flattenMessages(query.data), [query.data])
  const rows = useMemo(() => buildRows(messages), [messages])
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
    setActiveId(id)
    pendingFocus.current = id
  }

  const activatable = (el: Element | null) => {
    const node = el?.closest<HTMLElement>('[data-msg-id]')
    return node && (node.dataset.shape === 'agent' || node.dataset.shape === 'owner') ? node.dataset.msgId! : null
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    // Content scrolling under a still pointer is not a pointer move.
    const last = lastPointer.current
    if (last && last.x === e.clientX && last.y === e.clientY) return
    lastPointer.current = { x: e.clientX, y: e.clientY }
    setActiveId(activatable(e.target as Element))
  }

  function onPointerLeave() {
    lastPointer.current = null
    const focused = document.activeElement
    setActiveId(focused && scrollRef.current?.contains(focused) && focused.matches(':focus-visible') ? activatable(focused) : null)
  }

  function onFocus(e: FocusEvent<HTMLDivElement>) {
    const id = (e.target as HTMLElement).closest<HTMLElement>('[data-msg-id]')?.dataset.msgId
    if (id) setFocusId(id)
    if ((e.target as HTMLElement).matches(':focus-visible')) setActiveId(activatable(e.target as Element))
  }

  const items = virtualizer.getVirtualItems()

  return (
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
              ) : (
                <Message
                  message={row.message}
                  prev={row.prev}
                  parent={row.message.reply_to ? byId.get(row.message.reply_to) : undefined}
                  lang={lang}
                  active={activeId === row.key}
                  tabbable={tabbableId === row.key}
                  flash={flashId === row.key}
                  onJump={onJump}
                />
              )}
            </div>
          )
        })}
        </div>
      </div>
    </div>
  )
}

