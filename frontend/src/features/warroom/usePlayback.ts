import { useEffect, useRef, useState } from 'react'
import { clampAt, DAY_MS, type Speed } from './replayTime'

/** The playhead and playback (design v18: 1×, 10×, 60×; a tick every 250 ms, or every second with reduced motion). */
export function usePlayback(dayStart: number) {
  const end = dayStart + DAY_MS - 1
  const [at, setAtState] = useState(end)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<Speed>(60)
  const atRef = useRef(at)
  const setAt = (next: number) => {
    atRef.current = clampAt(next, dayStart)
    setAtState(atRef.current)
  }
  useEffect(() => {
    if (!playing) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const tick = reduce ? 1000 : 250
    const id = window.setInterval(() => {
      const next = atRef.current + speed * tick
      if (next >= end) {
        atRef.current = end
        setAtState(end)
        setPlaying(false)
        return
      }
      atRef.current = next
      setAtState(next)
    }, tick)
    return () => window.clearInterval(id)
  }, [playing, speed, end])
  const toggle = () => {
    if (!playing && atRef.current >= end - 60_000) setAt(dayStart)
    setPlaying(!playing)
  }
  return { at, setAt, playing, speed, setSpeed, toggle, pause: () => setPlaying(false) }
}
