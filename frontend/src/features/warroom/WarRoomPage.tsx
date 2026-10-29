import { CircleCheck, PanelRight, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { useSession } from '../auth/session'
import { Button, Spinner } from '../../ui'
import { flattenMessages, useMessages } from './api'
import { Composer } from './Composer'
import { Feed } from './Feed'
import { FeedEmpty, FeedError, FeedNotFound, FeedSkeleton } from './FeedStates'
import { Inspector } from './Inspector'
import { localMessages, useOutbox } from './live'
import { useLiveFeed, type BannerState } from './useLiveFeed'
import { useSelectedThread } from './selection'
import { useThreadName } from './shape'
import './warroom.css'

/*
 * The War Room (brief §12, design v4 War Room §6): the feed in a centred column and the
 * inspector at the inline end. The composer (ATL-40) floats over the feed's bottom edge, the
 * connection banner over its top, and a polite live region announces arrivals.
 */

function ConnectionBanner({ banner, onReconnect }: { banner: BannerState; onReconnect: () => void }) {
  const { t } = useTranslation('warroom')
  if (!banner) return null
  if (banner.kind === 'down') {
    return (
      <div className="wr-banner is-down" role="status" data-testid="wr-banner-down">
        <WifiOff aria-hidden="true" strokeWidth={1.5} className="icon" />
        <span>{t('live.down')}</span>
        <Spinner />
        <Button variant="secondary" size="sm" onClick={onReconnect}>
          {t('live.reconnect')}
        </Button>
      </div>
    )
  }
  return (
    <div className="wr-banner is-back" role="status" data-testid="wr-banner-back">
      <CircleCheck aria-hidden="true" strokeWidth={1.5} className="icon" />
      <span>{banner.missed > 0 ? t('live.backMissed', { count: banner.missed }) : t('live.back')}</span>
    </div>
  )
}

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
  const list = useMemo(() => flattenMessages(messages.data), [messages.data])
  const threadName = nameOf(selected)
  const live = useLiveFeed(env, selected?.id)
  const outbox = useOutbox((s) => s.items)
  // Local bubbles carry the signed-in owner's name, as the stored row will (the shell has the session cached).
  const ownerName = useSession().data?.name ?? ''
  const pending = useMemo(
    () => (selected ? localMessages(outbox, env, selected.id, list, ownerName) : []),
    [outbox, env, selected, list, ownerName],
  )

  let feed
  if (error) {
    feed = <FeedError onRetry={() => void (threads.isError ? threads.refetch() : messages.refetch())} />
  } else if (loading) {
    feed = <FeedSkeleton />
  } else if (!selected) {
    // Threads have loaded and the URL's thread (or Main) is not among them.
    feed = <FeedNotFound />
  } else if (list.length === 0 && pending.length === 0) {
    feed = <FeedEmpty thread={selected} />
  } else {
    // Keyed by thread so a new thread starts at its own bottom with fresh scroll state.
    feed = <Feed key={selected.id} thread={selected} threadName={threadName} query={messages} pending={pending} />
  }

  return (
    <div ref={findSlot} className="wr" data-inspector={inspector.shown ? 'open' : 'closed'} data-overlay={inspector.overlay || undefined}>
      <section className="wr-pane" aria-label={threadName ? t('feed', { thread: threadName }) : undefined}>
        <ConnectionBanner banner={live.banner} onReconnect={live.reconnect} />
        {feed}
        <Composer
          key={`${env}:${selected?.id ?? ''}`}
          env={env}
          thread={selected}
          threadName={threadName}
          disabled={error || loading || !selected}
        />
        <div className="sr-only" aria-live="polite" data-testid="wr-live">
          {live.announcement}
        </div>
      </section>
      {inspector.shown && (
        <Inspector env={env} thread={selected} threadName={threadName} loading={loading} lastMessageAt={list[list.length - 1]?.created_at ?? null} />
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
