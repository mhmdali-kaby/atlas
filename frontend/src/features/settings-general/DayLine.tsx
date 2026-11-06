import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { TEAM_TZ, useOwnerZone } from '../../lib/ownerZone'
import { diffFromTeam, zoneCity } from '../../lib/zones'
import { clock, DAY_MIN, dayItems, frameOf, nowOnFrame, placeLabels, segments, type ScheduleValues } from './dayModel'
import './dayline.css'

/*
 * DayLine (design v21 js/dayline.js, owner-setup §10): when the team works, on one 24-hour line.
 * Jobs are rings in their agent's colour, the analysis window is a comb with one tooth per cycle,
 * the US cash session and quiet hours are bands behind the line, Now is the accent dot.
 * `frame="owner"` draws the owner's 00:00–24:00 (General); `frame="team"` draws Baghdad's
 * (Schedule). When the clocks differ, a second tick row gives the other clock. Mirrors in Arabic.
 * The drawing is aria-hidden; a visually hidden list reads every job and window with its time.
 */

const NS = 'settings-general'
const LRI = '⁦'
const PDI = '⁩'
const fig = (s: string) => `${LRI}${s}${PDI}`
const pct = (m: number) => `${(m / DAY_MIN) * 100}%`

interface Label {
  key: string
  f: number
  run?: boolean
  now?: boolean
  lit?: boolean
  rings?: string[]
  time?: string
  text: string
  short?: string
}

export interface DayLineProps {
  values: ScheduleValues
  frame: 'owner' | 'team'
  now: number
  lang: Lang
  highlight?: string
}

