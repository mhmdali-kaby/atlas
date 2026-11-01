import { useLayoutEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../../lib/format'
import { fillSlots, slot } from '../slots'
import './ladder.css'

/*
 * The trade ladder (design v11 Phase 2 §4): one rail measured in R, the trade's initial risk. The
 * stop sits at −1R on the left, the entry zone at 0, the targets to the right with their split
 * and R, for longs and shorts alike. A number line, so it stays left-to-right in Arabic (like a
 * chart axis); its words are translated and its figures are LTR islands. Shared by the
 * TechnicalSetup card (ATL-139), the proposal card (ATL-164) and the PM's position updates.
 *
 * Prices are decimal strings and are printed from the strings at tick precision; Number() is used
 * only to place marks on the rail.
 */

export interface LadderTarget {
  price: string
  /** The R the card states for this target; computed from the geometry when absent. */
  r?: string | null
  /** Share of the position closed here, in percent. */
  pct?: string | null
}

export interface LadderProps {
  side: 'long' | 'short'
  entry: [string, string]
  stop: string
  /** The stop the trade opened with, which defines 1R (defaults to `stop`). */
  riskStop?: string
  targets: LadderTarget[]
  /** Price now. */
  mark?: string | null
  decimals: number
  compact?: boolean
  lang: Lang
}

/** "+1.2R", "−1.0R", "0.0R": the sign comes from the rounded value, so it never reads "−0.0R". */
export function rText(v: string | number, digits = 1): string {
  const nf = new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: 'exceptZero' })
  const value = typeof v === 'number' ? v : (v.trim() as Intl.StringNumericLiteral)
  return `${nf.format(value).replace(/-/g, '−')}R`
}

/** "50%", "33.3%". */
function pctText(v: string, lang: Lang): string {
  return `${formatFixed(v, Number(v) % 1 ? 1 : 0, lang)}%`
}

function Isl({ children, className = 'num' }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className}>
      {children}
    </bdi>
  )
}

/** Where a label sits so it never leaves the figure. */
const edge = (x: number) => (x < 11 ? 'is-start' : x > 89 ? 'is-end' : '')
const pos = (x: number) => ({ '--x': x.toFixed(2) }) as CSSProperties
const span = (a: number, b: number) => ({ '--a': a.toFixed(2), '--b': b.toFixed(2) }) as CSSProperties

/** Labels on one row keep at least this many px between them (design §4 "Labels never touch"). */
const GAP = 10

/**
 * After layout, a label closer than GAP to its neighbour on the same row is pushed right, then back
 * from the figure's end, and never leaves the figure. The offset goes in --nudge.
 */
function fit(fig: HTMLElement) {
  const fr = fig.getBoundingClientRect()
  if (!fr.width) return
  for (const row of ['is-above', 'is-below']) {
    const els = [...fig.querySelectorAll<HTMLElement>(`.ladder-label.${row}`)]
    els.forEach((el) => el.style.removeProperty('--nudge'))
    if (els.length < 2) continue
    const L = els
      .map((el) => {
        const r = el.getBoundingClientRect()
        return { el, a: r.left, w: r.width }
      })
      .sort((p, q) => p.a - q.a)
    const lo = Math.min(fr.left, L[0]!.a)
    const hi = Math.max(fr.right, L[L.length - 1]!.a + L[L.length - 1]!.w)
    const at = L.map((l) => l.a)
    for (let i = 1; i < L.length; i++) at[i] = Math.max(at[i]!, at[i - 1]! + L[i - 1]!.w + GAP)
    const n = L.length - 1
    if (at[n]! + L[n]!.w > hi) at[n] = hi - L[n]!.w
    for (let i = n - 1; i >= 0; i--) at[i] = Math.min(at[i]!, at[i + 1]! - L[i]!.w - GAP)
    if (at[0]! < lo) at[0] = lo
    L.forEach((l, i) => {
      const d = Math.round(at[i]! - l.a)
      if (d) l.el.style.setProperty('--nudge', `${d}px`)
    })
  }
}

