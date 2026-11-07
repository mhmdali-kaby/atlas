import { Calendar, ChevronLeft, ChevronRight, Clock, CloudOff, History, Lock, MessagesSquare, PanelRight, RadioTower, RefreshCw } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useEffect, useEffectEvent, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { toolbarIconButton } from '../../app/shell/toolbarClasses'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { formatFixed, type Lang } from '../../lib/format'
import { Button, Skeleton } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { DayLine, DayLineSkeleton } from './DayLine'
import { ReplayCalendar } from './ReplayCalendar'
import { ReplayFeed, type ReplayJump } from './ReplayFeed'
import { ReplayInspector } from './ReplayInspector'
import { dayStartMs, isReplayable, lastReplayDay, replayDayName, replayPath, shiftDay, useReplay } from './replay'
import { clampAt, DAY_MS, messageTimes, shownCount, stepIndex } from './replayTime'
import { useInspector } from './useInspector'
import { usePlayback } from './usePlayback'
import { fillSlots, slot } from './slots'
import { formatTime } from './time'
import './warroom.css'
import './replay.css'

/*
 * War Room Replay (ATL-206; brief §12 "Replay", design v18 Replay): /warroom/replay/<YYYY-MM-DD>
 * shows one finished Baghdad day read-only — the replay bar with the day and a way back to live,
 * the day's messages in order with the War Room's own cards, and the day's moments in the
 * inspector. No composer, no actions, no live messages. The day line (ATL-207) sits between the
 * bar and the body: the feed shows only what had been said by the playhead's time.
 */

/** /warroom/replay without a valid finished day opens yesterday. */
export function ReplayRedirect() {
  const now = useNow(60_000)
  return <Navigate to={replayPath(lastReplayDay(now))} replace />
}

export function ReplayPage() {
  const { date } = useParams<{ date: string }>()
  const now = useNow(60_000)
  if (!isReplayable(date, now)) return <Navigate to={replayPath(lastReplayDay(now))} replace />
  return <ReplayDay key={date} day={date} now={now} />
}

