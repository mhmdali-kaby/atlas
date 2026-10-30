import { Zap } from 'lucide-react'
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { fillSlots, slot } from './slots'
import { formatTime } from './time'
import { baseOf, dayStart, usSessions, type Pick, type Trigger } from './watchlist'

/*
 * The day rail (design v9 §A2, §A5): the watchlist's day from 08:00 to 08:00 next morning in
 * Baghdad. The US regular session is a band captioned with its Baghdad times (from the New York
 * calendar, so it moves with US DST), each catalyst of a live pick is a diamond with its time and
 * name above and the picks' bases below, the elapsed part is darker and Now is a dot. One
 * labelled image for assistive tech; every catalyst is also text in its row's chips.
 */

const DAY = 24 * 3_600_000
const RAIL_W = { wide: 590, narrow: 250 }

interface Mark {
  kind: 'cat' | 'trig'
  key: string
  p: number
  ms: number
  time: string
  text: string
  importance: number
  insts: string[]
}

/** Label widths are estimated per character (Arabic, digits, spaces, Latin), as the prototype does. */
const estW = (s: string, bold = false) =>
  [...s].reduce((w, ch) => w + (/[؀-ۿ]/.test(ch) ? 6.1 : /[0-9:]/.test(ch) ? 7 : /\s/.test(ch) ? 3.4 : 6.6), 0) * (bold ? 1.04 : 1)

/** Lanes above the line: importance-5 catalysts nearest, then the rest, then the trigger. */
function lanes(items: Mark[], mode: 'wide' | 'narrow'): Array<{ lane: number; align: string }> {
  const occ: Array<Array<[number, number]>> = [[], [], [], []]
  const rank = (it: Mark) => (it.kind === 'cat' ? 10 - it.importance : 20)
  const res: Array<{ lane: number; align: string }> = new Array(items.length)
  const order = items.map((_, i) => i).sort((a, b) => rank(items[a]!) - rank(items[b]!) || items[a]!.p - items[b]!.p)
  for (const i of order) {
    const it = items[i]!
    const wpx = estW(it.time, true) + (mode === 'wide' ? 5 + estW(it.text) : 0) + 2
    const w = (wpx / RAIL_W[mode]) * 100
    let x0 = it.p - w / 2
    let x1 = it.p + w / 2
    let align = 'c'
    if (x0 < 0) [x0, x1, align] = [0, w, 's0']
    else if (x1 > 100) [x0, x1, align] = [100 - w, 100, 'e']
    const gap = mode === 'wide' ? 1.4 : 2.6
    let lane = occ.findIndex((o) => o.every(([a, b]) => x1 + gap < a || x0 - gap > b))
    if (lane < 0) lane = 3
    occ[lane]!.push([x0, x1])
    res[i] = { lane, align }
  }
  return res
}

const css = (vars: Record<string, string | number>) => vars as CSSProperties

export interface RailProps {
  date: string
  rows: Pick[]
  trigger: Trigger | null
  now: number
  lang: Lang
  /** The instruments whose row is hovered: their diamonds ring. */
  linkedInst: string | null
  onHoverCatalyst: (insts: string[] | null) => void
}

