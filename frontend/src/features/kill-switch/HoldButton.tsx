import { OctagonX } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../../ui/cx'
import { HOLD_MS } from './api'
import { NS } from './parts'

export type Hint = 'idle' | 'holding' | 'early'

/**
 * Press and hold for 1.5 s (decision 251): pointer, or Space/Enter held. Released early,
 * nothing is sent and the hint says so. Clicks alone never fire it.
 */
export function HoldButton({
  onComplete,
  onHint,
  describedBy,
}: {
  onComplete: () => void
  /** Drives the hint line the parent draws above the buttons. */
  onHint: (hint: Hint) => void
  describedBy: string
}) {
  const { t } = useTranslation(NS)
  const [holding, setHolding] = useState(false)
  const [done, setDone] = useState(false)
  const timer = useRef<number | null>(null)
  const setHint = (h: Hint) => {
    setHolding(h === 'holding')
    onHint(h)
  }

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  function start() {
    if (timer.current !== null || done) return
    setHint('holding')
    timer.current = window.setTimeout(() => {
      timer.current = null
      setDone(true)
      onComplete()
    }, HOLD_MS)
  }

  function stop() {
    if (timer.current === null) return
    window.clearTimeout(timer.current)
    timer.current = null
    setHint('early')
  }

  const isKey = (e: KeyboardEvent) => e.key === ' ' || e.key === 'Enter'

  return (
    <button
      type="button"
      className={cx('ks-hold', holding && 'is-holding', done && 'is-done')}
      aria-describedby={describedBy}
      data-testid="kill-hold"
      onPointerDown={(e: PointerEvent<HTMLButtonElement>) => {
        if (e.button > 0) return
        try {
          e.currentTarget.setPointerCapture(e.pointerId)
        } catch {
          /* not capturable (tests, some browsers) */
        }
        start()
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onKeyDown={(e) => {
        if (!isKey(e)) return
        e.preventDefault()
        if (!e.repeat) start()
      }}
      onKeyUp={(e) => {
        if (!isKey(e)) return
        e.preventDefault()
        stop()
      }}
      onBlur={stop}
      onClick={(e) => e.preventDefault()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <span className="ks-hold-fill" aria-hidden="true" />
      <span className="ks-hold-label">
        <OctagonX aria-hidden="true" strokeWidth={1.75} />
        <span>{t('hold')}</span>
      </span>
    </button>
  )
}
