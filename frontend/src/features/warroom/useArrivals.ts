import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { WarroomMessage } from './api'
import { isLocal } from './live'

/*
 * Live arrivals in the feed (design v4 War Room §8 "Live arrival", §10): which new rows fade in,
 * how many arrived while the reader was scrolled up (the "N new messages" pill), and whether the
 * scroll-to-latest button shows. The feed keeps the virtualiser and the pinning; this hook only
 * classifies what changed between renders.
 *
 * Call it before the feed's own pinning effect: when the owner sends, it calls `pinToBottom` so
 * the pinning effect in the same commit follows the new bubble to the bottom.
 */

/** A live arrival fades and rises 8px over 300ms. */
export const ARRIVE = { duration: 0.3, ease: [0.22, 1, 0.36, 1] as const }
const FRESH_MS = 400

export interface ArrivalsInput {
  /** The feed's messages, oldest first, local (sending/failed) ones last. */
  messages: WarroomMessage[]
  /** Whether the reader is at the bottom (the feed's pinning state). */
  isAtBottom: () => boolean
  /** Marks the reader as at the bottom, so the feed's pinning follows new rows. */
  pinToBottom: () => void
  /** Scrolls to the newest row; `smooth` unless reduced motion. */
  scrollToEnd: (smooth: boolean) => void
  reduceMotion: boolean
}

export function useArrivals({ messages, isAtBottom, pinToBottom, scrollToEnd, reduceMotion }: ArrivalsInput) {
  const seen = useRef<Set<string> | null>(null)
  const storedNewest = useRef('')
  const [unread, setUnread] = useState(0)
  const [scrolledUp, setScrolledUp] = useState(false)
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set())

  useLayoutEffect(() => {
    const newestStored = (list: WarroomMessage[]) => list.reduce((ts, m) => (!isLocal(m) && m.created_at > ts ? m.created_at : ts), '')
    if (!seen.current) {
      // The first render is history, not arrivals.
      seen.current = new Set(messages.map((m) => m.id))
      storedNewest.current = newestStored(messages)
      return
    }
    const known = seen.current
    const since = storedNewest.current
    // New ids newer than what was stored before: live arrivals and the owner's local bubbles.
    // An older page is never newer; the stored row that replaces a local bubble is the owner's.
    const added = messages.filter((m) => !known.has(m.id) && (isLocal(m) || m.created_at >= since))
    for (const m of messages) known.add(m.id)
    storedNewest.current = newestStored(messages) || since
    if (added.length === 0) return
    if (added.some((m) => isLocal(m))) {
      // The owner just sent: follow it to the bottom.
      pinToBottom()
      setUnread(0)
    } else if (!isAtBottom()) {
      const others = added.filter((m) => m.author_type !== 'human').length
      if (others) setUnread((n) => n + others)
    }
    const animate = added.filter((m) => isLocal(m) || m.author_type !== 'human')
    if (!reduceMotion && animate.length) setFresh((s) => new Set([...s, ...animate.map((m) => m.id)]))
  }, [messages, reduceMotion, isAtBottom, pinToBottom])

  // A row animates once; after that a re-mount (scrolling it back into view) draws it still.
  useEffect(() => {
    if (fresh.size === 0) return
    const timer = window.setTimeout(() => setFresh(new Set()), FRESH_MS)
    return () => window.clearTimeout(timer)
  }, [fresh])

  const scrollToLatest = useCallback(() => {
    pinToBottom()
    setUnread(0)
    setScrolledUp(false)
    scrollToEnd(!reduceMotion)
  }, [pinToBottom, scrollToEnd, reduceMotion])

  /** The feed's scroll handler reports where the reader is after each scroll. */
  const onScrolled = useCallback((bottom: boolean) => {
    setScrolledUp(!bottom)
    if (bottom) setUnread(0)
  }, [])

  return { unread, scrolledUp, fresh, scrollToLatest, onScrolled }
}
