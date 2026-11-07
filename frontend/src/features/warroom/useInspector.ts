import { useEffect, useState } from 'react'

/*
 * The War Room inspector's open state (design v4 War Room; ATL-207 shares it with Replay, which
 * remembers its own toggle under a separate key).
 */

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
export function useInspector(storageKey = 'atlas.wr.inspector') {
  const wide = useWide()
  const [open, setOpen] = useState(() => localStorage.getItem(storageKey) !== 'closed')
  // Below 1280 the overlay is separate state; it only counts while the window is narrow.
  const [overlayOpen, setOverlayOpen] = useState(false)
  const overlay = !wide && overlayOpen
  const toggle = () => {
    if (wide) {
      localStorage.setItem(storageKey, open ? 'closed' : 'open')
      setOpen(!open)
    } else setOverlayOpen(!overlay)
  }
  /** Close the narrow-window overlay (a moment picked in it jumps the feed underneath). */
  const closeOverlay = () => setOverlayOpen(false)
  /** Show it, whatever the width (an instrument picked with ⌘K, ATL-250). Not remembered. */
  const reveal = () => {
    if (wide) setOpen(true)
    else setOverlayOpen(true)
  }
  return { shown: wide ? open : overlay, overlay, toggle, closeOverlay, reveal }
}
