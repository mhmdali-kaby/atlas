import { useVirtualizer } from '@tanstack/react-virtual'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useLang } from '../portfolio/hooks'
import type { WarroomMessage } from './api'
import { Message } from './Message'
import { ReadOnlyContext } from './readOnly'

/*
 * The Replay feed (ATL-206, design v18 Replay): one Baghdad day's messages in order, drawn by the
 * live War Room's own Message renderer and cards inside a read-only provider. Virtualised exactly
 * like the live feed (TanStack Virtual, dynamic measurement, stable message-id keys), so a
 * 10,000-message day keeps a few dozen nodes in the DOM (brief §19). It opens at the day's end.
 * Nothing here subscribes to the socket: the list is the day's response and nothing else.
 */

const FLASH_MS = 1600
/** "At the end" tolerance (px), as in the live feed. */
const BOTTOM_SLACK = 48
const NO_REPLY = () => {}
const NO_MENU = () => {}

export interface ReplayJump {
  id: string
  /** Bumped on every request, so asking for the same message twice jumps twice. */
  seq: number
  /** Move keyboard focus onto the message (default). An arrow-key step on the day line keeps focus on the playhead. */
  focus?: boolean
}

/**
 * `follow` (ATL-207): bumped whenever the day line moves the playhead by dragging or playing; the
 * feed goes back to its newest shown message and stays pinned there.
 */
export function ReplayFeed({ messages, label, jump, follow = 0 }: { messages: WarroomMessage[]; label: string; jump: ReplayJump | null; follow?: number }) {
  const lang = useLang()
  const scrollRef = useRef<HTMLDivElement>(null)
  const byId = useMemo(() => new Map(messages.map((m) => [m.id, m])), [messages])
  const indexOf = useMemo(() => new Map(messages.map((m, i) => [m.id, i])), [messages])

  // TanStack Virtual returns fresh functions each render by design; nothing here memoises them.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 72,
    getItemKey: (i) => messages[i]!.id,
    overscan: 6,
    paddingStart: 18,
    paddingEnd: 24,
  })

  // The day as it ended: open at the last message, and stay there while rows measure in (their
  // real heights replace the estimate a frame later), until the reader scrolls away.
  const atBottom = useRef(true)
  const opened = useRef(false)
  const total = virtualizer.getTotalSize()
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el || messages.length === 0) return
    if (!opened.current) {
      opened.current = true
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end' })
    } else if (atBottom.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [messages.length, total, virtualizer])
  // The day line moved the playhead: show the newest message it reaches.
  const followed = useRef(follow)
  useLayoutEffect(() => {
    if (followed.current === follow || messages.length === 0) return
    followed.current = follow
    atBottom.current = true
    virtualizer.scrollToIndex(messages.length - 1, { align: 'end' })
  }, [follow, messages.length, virtualizer])
  const onScroll = () => {
    const el = scrollRef.current
    if (el) atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK
  }

  const [flashId, setFlashId] = useState<string | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const pendingFocus = useRef<string | null>(null)

  const onJump = useCallback(
    (id: string) => {
      const index = indexOf.get(id)
      if (index === undefined) return
      atBottom.current = false
      virtualizer.scrollToIndex(index, { align: 'center' })
      setFlashId(id)
      window.setTimeout(() => setFlashId((cur) => (cur === id ? null : cur)), FLASH_MS)
    },
    [indexOf, virtualizer],
  )

  // A moment picked in the inspector or on the day line: scroll there, flash it and move focus
  // onto it. Each request is handled once: the shown list changing under playback recreates
  // onJump, and that must not jump back to an old request.
  const handled = useRef<ReplayJump | null>(null)
  useEffect(() => {
    if (!jump || handled.current === jump) return
    handled.current = jump
    onJump(jump.id)
    if (jump.focus === false) return
    setFocusId(jump.id)
    pendingFocus.current = jump.id
  }, [jump, onJump])

  useEffect(() => {
    const id = pendingFocus.current
    if (!id) return
    const el = scrollRef.current?.querySelector<HTMLElement>(`[data-msg-id="${CSS.escape(id)}"]`)
    if (el) {
      pendingFocus.current = null
      el.focus({ preventScroll: true })
    }
  })

  // One tab stop in the list (the focused message, else the last); arrows move between messages.
  const tabbableId = focusId && byId.has(focusId) ? focusId : messages[messages.length - 1]?.id
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return
    const target = e.target as HTMLElement
    const onMessage = target.matches('[data-msg-id]')
    if (!onMessage && target !== scrollRef.current) return
    const current = indexOf.get((onMessage ? target.dataset.msgId : tabbableId) ?? '') ?? messages.length - 1
    const next =
      e.key === 'Home'
        ? 0
        : e.key === 'End'
          ? messages.length - 1
          : target === scrollRef.current
            ? current
            : Math.max(0, Math.min(messages.length - 1, current + (e.key === 'ArrowDown' ? 1 : -1)))
    e.preventDefault()
    atBottom.current = next === messages.length - 1
    const id = messages[next]!.id
    virtualizer.scrollToIndex(next, { align: 'auto' })
    setFocusId(id)
    pendingFocus.current = id
  }

  const items = virtualizer.getVirtualItems()
  return (
    <ReadOnlyContext.Provider value={true}>
      <div
        ref={scrollRef}
        className="wr-scroller rp-scroller"
        role="log"
        aria-label={label}
        aria-live="off"
        tabIndex={0}
        onKeyDown={onKeyDown}
        onScroll={onScroll}
        onFocus={(e) => {
          const id = (e.target as HTMLElement).closest<HTMLElement>('[data-msg-id]')?.dataset.msgId
          if (id) setFocusId(id)
        }}
        data-testid="rp-feed"
      >
        <div className="wr-column">
          <div className="wr-sizer" style={{ height: virtualizer.getTotalSize() }}>
            {items.map((item) => {
              const m = messages[item.index]!
              return (
                <div key={item.key} data-index={item.index} ref={virtualizer.measureElement} className="wr-row" style={{ transform: `translateY(${item.start}px)` }}>
                  <Message
                    message={m}
                    prev={item.index > 0 ? messages[item.index - 1] : undefined}
                    parent={m.reply_to ? byId.get(m.reply_to) : undefined}
                    lang={lang}
                    active={false}
                    tabbable={tabbableId === m.id}
                    flash={flashId === m.id}
                    onJump={onJump}
                    onReply={NO_REPLY}
                    onMenuChange={NO_MENU}
                  />
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </ReadOnlyContext.Provider>
  )
}
