import { ChevronLeft, ChevronRight, Info } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { isReplayable, replayDayName } from './replay'
import { baghdadDay } from './time'

/*
 * The Replay date picker (design v18 Replay, "Choose a day"): one month of Baghdad days, weeks
 * starting Monday. Today and every later day are disabled (aria-disabled, skipped by the arrows):
 * today is still live, and Replay shows finished days only. Arrow keys move by a day or a week
 * and follow the reading direction; Enter or Space opens the day.
 */

const month = (day: string) => day.slice(0, 7)

function addMonths(ym: string, n: number): string {
  const [y, m] = ym.split('-').map(Number) as [number, number]
  const d = new Date(Date.UTC(y, m - 1 + n, 1))
  return d.toISOString().slice(0, 7)
}

const fmtCache = new Map<string, Intl.DateTimeFormat>()
function fmt(lang: Lang, o: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${lang}|${JSON.stringify(o)}`
  let f = fmtCache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : DATE_LOCALE.ar, o)
    fmtCache.set(key, f)
  }
  return f
}

export interface ReplayCalendarProps {
  selected: string
  now: number
  lang: Lang
  onPick: (day: string) => void
}

export function ReplayCalendar({ selected, now, lang, onPick }: ReplayCalendarProps) {
  const { t } = useTranslation('warroom')
  const today = baghdadDay(now)
  const [shown, setShown] = useState(() => month(selected))
  const [focusDay, setFocusDay] = useState(selected)
  const grid = useRef<HTMLDivElement>(null)
  const moved = useRef(false)

  const days = useMemo(() => {
    const [y, m] = shown.split('-').map(Number) as [number, number]
    const count = new Date(Date.UTC(y, m, 0)).getUTCDate()
    const lead = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7
    const list: Array<string | null> = Array.from({ length: lead }, () => null)
    for (let d = 1; d <= count; d++) list.push(`${shown}-${String(d).padStart(2, '0')}`)
    return list
  }, [shown])
  const weekdays = useMemo(
    // 2 Nov 2026 is a Monday.
    () => Array.from({ length: 7 }, (_, i) => fmt(lang, { weekday: 'narrow', timeZone: 'UTC' }).format(new Date(Date.UTC(2026, 10, 2 + i)))),
    [lang],
  )
  const title = fmt(lang, { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${shown}-01T00:00:00Z`))
  const nextOff = shown >= month(today)

  // The roving tab stop: the selected day, else the newest open day of the month.
  const open = days.filter((d): d is string => d !== null && isReplayable(d, now))
  const tabDay = open.includes(focusDay) ? focusDay : (open[open.length - 1] ?? null)

  useEffect(() => {
    if (!moved.current) return
    moved.current = false
    grid.current?.querySelector<HTMLElement>(`[data-day="${focusDay}"]`)?.focus()
  }, [focusDay, shown])

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, day: string) {
    const step: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }
    let n = step[e.key]
    if (n === undefined) return
    e.preventDefault()
    if (document.documentElement.dir === 'rtl' && Math.abs(n) === 1) n = -n
    // The only disabled days are today and later, so a step onto one simply stays put.
    const next = new Date(Date.parse(`${day}T12:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10)
    if (!isReplayable(next, now)) return
    moved.current = true
    setFocusDay(next)
    if (month(next) !== shown) setShown(month(next))
  }

  return (
    <div className="rp-cal" data-testid="rp-calendar">
      <div className="rp-cal-head">
        <button type="button" className="rp-cal-nav" aria-label={t('rp.prevMonth')} title={t('rp.prevMonth')} onClick={() => setShown(addMonths(shown, -1))}>
          <ChevronLeft aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </button>
        <p className="rp-cal-title" aria-live="polite">
          {title}
        </p>
        <button
          type="button"
          className="rp-cal-nav"
          aria-label={t('rp.nextMonth')}
          title={t('rp.nextMonth')}
          disabled={nextOff}
          onClick={() => setShown(addMonths(shown, 1))}
          data-testid="rp-cal-next"
        >
          <ChevronRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </button>
      </div>
      <div className="rp-cal-grid" ref={grid} role="group" aria-label={title}>
        {weekdays.map((w, i) => (
          <span key={`wd${i}`} className="rp-cal-wd" aria-hidden="true">
            {w}
          </span>
        ))}
        {days.map((day, i) => {
          if (!day) return <span key={`lead${i}`} />
          const off = !isReplayable(day, now)
          const sel = day === selected
          return (
            <button
              key={day}
              type="button"
              className={['rp-cal-d', sel && 'is-sel', day === today && 'is-today'].filter(Boolean).join(' ')}
              data-day={day}
              aria-disabled={off || undefined}
              aria-pressed={sel}
              aria-label={replayDayName(day, lang)}
              title={day === today ? t('rp.todayOff') : undefined}
              tabIndex={!off && day === tabDay ? 0 : -1}
              onClick={() => {
                if (!off) onPick(day)
              }}
              onFocus={() => {
                if (!off) setFocusDay(day)
              }}
              onKeyDown={(e) => onKeyDown(e, day)}
            >
              <bdi dir="ltr" className="num">
                {Number(day.slice(8))}
              </bdi>
            </button>
          )
        })}
      </div>
      <p className="rp-cal-note">
        <Info aria-hidden="true" strokeWidth={1.5} />
        <span>{t('rp.todayOff')}</span>
      </p>
    </div>
  )
}