export function Ladder({ side, entry, stop, riskStop, targets, mark, decimals, compact = false, lang }: LadderProps) {
  const { t } = useTranslation('warroom')
  const ref = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    fit(el)
    let frame = 0
    const again = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => fit(el))
    }
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(again)
    ro?.observe(el)
    void document.fonts?.ready.then(again)
    return () => {
      cancelAnimationFrame(frame)
      ro?.disconnect()
    }
  })

  const P = (p: string) => formatFixed(p, decimals, lang)
  const lo0 = Number(entry[0])
  const hi0 = Number(entry[1])
  const refPx = (lo0 + hi0) / 2
  const unit = Math.abs(refPx - Number(riskStop ?? stop)) || 1
  const R = (p: string) => ((side === 'short' ? refPx - Number(p) : Number(p) - refPx) / unit)
  const rs = R(stop)
  const tR = targets.map((x) => R(x.price))
  const pts = [rs, -1, ...tR, ...(mark ? [R(mark)] : [])]
  const lo = Math.min(...pts) - 0.3
  const hi = Math.max(...pts) + 0.3
  const X = (r: number) => ((r - lo) / (hi - lo)) * 100
  const xs = X(rs)
  const x0 = X(0)
  const zr = [R(entry[0]), R(entry[1])]
  const za = X(Math.min(...zr))
  const zb = X(Math.max(...zr))
  const xLast = X(Math.max(...tR))
  const xe = (za + zb) / 2
  const entryText = entry[0] === entry[1] ? P(entry[0]) : `${P(entry[0])}–${P(entry[1])}`

  const stopPct = ((Math.abs(Number(stop) - refPx) / refPx) * -100).toFixed(2)
  const stopNote: ReactNode =
    rs < -0.05 ? <Isl>{`${formatFixed(stopPct, 2, lang)}%`}</Isl> : Math.abs(rs) <= 0.05 ? t('ld.breakeven') : fillSlots(t('ld.locked', { r: slot('r') }), { r: <Isl>{rText(rs)}</Isl> })

  const targetR = (x: LadderTarget, i: number) => (x.r ? rText(x.r) : rText(tR[i]!))

  // The price now: above its dot, unless that would sit on the entry label; then below, or inside the entry label.
  let now: ReactNode = null
  let nowInline: ReactNode = null
  if (mark) {
    const rm = R(mark)
    const xm = X(rm)
    const gap = compact ? 22 : 17
    const nearEntry = Math.abs(xm - xe) < gap
    const belowFree = [xs, ...tR.map(X)].every((x) => Math.abs(x - xm) >= gap)
    const where = !nearEntry ? 'is-above' : belowFree ? 'is-below' : ''
    now = <i className="ladder-mark" style={pos(xm)} />
    if (where) {
      now = (
        <>
          {now}
          <span className={`ladder-label ${where} is-now ${edge(xm)}`} style={pos(xm)} data-testid="ld-now">
            <span className="ladder-k">{t('ld.now')}</span>
            <Isl className="num ladder-v">{P(mark)}</Isl>
            {where === 'is-below' && !compact && (
              <span className="ladder-s">
                <Isl>{rText(rm)}</Isl>
              </span>
            )}
          </span>
        </>
      )
    } else {
      nowInline = (
        <span className="ladder-nowline" data-testid="ld-now">
          <span>{t('ld.now')}</span> <Isl className="num ladder-v">{P(mark)}</Isl> <Isl>{rText(rm)}</Isl>
        </span>
      )
    }
  }

  const aria =
    t('ld.aria', {
      side: t(side === 'short' ? 'ld.short' : 'ld.long'),
      stop: P(stop),
      entry: entryText,
      targets: targets.map((x, i) => t('ld.tgt', { n: i + 1, price: P(x.price), r: targetR(x, i) })).join(', '),
    }) + (mark ? t('ld.ariaNow', { now: P(mark), r: rText(R(mark)) }) : '')

  return (
    <figure ref={ref} className={`ladder${compact ? ' is-compact' : ''}`} dir="ltr" role="img" aria-label={aria} data-testid={compact ? 'ladder-compact' : 'ladder'}>
      <div className="ladder-rail" aria-hidden="true">
        {rs < 0 && <i className="ladder-seg is-loss" style={span(xs, x0)} />}
        <i className="ladder-seg is-gain" style={span(x0, xLast)} />
        <i className="ladder-zone" style={span(za, zb)} />
        <i className={`ladder-tick ${rs < -0.05 ? 'is-stop' : 'is-lock'}`} style={pos(xs)} />
        <span className={`ladder-label is-below ${edge(xs)}`} style={pos(xs)} data-testid="ld-stop">
          <span className="ladder-k">{t('ld.stop')}</span>
          <Isl className="num ladder-v">{P(stop)}</Isl>
          {!compact && <span className="ladder-s">{stopNote}</span>}
        </span>
        {targets.map((x, i) => {
          const xx = X(tR[i]!)
          const tick = <i key={`t${i}`} className="ladder-tick is-tp" style={pos(xx)} />
          if (compact && i > 0 && i < targets.length - 1) return tick
          const r = <Isl>{targetR(x, i)}</Isl>
          const note = x.pct ? fillSlots(t('ld.split', { pct: slot('pct'), r: slot('r') }), { pct: <Isl>{pctText(x.pct, lang)}</Isl>, r }) : fillSlots(t('ld.at', { r: slot('r') }), { r })
          return [
            tick,
            <span key={`l${i}`} className={`ladder-label is-below ${edge(xx)}`} style={pos(xx)} data-testid="ld-target">
              <span className="ladder-k">{t('ld.tp', { n: i + 1 })}</span>
              <Isl className="num ladder-v">{P(x.price)}</Isl>
              {!compact && <span className="ladder-s">{note}</span>}
            </span>,
          ]
        })}
        {now}
        <span className={`ladder-label is-above is-entry ${edge(xe)}`} style={pos(xe)} data-testid="ld-entry">
          <span className="ladder-k">{t('ld.entry')}</span>
          <Isl className="num ladder-v">{entryText}</Isl>
          {nowInline}
        </span>
      </div>
    </figure>
  )
}