function ReplayDay({ day, now }: { day: string; now: number }) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const env = useTradingEnv()
  const query = useReplay(env, day)
  const messages = useMemo(() => query.data?.data ?? [], [query.data])
  const times = useMemo(() => messageTimes(messages), [messages])
  const dayStart = dayStartMs(day)
  const play = usePlayback(dayStart)
  const { at } = play
  const shown = shownCount(times, at)
  // A prefix of the day, recomputed only when the playhead crosses a message.
  const visible = useMemo(() => messages.slice(0, shown), [messages, shown])
  const [jump, setJump] = useState<ReplayJump | null>(null)
  const [follow, setFollow] = useState(0)
  const inspector = useInspector('atlas.rp.inspector')
  const dayName = replayDayName(day, lang)
  const status = query.isPending ? 'loading' : query.isError ? 'error' : messages.length === 0 ? 'empty' : 'ok'

  // Screen readers hear how far the replay has got when the owner moves it, not on every playback tick.
  const [announce, setAnnounce] = useState('')
  const announceAt = (next: number) => {
    const n = shownCount(times, clampAt(next, dayStart))
    if (n !== shown) setAnnounce(t('rp.shown', { shown: formatFixed(n, 0, lang), total: formatFixed(messages.length, 0, lang) }))
  }

  /** Drag, track click, Home/End: the feed follows to the newest message shown. */
  const seek = (next: number) => {
    play.pause()
    announceAt(next)
    play.setAt(next)
    setJump(null)
    setFollow((f) => f + 1)
  }
  /** A marker, a moment, or an arrow-key step: the playhead goes to that message and the feed jumps to it. */
  const goTo = (id: string, focus = true) => {
    const i = messages.findIndex((m) => m.id === id)
    if (i < 0) return
    play.pause()
    announceAt(times[i]!)
    play.setAt(times[i]!)
    setJump((j) => ({ id, seq: (j?.seq ?? 0) + 1, focus }))
  }
  const step = (dir: 1 | -1) => {
    const i = stepIndex(times, at, dir)
    if (i !== null) goTo(messages[i]!.id, false)
  }
  const togglePlay = () => {
    if (!play.playing) setFollow((f) => f + 1)
    play.toggle()
  }

  // Space plays or pauses anywhere on the page, except where it types or presses a control.
  const onSpace = useEffectEvent(togglePlay)
  const canPlay = status === 'ok'
  useEffect(() => {
    if (!canPlay) return
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== ' ' || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return
      const el = e.target instanceof Element ? e.target : null
      if (el?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"], a, button, [role="radio"], [role="menuitem"]')) return
      e.preventDefault()
      onSpace()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [canPlay])

  // The shell's toolbar has a slot for page actions; the inspector toggle goes there, as in the live War Room.
  const [toolbarSlot, setToolbarSlot] = useState<Element | null>(null)
  const findSlot = (el: HTMLDivElement | null) => {
    if (el) setToolbarSlot(document.querySelector('[data-slot="toolbar-actions"]'))
  }

  let main
  if (status === 'loading') {
    main = <ReplaySkeleton />
  } else if (status === 'error') {
    main = (
      <div className="wr-column">
        <div className="state-block" role="alert" data-testid="rp-error">
          <div className="state-icon is-error">
            <CloudOff aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h2 className="text-title-3">{t('rp.err.title')}</h2>
          <p className="state-body">
            {t('rp.err.bodyBefore')}{' '}
            <code className="cmd" dir="ltr">
              ./atlas up
            </code>
            {t('rp.err.bodyAfter')}
          </p>
          <Button variant="secondary" onClick={() => void query.refetch()} icon={<RefreshCw aria-hidden="true" strokeWidth={1.5} />}>
            {t('tryAgain')}
          </Button>
        </div>
      </div>
    )
  } else if (status === 'empty') {
    main = (
      <div className="wr-column">
        <div className="state-block" data-testid="rp-empty">
          <div className="state-icon">
            <MessagesSquare aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h2 className="text-title-3">{t('rp.empty.title', { date: dayName })}</h2>
          <p className="state-body">{t('rp.empty.body')}</p>
          <DayPicker day={day} now={now} lang={lang} />
        </div>
      </div>
    )
  } else {
    main = (
      <>
        {shown === 0 ? (
          <div className="rp-before-wrap" data-testid="rp-before">
            <p className="rp-before">
              <Clock aria-hidden="true" strokeWidth={1.5} />
              <span>{fillSlots(t('rp.before', { time: slot('time') }), { time: <bdi dir="ltr" className="num">{formatTime(times[0]!, lang)}</bdi> })}</span>
            </p>
          </div>
        ) : (
          <ReplayFeed messages={visible} label={t('rp.feed', { date: dayName })} jump={jump} follow={follow} />
        )}
        <p className="rp-foot" data-testid="rp-foot">
          <Lock aria-hidden="true" strokeWidth={1.5} />
          <span>{t('rp.readonly')}</span>
          <Button asChild variant="plain" size="sm">
            <Link to="/warroom">{t('rp.live')}</Link>
          </Button>
        </p>
      </>
    )
  }

  return (
    <div ref={findSlot} className="rp" data-testid="rp-page" aria-busy={status === 'loading' || undefined}>
      <ReplayBar day={day} now={now} lang={lang} dayName={dayName} />
      {status === 'loading' ? (
        <DayLineSkeleton />
      ) : status === 'ok' || status === 'empty' ? (
        <DayLine
          dayStart={dayStart}
          messages={messages}
          times={times}
          at={at}
          shown={shown}
          playing={play.playing}
          speed={play.speed}
          lang={lang}
          onSeek={seek}
          onTogglePlay={togglePlay}
          onSpeed={play.setSpeed}
          onStep={step}
          onEdge={(edge) => seek(edge === 'start' ? dayStart : dayStart + DAY_MS - 1)}
          onMarker={(id) => goTo(id)}
        />
      ) : null}
      <div className="wr rp-body" data-inspector={inspector.shown ? 'open' : 'closed'} data-overlay={inspector.overlay || undefined}>
        {/* Not a second landmark: the bar names the day, the feed's log names its messages. */}
        <div className="rp-main">{main}</div>
        {inspector.shown && (
          <ReplayInspector
            day={day}
            lang={lang}
            status={status}
            messages={messages}
            times={times}
            at={at}
            onPick={(id) => {
              goTo(id)
              if (inspector.overlay) inspector.closeOverlay()
            }}
          />
        )}
      </div>
      <div className="sr-only" aria-live="polite" data-testid="rp-live">
        {announce}
      </div>
      {toolbarSlot &&
        createPortal(
          <button
            type="button"
            className={toolbarIconButton}
            aria-label={t('rp.inspToggle')}
            title={t('rp.inspToggle')}
            data-menu-label={t('rp.inspToggle')}
            aria-pressed={inspector.shown}
            aria-controls="rp-inspector"
            onClick={inspector.toggle}
            data-testid="rp-inspector-toggle"
          >
            <PanelRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
          </button>,
          toolbarSlot,
        )}
    </div>
  )
}
function ReplayBar({ day, now, lang, dayName }: { day: string; now: number; lang: Lang; dayName: string }) {
  const { t } = useTranslation('warroom')
  const navigate = useNavigate()
  const next = shiftDay(day, 1)
  const canNext = isReplayable(next, now)
  return (
    <div className="rp-bar" role="region" aria-label={t('rp.banner', { date: dayName })} data-testid="rp-banner">
      <span className="rp-bar-ic" aria-hidden="true">
        <History strokeWidth={1.75} />
      </span>
      <div className="rp-bar-text">
        <p className="rp-bar-title">{t('rp.banner', { date: dayName })}</p>
        <p className="rp-bar-sub">{t('rp.bannerSub')}</p>
      </div>
      <div className="rp-bar-nav">
        <Button
          size="sm"
          className="rp-icon-btn"
          aria-label={t('rp.prevDay')}
          title={t('rp.prevDay')}
          onClick={() => navigate(replayPath(shiftDay(day, -1)))}
          data-testid="rp-prev-day"
        >
          <ChevronLeft aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </Button>
        <DayPicker day={day} now={now} lang={lang} trigger="bar" />
        <Button
          size="sm"
          className="rp-icon-btn"
          aria-label={t('rp.nextDay')}
          title={t('rp.nextDay')}
          disabled={!canNext}
          onClick={() => navigate(replayPath(next))}
          data-testid="rp-next-day"
        >
          <ChevronRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </Button>
      </div>
      <Button asChild variant="primary" size="sm" icon={<RadioTower aria-hidden="true" strokeWidth={1.5} />}>
        <Link to="/warroom" data-testid="rp-back-live">
          {t('rp.live')}
        </Link>
      </Button>
    </div>
  )
}

/** The "Choose a day" popover: the bar's date button, or the empty state's button. */
function DayPicker({ day, now, lang, trigger = 'empty' }: { day: string; now: number; lang: Lang; trigger?: 'bar' | 'empty' }) {
  const { t } = useTranslation('warroom')
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {trigger === 'bar' ? (
          <Button size="sm" className="rp-date" icon={<Calendar aria-hidden="true" strokeWidth={1.5} />} aria-label={`${replayDayName(day, lang, true)}, ${t('rp.pick')}`} data-testid="rp-date">
            {replayDayName(day, lang, true)}
          </Button>
        ) : (
          <Button variant="secondary" icon={<Calendar aria-hidden="true" strokeWidth={1.5} />} data-testid="rp-pick">
            {t('rp.pick')}
          </Button>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="rp-cal-pop" sideOffset={6} align="center" aria-label={t('rp.pick')} collisionPadding={12}>
          <ReplayCalendar
            selected={day}
            now={now}
            lang={lang}
            onPick={(d) => {
              setOpen(false)
              navigate(replayPath(d))
            }}
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function ReplaySkeleton() {
  const { t } = useTranslation('warroom')
  return (
    <div className="wr-column" role="status" aria-label={t('loading')} data-testid="rp-loading">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="msg-skel">
          <Skeleton className="skel-round" />
          <span className="lines">
            <Skeleton style={{ width: 180, height: 13 }} />
            <Skeleton style={{ width: '80%', height: 12 }} />
            <Skeleton style={{ width: '60%', height: 12 }} />
          </span>
        </div>
      ))}
    </div>
  )
}
