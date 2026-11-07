import { useEffect, useRef } from 'react'

/** Input that counts as the owner being present. */
const ACTIVITY_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'scroll'] as const

/**
 * Calls `onIdle` once after `timeoutMs` with no input (brief §18). Any input restarts
 * the clock. Inactive while `enabled` is false.
 */
export function useIdleLock(enabled: boolean, timeoutMs: number, onIdle: () => void): void {
  const onIdleRef = useRef(onIdle)
  useEffect(() => {
    onIdleRef.current = onIdle
  }, [onIdle])

  useEffect(() => {
    if (!enabled) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const restart = () => {
      clearTimeout(timer)
      timer = setTimeout(() => onIdleRef.current(), timeoutMs)
    }
    restart()
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, restart, { passive: true, capture: true })
    return () => {
      clearTimeout(timer)
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, restart, { capture: true })
    }
  }, [enabled, timeoutMs])
}