export function WatchlistRail({ date, rows, trigger, now, lang, linkedInst, onHoverCatalyst }: RailProps) {
  const { t } = useTranslation('warroom')
  const a = dayStart(date)
  const b = a + DAY
  const pos = (ms: number) => ((ms - a) / (b - a)) * 100
  const fmt = (ms: number) => formatTime(ms, lang)
  const list = (xs: string[]) => new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { style: 'long', type: 'conjunction' }).format(xs)
  const weekday = (ms: number) => new Intl.DateTimeFormat(lang === 'ar' ? 'ar-IQ-u-nu-latn' : 'en-GB', { timeZone: 'Asia/Baghdad', weekday: 'short' }).format(new Date(ms))

  // One mark per catalyst (time + name), carrying every live pick it moves.
  const byKey = new Map<string, Mark>()
  for (const r of rows) {
    for (const k of r.catalysts) {
      const ms = Date.parse(k.time)
      const key = `${ms}|${k.name}`
      const m = byKey.get(key) ?? { kind: 'cat' as const, key, p: pos(ms), ms, time: fmt(ms), text: k.name, importance: k.importance, insts: [] }
      m.importance = Math.max(m.importance, k.importance)
      if (!m.insts.includes(r.instId)) m.insts.push(r.instId)
      byKey.set(key, m)
    }
  }
  const all = [...byKey.values()].sort((x, y) => x.ms - y.ms)
  const inside = all.filter((k) => k.ms >= a && k.ms <= b)
  const later = all.filter((k) => k.ms > b)
  const marks: Mark[] = [...inside]
  const trigAt = trigger ? Date.parse(trigger.at) : NaN
  if (trigger && trigAt >= a && trigAt <= b) {
    marks.push({ kind: 'trig', key: 'trig', p: pos(trigAt), ms: trigAt, time: fmt(trigAt), text: t('wl.alertMark'), importance: 0, insts: [] })
  }
  marks.sort((x, y) => x.p - y.p)
  const wide = lanes(marks, 'wide')
  const narrow = lanes(marks, 'narrow')
  const nlw = Math.max(1, ...wide.map((x) => x.lane + 1))
  const nln = Math.max(1, ...narrow.map((x) => x.lane + 1))
  const sess = usSessions(a, b)
  const past = now <= a ? 0 : now >= b ? 100 : pos(now)
  const nowP = now > a && now < b ? pos(now) : null
  const underUs = (p: number) => sess.some(([o, c]) => p > pos(o) - 6 && p < pos(c) + 6)
  const laterText = list(later.map((k) => `${weekday(k.ms)} ${fmt(k.ms)} ${k.text}`))

  const label = t('wl.rail.label', {
    cats: inside.length
      ? t('wl.rail.cats', {
          list: inside.map((k) => t('wl.rail.cat', { name: k.text, time: k.time, bases: list(k.insts.map(baseOf)) })).join(lang === 'ar' ? '؛ ' : '; '),
        })
      : t('wl.rail.none'),
    us:
      (sess[0] ? t('wl.rail.us', { open: fmt(sess[0][2]), close: fmt(sess[0][3]) }) : '') +
      (later.length ? ` ${t('wl.laterTip', { list: laterText })}.` : ''),
  })

  return (
    <div className="wl-rail" role="img" aria-label={label} style={css({ '--nlw': nlw, '--nln': nln })} data-testid="wl-rail">
      <div className="wl-plot">
        {sess.map(([o, c, ro, rc]) => (
          <span key={o} className="wl-us" style={css({ '--x': `${pos(o).toFixed(2)}%`, '--w': `${(pos(c) - pos(o)).toFixed(2)}%` })} title={`${fmt(ro)}–${fmt(rc)}`} />
        ))}
        <div className="wl-labs" aria-hidden="true">
          {marks.map((it, i) => (
            <span
              key={it.key}
              className={`wl-lab is-${it.kind}${it.kind === 'cat' && it.ms < now ? ' is-past' : ''}`}
              style={css({ '--x': `${it.p.toFixed(2)}%`, '--lw': wide[i]!.lane, '--ln': narrow[i]!.lane })}
              data-aw={wide[i]!.align}
              data-an={narrow[i]!.align}
            >
              <b className="num">{it.time}</b>
              <span className="wl-lab-name" dir="auto">
                {it.text}
              </span>
            </span>
          ))}
        </div>
        <div className="wl-track">
          <span className="wl-line" />
          <span className="wl-line is-past" style={css({ '--w': `${past.toFixed(2)}%` })} />
          {marks.map((it) =>
            it.kind === 'trig' ? (
              <span key={it.key} className="wl-trig" style={css({ '--x': `${it.p.toFixed(2)}%` })}>
                <Zap aria-hidden="true" strokeWidth={2} />
              </span>
            ) : (
              <span
                key={it.key}
                className={`wl-cat${it.ms < now ? ' is-past' : ''}${it.importance >= 5 ? ' is-imp5' : ''}${linkedInst && it.insts.includes(linkedInst) ? ' is-linked' : ''}`}
                style={css({ '--x': `${it.p.toFixed(2)}%` })}
                title={t('wl.rail.tip', { time: it.time, name: it.text, imp: it.importance, insts: list(it.insts) })}
                data-testid="wl-diamond"
                onMouseEnter={() => onHoverCatalyst(it.insts)}
                onMouseLeave={() => onHoverCatalyst(null)}
              >
                <i />
              </span>
            ),
          )}
          {nowP !== null && <span className="wl-now" style={css({ '--x': `${nowP.toFixed(2)}%` })} />}
        </div>
        <div className="wl-baserow" aria-hidden="true">
          {marks
            .filter((x) => x.kind === 'cat')
            .map((it) => (
              <span key={it.key} className="wl-bases" style={css({ '--x': `${it.p.toFixed(2)}%` })}>
                {it.insts.map((id) => (
                  <span key={id} className="wl-mono">
                    {baseOf(id)}
                  </span>
                ))}
              </span>
            ))}
        </div>
      </div>
      <div className="wl-axis" aria-hidden="true">
        {[0, 4, 8, 12, 16, 20].map((h) => {
          const p = (h / 24) * 100
          const hide = (nowP !== null && Math.abs(nowP - p) < 6.3) || underUs(p)
          return (
            <span
              key={h}
              className={`wl-tick num${h === 0 ? ' is-first' : ''}${h % 8 ? ' is-minor' : ''}${hide ? ' is-hidden' : ''}`}
              style={css({ '--x': `${p}%` })}
            >
              {fmt(a + h * 3_600_000)}
            </span>
          )
        })}
        {sess.map(([o, c, ro, rc]) => (
          <span key={o} className="wl-tick is-us" style={css({ '--x': `${((pos(o) + pos(c)) / 2).toFixed(2)}%` })} title={t('wl.usHoursTip')}>
            <span className="wl-us-full">
              {fillSlots(t('wl.usHoursAt', { range: slot('range') }), {
                range: (
                  <bdi dir="ltr" className="fig num">
                    {fmt(ro)}–{fmt(rc)}
                  </bdi>
                ),
              })}
            </span>
            <span className="wl-us-short">{t('wl.usShort')}</span>
          </span>
        ))}
        {nowP !== null && (
          <span className={`wl-tick is-now${nowP < 3 ? ' is-first' : nowP > 97 ? ' is-last' : ''}`} style={css({ '--x': `${nowP.toFixed(2)}%` })}>
            {t('wl.now')}
          </span>
        )}
        {later.length > 0 && (
          <span className="wl-later" title={t('wl.laterTip', { list: laterText })}>
            {t('wl.later', { n: later.length })}
          </span>
        )}
      </div>
    </div>
  )
}
