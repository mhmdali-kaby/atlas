import { PanelRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { flattenMessages, useMessages } from './api'
import { Feed, FeedEmpty, FeedError, FeedNotFound, FeedSkeleton } from './Feed'
import { Inspector } from './Inspector'
import { useSelectedThread } from './selection'
import { useThreadName } from './shape'
import './warroom.css'

/*
 * The War Room (brief §12, design v4 War Room §6): the feed in a centred column and the
 * inspector at the inline end. The composer (ATL-40) floats over the feed's bottom edge.
 */

const INSPECTOR_KEY = 'atlas.wr.inspector'
const WIDE_QUERY = '(min-width: 1280px)'

function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia?.(WIDE_QUERY).matches ?? true)
  useEffect(() => {
    const mq = window.matchMedia?.(WIDE_QUERY)
    if (!mq) return
    const on = () => setWide(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return wide
}

/**
 * From 1280px the inspector is open by default and the toggle is remembered; below it starts
 * closed and opens over the feed.
 */
function useInspector() {
  const wide = useWide()
  const [open, setOpen] = useState(() => localStorage.getItem(INSPECTOR_KEY) !== 'closed')
  // Below 1280 the overlay is separate state; it only counts while the window is narrow.
  const [overlayOpen, setOverlayOpen] = useState(false)
  const overlay = !wide && overlayOpen
  const toggle = () => {
    if (wide) {
      localStorage.setItem(INSPECTOR_KEY, open ? 'closed' : 'open')
      setOpen(!open)
    } else setOverlayOpen(!overlay)
  }
  return { shown: wide ? open : overlay, overlay, toggle }
}

export function WarRoomPage() {
  const { t } = useTranslation('warroom')
  const { env, threads, selected } = useSelectedThread()
  const nameOf = useThreadName()
  const messages = useMessages(env, selected?.id)
  const inspector = useInspector()
  // The shell's toolbar has a slot for page actions; the inspector toggle goes there.
  const [toolbarSlot, setToolbarSlot] = useState<Element | null>(null)
  const findSlot = (el: HTMLDivElement | null) => {
    if (el) setToolbarSlot(document.querySelector('[data-slot="toolbar-actions"]'))
  }

  const loading = threads.isPending || (Boolean(selected) && messages.isPending)
  // A failed older page keeps the feed (the top row says so); only a feed with nothing to show is an error.
  const error = (threads.isError && !threads.data) || (messages.isError && !messages.data)
  const list = flattenMessages(messages.data)
  const threadName = nameOf(selected)

  let feed
  if (error) {
    feed = <FeedError onRetry={() => void (threads.isError ? threads.refetch() : messages.refetch())} />
  } else if (loading) {
    feed = <FeedSkeleton />
  } else if (!selected) {
    // Threads have loaded and the URL's thread (or Main) is not among them.
    feed = <FeedNotFound />
  } else if (list.length === 0) {
    feed = <FeedEmpty thread={selected} />
  } else {
    // Keyed by thread so a new thread starts at its own bottom with fresh scroll state.
    feed = <Feed key={selected.id} thread={selected} threadName={threadName} query={messages} />
  }

  return (
    <div ref={findSlot} className="wr" data-inspector={inspector.shown ? 'open' : 'closed'} data-overlay={inspector.overlay || undefined}>
      <section className="wr-pane" aria-label={threadName ? t('feed', { thread: threadName }) : undefined}>
        {feed}
      </section>
      {inspector.shown && (
        <Inspector thread={selected} threadName={threadName} loading={loading} lastMessageAt={list[list.length - 1]?.created_at ?? null} />
      )}
      {toolbarSlot &&
        createPortal(
          <button
            type="button"
            className={toolbarIconButton}
            aria-label={t('toggleInspector')}
            title={t('toggleInspector')}
            aria-pressed={inspector.shown}
            aria-controls="wr-inspector"
            onClick={inspector.toggle}
            data-testid="wr-inspector-toggle"
          >
            <PanelRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
          </button>,
          toolbarSlot,
        )}
    </div>
  )
}