export function DayLine({ values, frame, now, lang, highlight }: DayLineProps) {
  const { t } = useTranslation(NS)
  const owner = useOwnerZone()
  const diff = diffFromTeam(owner, now)
  const two = diff !== 0
  const { toFrame, toOther } = frameOf(frame, diff)
  const frameZone = frame === 'team' ? TEAM_TZ : owner
  const otherZone = frame === 'team' ? owner : TEAM_TZ
  const city = zoneCity(frameZone, lang)
  const otherCity = zoneCity(otherZone, lang)
  const it = dayItems(values, now)

  const what = (key: string, paired = false) => (paired ? t('dl.briefRegime') : t(`dl.${key}`))
  const tip = (label: string, teamMin: number) => {
    const f = toFrame(teamMin)
    return two
      ? t('dl.tipBoth', { what: label, time: clock(f), city, other: clock(toOther(f)), otherCity })
      : t('dl.tip', { what: label, time: clock(f), city })
  }

  const points = it.points.map((p) => ({ ...p, f: toFrame(p.m) })).sort((a, b) => a.f - b.f)
  const runWhat = t('dl.analysis', { n: fig(String(values.analysis_interval_minutes)) })
  const runSegs = it.run ? segments(toFrame(it.run.from), toFrame(it.run.to) + 0.0001) : []
  const longestRun = [...runSegs].sort((a, b) => b[1] - b[0] - (a[1] - a[0]))[0]
  const runMid = longestRun ? (longestRun[0] + longestRun[1]) / 2 : null
  const nowF = nowOnFrame(now, frame, diff)

  const labels: Label[] = [
    ...points.map((p) => ({
      key: p.key,
      f: p.f,
      lit: highlight === p.key,
      rings: p.rings,
      time: clock(p.f),
      text: p.key === 'brief' ? t('dl.brief') : what(p.key),
    })),
    ...(runMid !== null ? [{ key: 'run', f: runMid, run: true, text: runWhat, short: t('dl.analysisShort') }] : []),
    { key: 'now', f: nowF, now: true, time: clock(nowF), text: t('dl.now') },
  ]

  const plotRef = useRef<HTMLDivElement>(null)
  const [layout, setLayout] = useState<{ lanes: number; places: Record<string, { lane: number; anchor: string }> }>({ lanes: 1, places: {} })
  const sig = labels.map((l) => `${l.key}:${l.f}:${l.text}`).join('|')
  useLayoutEffect(() => {
    const plot = plotRef.current
    if (!plot) return
    const measure = () => {
      const width = plot.clientWidth
      if (!width) return
      const els = Array.from(plot.querySelectorAll<HTMLElement>('.dl-lab'))
      const shown = els.filter((el) => el.offsetWidth > 0)
      const boxes = shown.map((el) => ({
        f: Number(el.dataset.f),
        width: el.offsetWidth,
        run: el.dataset.run === 'true',
        now: el.dataset.now === 'true',
      }))
      const res = placeLabels(boxes, width)
      const places: Record<string, { lane: number; anchor: string }> = {}
      shown.forEach((el, i) => {
        places[el.dataset.key ?? ''] = res.places[i] ?? { lane: 0, anchor: 's' }
      })
      setLayout((old) =>
        old.lanes === res.lanes && JSON.stringify(old.places) === JSON.stringify(places) ? old : { lanes: res.lanes, places },
      )
    }
    measure()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    ro?.observe(plot)
    return () => ro?.disconnect()
  }, [sig])

  const ticks = (alt: boolean) =>
    Array.from({ length: 9 }, (_, i) => {
      const f = i * 180
      const m = alt ? toOther(f) : f
      const tier = i % 4 === 0 ? '' : i % 2 === 0 ? ' is-6h' : ' is-3h'
      const edge = i === 0 ? ' is-first' : i === 8 ? ' is-last' : ''
      return (
        <span key={i} className={`dl-tick${tier}${edge}`} style={{ '--x': pct(f) } as CSSProperties}>
          <span className="fig">{fig(i === 8 && !alt ? '24:00' : clock(m))}</span>
          {two && i === 0 && <span className="dl-tick-zone">{alt ? otherCity : city}</span>}
        </span>
      )
    })

  const ringStyle = (rings: string[] | undefined, x: number): CSSProperties =>
    ({
      '--x': pct(x),
      '--ring': `var(--ring-${rings?.[0] ?? 'orchestrator'})`,
      ...(rings?.[1] ? { '--ring2': `var(--ring-${rings[1]})` } : {}),
    }) as CSSProperties

  const srItems = [
    ...points.map((p) => t('dl.srItem', { what: what(p.key, p.paired), time: clock(p.f) })),
    ...(it.run ? [t('dl.srRun', { what: runWhat, from: clock(toFrame(it.run.from)), to: clock(toFrame(it.run.to)) })] : []),
    ...it.bands.map((b) => t('dl.srRun', { what: t(`dl.${b.key}`), from: clock(toFrame(b.from)), to: clock(toFrame(b.to)) })),
  ]

  return (
    <figure
      className={`dl${two ? ' has-two' : ''}`}
      data-testid="dayline"
      aria-label={t('dl.sr', { city })}
      style={{ '--lanes': layout.lanes } as CSSProperties}
    >
      <div className="dl-plot" ref={plotRef} aria-hidden="true">
        <div className="dl-labs">
          {labels.map((l) => {
            const place = layout.places[l.key]
            const cls = ['dl-lab', l.run && 'is-run', l.now && 'is-now', l.lit && 'is-lit'].filter(Boolean).join(' ')
            return (
              <span
                key={l.key}
                className={cls}
                data-key={l.key}
                data-f={l.f}
                data-run={l.run ? 'true' : undefined}
                data-now={l.now ? 'true' : undefined}
                data-pair={l.rings?.[1] ? '' : undefined}
                data-aw={place?.anchor ?? 's'}
                style={{ ...ringStyle(l.rings, l.f), '--ln': place?.lane ?? 0 } as CSSProperties}
              >
                {l.run ? (
                  <>
                    <span className="dl-long">{l.text}</span>
                    <span className="dl-short">{l.short}</span>
                  </>
                ) : (
                  <>
                    <b className="fig">{fig(l.time ?? '')}</b>
                    <span>{l.text}</span>
                  </>
                )}
              </span>
            )
          })}
        </div>
        <div className="dl-field">
          {it.bands.map((b) => {
            const sg = segments(toFrame(b.from), toFrame(b.to))
            const longest = sg.reduce((k, x, i) => (x[1] - x[0] > (sg[k]?.[1] ?? 0) - (sg[k]?.[0] ?? 0) ? i : k), 0)
            const title = t('dl.bandTip', { what: t(`dl.${b.key}`), from: clock(toFrame(b.from)), to: clock(toFrame(b.to)), city })
            return sg.map(([a, c], i) => (
              <span
                key={`${b.key}-${i}`}
                className={`dl-band is-${b.key}`}
                style={{ '--x': pct(a), '--w': pct(c - a) } as CSSProperties}
                title={title}
              >
                {i === longest && <span className="dl-band-cap">{t(`dl.${b.key}`)}</span>}
              </span>
            ))
          })}
          <span className="dl-line" />
          {it.run && (
            <>
              {runSegs.map(([a, c], i) => (
                <span
                  key={`run-${i}`}
                  className={`dl-run${highlight === 'analysis' ? ' is-lit' : ''}`}
                  style={{ '--x': pct(a), '--w': pct(c - a) } as CSSProperties}
                  title={t('dl.runTip', {
                    what: runWhat,
                    from: clock(toFrame(it.run!.from)),
                    to: clock(toFrame(it.run!.to)),
                    city,
                    count: it.run!.slots.length === 1 ? t('dl.cycles1') : t('dl.cycles', { n: fig(String(it.run!.slots.length)) }),
                  })}
                />
              ))}
              {it.run.slots.map((m) => (
                <i key={m} className="dl-tooth" style={{ '--x': pct(toFrame(m)) } as CSSProperties} />
              ))}
            </>
          )}
          {points.map((p) => (
            <span
              key={p.key}
              className={`dl-pt${highlight === p.key ? ' is-lit' : ''}`}
              data-pair={p.paired ? '' : undefined}
              style={ringStyle(p.rings, p.f)}
              title={tip(what(p.key, p.paired), p.m)}
            />
          ))}
          <span className="dl-now" style={{ '--x': pct(nowF) } as CSSProperties} title={t('dl.tip', { what: t('dl.now'), time: clock(nowF), city })} />
        </div>
      </div>
      <div className="dl-axis" aria-hidden="true">
        {ticks(false)}
      </div>
      {two && (
        <div className="dl-axis is-alt" aria-hidden="true">
          {ticks(true)}
        </div>
      )}
      <ul className="sr-only">
        {srItems.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </figure>
  )
}
