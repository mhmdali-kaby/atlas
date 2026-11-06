import { Pause, Play } from 'lucide-react'
import { useMemo, useRef, type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { SegmentedControl, Skeleton } from '../../ui'
import { useDir } from '../../ui/useDir'
import type { WarroomMessage } from './api'
import { isMoment } from './replay'
import { MOMENT_ICON } from './momentIcons'
import { atFromPointer, BUCKETS, DAY_MS, densityBuckets, linePct, minuteOfDay, SPEEDS, type Speed } from './replayTime'
import { formatClock, formatTime, ownerCity } from './time'

/*
 * The day line (ATL-207; design v18 Replay, "the signature"): 00:00 to 24:00 Baghdad, the day's
 * talk as a density strip, the moments that mattered as markers above it, and a playhead the
 * owner drags, clicks to, or plays at 1×, 10× or 60×. Every position is an inline-start
 * percentage, so the line runs right to left in Arabic with no extra code; the pointer maths
 * flips the same way.
 */

const HOURS = [0, 3, 6, 9, 12, 15, 18, 21, 24]

export interface DayLineProps {
  dayStart: number
  messages: WarroomMessage[]
  times: number[]
  at: number
  shown: number
  playing: boolean
  speed: Speed
  lang: Lang
  /** The playhead dragged or clicked along the track. */
  onSeek: (at: number) => void
  onTogglePlay: () => void
  onSpeed: (speed: Speed) => void
  /** An arrow key on the playhead: the next (1) or previous (-1) message. */
  onStep: (dir: 1 | -1) => void
  /** Home / End on the playhead. */
  onEdge: (edge: 'start' | 'end') => void
  onMarker: (id: string) => void
}

export function DayLine({ dayStart, messages, times, at, shown, playing, speed, lang, onSeek, onTogglePlay, onSpeed, onStep, onEdge, onMarker }: DayLineProps) {
  const { t } = useTranslation('warroom')
  const rtl = useDir() === 'rtl'
  const trackRef = useRef<HTMLDivElement>(null)
  const headRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const buckets = useMemo(() => densityBuckets(times, dayStart), [times, dayStart])
  const max = Math.max(1, ...buckets)
  const moments = useMemo(() => messages.flatMap((m, i) => (isMoment(m) ? [{ m, at: times[i]! }] : [])), [messages, times])
  const total = messages.length
  const headPct = linePct(at, dayStart)
  const shownText = formatFixed(shown, 0, lang)
  const totalText = formatFixed(total, 0, lang)

  const seekFrom = (e: PointerEvent) => {
    const el = trackRef.current
    if (el) onSeek(atFromPointer(e.clientX, el.getBoundingClientRect(), rtl, dayStart))
  }
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    dragging.current = true
    e.currentTarget.setPointerCapture?.(e.pointerId)
    e.preventDefault()
    seekFrom(e)
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging.current) seekFrom(e)
  }
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return
    dragging.current = false
    e.currentTarget.releasePointerCapture?.(e.pointerId)
    headRef.current?.focus({ preventScroll: true })
  }

  const onHeadKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      onEdge(e.key === 'Home' ? 'start' : 'end')
      return
    }
    // Time runs with the reading direction: in Arabic the right arrow goes back in time.
    const forward = e.key === 'ArrowDown' || (e.key === 'ArrowRight' && !rtl) || (e.key === 'ArrowLeft' && rtl)
    const back = e.key === 'ArrowUp' || (e.key === 'ArrowLeft' && !rtl) || (e.key === 'ArrowRight' && rtl)
    if (!forward && !back) return
    e.preventDefault()
    onStep(forward ? 1 : -1)
  }

  return (
    <div className="rp-line" role="group" aria-label={t('rp.timeline', { city: ownerCity(lang) })} data-testid="rp-line">
      <div className="rp-controls">
        <button
          type="button"
          className="rp-play"
          aria-pressed={playing}
          aria-label={t(playing ? 'rp.pause' : 'rp.play')}
          title={t(playing ? 'rp.pause' : 'rp.play')}
          disabled={total === 0}
          onClick={onTogglePlay}
          data-testid="rp-play"
        >
          {playing ? <Pause aria-hidden="true" strokeWidth={1.5} /> : <Play aria-hidden="true" strokeWidth={1.5} />}
        </button>
        <div className="rp-clock">
          <p className="rp-time num" data-testid="rp-time">
            {formatClock(at, lang)}
          </p>
          <p className="rp-count" data-testid="rp-count">
            {t('rp.shown', { shown: shownText, total: totalText })}
          </p>
        </div>
        <div className="rp-speed">
          <SegmentedControl<string>
            aria-label={t('rp.speed')}
            options={SPEEDS.map((v) => ({ value: String(v), label: <span className="num">{`${formatFixed(v, 0, lang)}×`}</span> }))}
            value={String(speed)}
            onValueChange={(v) => onSpeed(Number(v) as Speed)}
          />
        </div>
      </div>
      <div className="rp-track-wrap">
        <div className="rp-marks">
          {moments.map(({ m, at: when }) => {
            const Icon = MOMENT_ICON[m.kind]
            const tip = t('rp.mkTip', { time: formatTime(when, lang), what: t(`rp.mk.${m.kind}`) })
            return (
              <button
                key={m.id}
                type="button"
                className={`rp-mk is-${m.kind}${when <= at ? ' is-past' : ''}`}
                style={{ '--x': linePct(when, dayStart).toFixed(3) } as CSSProperties}
                aria-label={tip}
                title={tip}
                onClick={() => onMarker(m.id)}
                data-testid="rp-marker"
                data-kind={m.kind}
              >
                <Icon aria-hidden="true" strokeWidth={2} />
              </button>
            )
          })}
        </div>
        <div className="rp-track" ref={trackRef} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} data-testid="rp-track">
          <div className="rp-strip" aria-hidden="true">
            {buckets.map((n, i) =>
              n > 0 ? (
                <i
                  key={i}
                  className={`rp-d${dayStart + ((i + 1) * DAY_MS) / BUCKETS <= at ? ' is-past' : ''}`}
                  style={{ '--x': ((i / BUCKETS) * 100).toFixed(3), '--h': Math.max(0.18, n / max).toFixed(2) } as CSSProperties}
                />
              ) : null,
            )}
          </div>
          <i className="rp-past" aria-hidden="true" style={{ '--x': headPct.toFixed(3) } as CSSProperties} />
          <div
            ref={headRef}
            className="rp-head"
            role="slider"
            tabIndex={0}
            aria-label={t('rp.timeline', { city: ownerCity(lang) })}
            aria-valuemin={0}
            aria-valuemax={1439}
            aria-valuenow={minuteOfDay(at, dayStart)}
            aria-valuetext={t('rp.timeAria', { city: ownerCity(lang), time: formatTime(at, lang), shown: shownText, total: totalText })}
            aria-describedby="rp-keys"
            aria-orientation="horizontal"
            style={{ '--x': headPct.toFixed(3) } as CSSProperties}
            onKeyDown={onHeadKey}
            data-testid="rp-head"
          >
            <span className="rp-knob" />
          </div>
        </div>
        <div className="rp-hours" aria-hidden="true">
          {HOURS.map((h) => (
            <span key={h} className="rp-hr" style={{ '--x': ((h / 24) * 100).toFixed(2) } as CSSProperties}>
              <bdi dir="ltr" className="num">{`${String(h).padStart(2, '0')}:00`}</bdi>
            </span>
          ))}
        </div>
      </div>
      <p className="sr-only" id="rp-keys">
        {t('rp.keys')}
      </p>
    </div>
  )
}

/** The day line while the day loads (design: one 64px skeleton bar). */
export function DayLineSkeleton() {
  return (
    <div className="rp-line is-skel" aria-hidden="true">
      <Skeleton style={{ width: '100%', height: 64, borderRadius: 12 }} />
    </div>
  )
}
