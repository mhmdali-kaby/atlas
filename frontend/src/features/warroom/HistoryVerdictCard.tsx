import {
  Activity,
  CircleCheck,
  CircleDot,
  CircleMinus,
  CircleX,
  Database,
  Minus,
  MoveHorizontal,
  Scale,
  Sigma,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  CircleAlert,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react'
import { Fragment, useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatSigned, type Lang } from '../../lib/format'
import {
  isSmallSampleRule,
  KNOWN_REGIMES,
  MIN_N,
  normaliseRaw,
  type Correction,
  type Dist,
  type HistoryVerdictCard as Card,
  type Unmatched,
  type Verdict,
} from './historian'
import { InstrumentId, Inline } from './text'
import { baghdadDay, formatDate, formatFullTime, formatTime } from './time'
import { UntracedMark, UntracedScope } from './Untraced'

/*
 * HistoryVerdict card (design v7 "Historian cards and live reasoning" §5): question, then the
 * verdict, then the evidence. Statistics keep their sign but stay in label ink (they are not
 * P&L); the verdict pill is the one coloured thing on the card, always icon + words.
 */

// ---------------------------------------------------------------------------------- formats

/** A signed return given as a fraction: 0.0094 → "+0.94%", −0.0383 → "−3.83%". */
export const pctSigned = (v: number, lang: Lang) => formatSigned(v * 100, 'percent', lang)
/** A 0–100 share → "58%". */
export const pctWhole = (v: number, lang: Lang) => `${formatFixed(v, 0, lang)}%`
/** A 0–1 probability → "71%". */
export const probability = (p: number, lang: Lang) => `${formatFixed(p * 100, 0, lang)}%`

/** A translated template split around {{slot}} placeholders, each placed as a React node. */
export function fillSlots(template: string, nodes: Record<string, ReactNode>): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of template.matchAll(/([a-z]+)/g)) {
    if (m.index > last) out.push(<Fragment key={`t${last}`}>{template.slice(last, m.index)}</Fragment>)
    out.push(<Fragment key={`s${m.index}`}>{nodes[m[1]!] ?? null}</Fragment>)
    last = m.index + m[0].length
  }
  if (last < template.length) out.push(<Fragment key={`t${last}`}>{template.slice(last)}</Fragment>)
  return out
}
/** The slot marker passed to t() for a value that fillSlots replaces. */
export const slot = (name: string) => `${name}`

export function Fig({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <bdi dir="ltr" className={className ? `fig num ${className}` : 'fig num'}>
      {children}
    </bdi>
  )
}

/** days_in_regime → "6 days", "10 hours", "under an hour". */
export function useSpanText() {
  const { t } = useTranslation('warroom')
  return (d: number) => {
    if (d < 1 / 24) return t('rg.underHour')
    if (d < 1) return t('rg.hours', { count: Math.round(d * 24) })
    return t('rg.days', { count: Math.round(d) })
  }
}

/** "Data as of 14:33" today in Baghdad, "Data as of 27 Oct, 18:40" on another day. */
export function AsOf({ ts, lang }: { ts: string; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const today = baghdadDay(ts) === baghdadDay(Date.now())
  const when = today ? formatTime(ts, lang) : `${formatDate(ts, lang)}${lang === 'ar' ? '، ' : ', '}${formatTime(ts, lang)}`
  return (
    <time className="hv-asof" dateTime={ts} tabIndex={0} title={formatFullTime(ts, lang)} data-testid="hv-asof">
      {fillSlots(t('hv.asOf', { time: slot('time') }), { time: <span className="num">{when}</span> })}
    </time>
  )
}

// ----------------------------------------------------------------------------------- regime

const REGIME_ICON: Record<string, { Icon: LucideIcon; dir?: boolean }> = {
  trend_up: { Icon: TrendingUp, dir: true },
  trend_down: { Icon: TrendingDown, dir: true },
  range: { Icon: MoveHorizontal },
  high_vol: { Icon: Activity },
  low_vol: { Icon: Minus },
}

/** A regime label with its icon; an unknown label is shown raw in an LTR island. */
export function RegimeLabel({ label }: { label: string }) {
  const { t } = useTranslation('warroom')
  const { Icon, dir } = REGIME_ICON[label] ?? { Icon: CircleDot }
  const known = KNOWN_REGIMES.has(label) || label === 'no_data' || label === 'insufficient_data'
  return (
    <span className="hv-rg">
      <Icon aria-hidden="true" strokeWidth={1.5} className={dir ? 'icon-dir' : undefined} />
      {known ? <span>{t(`rg.label.${label}`)}</span> : <bdi dir="ltr">{label}</bdi>}
    </span>
  )
}

export function RuleBased() {
  const { t } = useTranslation('warroom')
  return (
    <span className="hv-rule" tabIndex={0} title={t('rg.ruleTip')}>
      {t('rg.rule')}
    </span>
  )
}

// ---------------------------------------------------------------------------------- verdict

/** Lucide has no dashed check circle: its circle-dashed with circle-check's tick. */
function CircleCheckDashed(props: LucideProps) {
  return (
    <svg viewBox="0 0 24 24" width={24} height={24} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M10.1 2.182a10 10 0 0 1 3.8 0" />
      <path d="M13.9 21.818a10 10 0 0 1-3.8 0" />
      <path d="M17.609 3.721a10 10 0 0 1 2.69 2.7" />
      <path d="M2.182 13.9a10 10 0 0 1 0-3.8" />
      <path d="M20.279 17.609a10 10 0 0 1-2.7 2.69" />
      <path d="M21.818 10.1a10 10 0 0 1 0 3.8" />
      <path d="M3.721 6.391a10 10 0 0 1 2.7-2.69" />
      <path d="M6.391 20.279a10 10 0 0 1-2.69-2.7" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

const VERDICT_ICON: Record<Verdict, (p: LucideProps) => ReactNode> = {
  supports: (p) => <CircleCheck {...p} />,
  weakly_supports: (p) => <CircleCheckDashed {...p} />,
  does_not_support: (p) => <CircleMinus {...p} />,
  contradicts: (p) => <CircleX {...p} />,
}

function VerdictPill({ verdict }: { verdict: Verdict }) {
  const { t } = useTranslation('warroom')
  return (
    <span className={`verdict v-${verdict}`} data-testid="hv-verdict" data-verdict={verdict}>
      <span className="sr-only">{t('hv.verdictSr')} </span>
      {VERDICT_ICON[verdict]({ 'aria-hidden': true, strokeWidth: 1.75 })}
      <span>{t(`hv.v.${verdict}`)}</span>
    </span>
  )
}

/**
 * What a verdict rule changed (card meta verdict_corrections, ATL-93/ATL-99): a small-sample cap
 * to weakly_supports reads "Capped from Supports: 12 cases", a gapped backtest "Lowered from …",
 * and anything else — contradicts → does_not_support on a small sample, or a rule this build does
 * not know — the generic "Changed from Contradicts: 5 cases".
 */
function CorrectionNote({ c, n }: { c: Correction; n: number }) {
  const { t } = useTranslation('warroom')
  const from = t(`hv.v.${c.from}`)
  const cases = t('hv.cases', { count: c.n ?? n })
  const small = isSmallSampleRule(c.rule)
  const [text, tip] =
    small && c.to === 'weakly_supports'
      ? [t('hv.corr.capped', { from, cases }), t('hv.corr.cappedTip')]
      : c.rule === 'backtest_on_gapped_data'
        ? [t('hv.corr.gapped', { from }), t('hv.corr.gappedTip')]
        : [t('hv.corr.changed', { from, cases }), t(small ? 'hv.corr.changedTip' : 'hv.corr.changedOtherTip')]
  return (
    <span className="hv-corr" tabIndex={0} title={tip} data-testid="hv-corr" data-rule={c.rule}>
      <Scale aria-hidden="true" strokeWidth={1.5} />
      <span>{text}</span>
    </span>
  )
}

// ----------------------------------------------------------------------------------- sample

function Sample({ card, lang }: { card: Card; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const segs: ReactNode[] = []
  if (card.instId) segs.push(<InstrumentId id={card.instId} />)
  if (card.bar) {
    const label = t(`hv.bar.${card.bar}`, { defaultValue: '' })
    segs.push(label ? <span>{label}</span> : <Fig>{card.bar}</Fig>)
  }
  if (card.period) {
    const [a = '', b = ''] = card.period.split('..')
    const da = Date.parse(a)
    const db = Date.parse(b)
    segs.push(
      Number.isFinite(da) && Number.isFinite(db) ? (
        <span>
          {fillSlots(t('hv.period', { from: slot('from'), to: slot('to') }), {
            from: <span className="num">{formatDate(da, lang, true)}</span>,
            to: <span className="num">{formatDate(db, lang, true)}</span>,
          })}
        </span>
      ) : (
        <Fig>{card.period}</Fig>
      ),
    )
  }
  segs.push(
    card.noData ? (
      <span className="hv-n" title={t('hv.noCasesTip')} data-testid="hv-n">
        {t('hv.noCases')}
      </span>
    ) : (
      <span className="hv-n num" data-testid="hv-n">
        {t('hv.cases', { count: card.n })}
      </span>
    ),
  )
  return (
    <p className="hv-sample" data-testid="hv-sample">
      <span className="sr-only">{t('hv.sample')}: </span>
      {segs.map((s, i) => (
        <span key={i} className="hv-seg">
          {s}
        </span>
      ))}
      {!card.noData && card.n < MIN_N && (
        <span className="hv-weak" tabIndex={0} title={t('hv.weakTip')} data-testid="hv-weak">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>{t('hv.weak')}</span>
        </span>
      )}
    </p>
  )
}

// ------------------------------------------------------------------------------ range strip

const STRIP_W = 144
const STRIP_C = STRIP_W / 2

/** A small forest plot on its own symmetric scale around zero: these cases (top) over the base rate. */
function RangeStrip({ cases, base, weak, id }: { cases: Dist; base: Dist | null; weak: boolean; id: string }) {
  const vals = [cases, base].flatMap((q) => (q ? [q.p10, q.p90, q.median] : [])).map(Math.abs)
  const ext = Math.max(...vals, 1e-6) * 1.1
  const x = (v: number) => STRIP_C + (v / ext) * (STRIP_C - 4)
  const band = (q: Dist, y: number, cls: string, fill?: string) => (
    <>
      <rect className={cls} x={x(q.p10)} y={y - 3} width={Math.max(3, x(q.p90) - x(q.p10))} height={6} rx={3} style={fill ? { fill } : undefined} />
      <rect className={`${cls}-med`} x={x(q.median) - 1} y={y - 6} width={2} height={12} rx={1} />
    </>
  )
  const pid = `hatch-${id}`
  return (
    <svg className={weak ? 'hv-strip is-weak' : 'hv-strip'} width={STRIP_W} height={48} viewBox={`0 0 ${STRIP_W} 48`} aria-hidden="true" focusable="false">
      {weak && (
        <defs>
          <pattern id={pid} width={4} height={4} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect className="hatch" width={1.6} height={4} />
          </pattern>
        </defs>
      )}
      <line className="zero" x1={STRIP_C} x2={STRIP_C} y1={2} y2={46} />
      {base && band(base, 36, 'b-base')}
      {band(cases, 12, 'b-cases', weak ? `url(#${pid})` : undefined)}
    </svg>
  )
}

// ---------------------------------------------------------------------------- horizon table

/** Marks a card figure when its path is untraced. */
type MarkFn = (path: string, node: ReactNode) => ReactNode

/** Shows an inline-end fade while a horizontal scroller has more to the inline end (design review v7, 390 px). */
function useMoreAtEnd() {
  const ref = useRef<HTMLDivElement>(null)
  const [more, setMore] = useState(false)
  const measure = useCallback(() => {
    const el = ref.current
    if (!el) return
    const room = el.scrollWidth - el.clientWidth
    setMore(room > 1 && Math.abs(el.scrollLeft) < room - 1)
  }, [])
  useEffect(() => {
    const el = ref.current
    if (!el) return
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure])
  return { ref, more, onScroll: measure }
}

function HorizonTable({ card, lang, mark }: { card: Card; lang: Lang; mark: MarkFn }) {
  const { t } = useTranslation('warroom')
  const id = useId().replace(/:/g, '')
  const scroller = useMoreAtEnd()
  const weak = card.n < MIN_N
  const cell = (path: string, text: string) => mark(path, <Fig>{text}</Fig>)
  const cells = (q: Dist | null, root: string, h: string) =>
    q ? (
      <>
        <td className="hv-num">{cell(`${root}.${h}.median`, pctSigned(q.median, lang))}</td>
        <td className="hv-num hv-mid">
          {fillSlots(t('hv.range', { a: slot('a'), b: slot('b') }), {
            a: cell(`${root}.${h}.p10`, pctSigned(q.p10, lang)),
            b: cell(`${root}.${h}.p90`, pctSigned(q.p90, lang)),
          })}
        </td>
        <td className="hv-num">{cell(`${root}.${h}.pct_positive`, pctWhole(q.pct_positive, lang))}</td>
      </>
    ) : (
      <td colSpan={3} className="hv-none">
        {t('hv.noBase')}
      </td>
    )
  return (
    <div
      ref={scroller.ref}
      onScroll={scroller.onScroll}
      className={scroller.more ? 'hv-dist has-more' : 'hv-dist'}
      data-testid="hv-dist"
      // A scroller with content past its edge must be reachable by keyboard (axe scrollable-region-focusable).
      tabIndex={scroller.more ? 0 : undefined}
      aria-label={scroller.more ? t('hv.more') : undefined}
      role={scroller.more ? 'region' : undefined}
    >
      <table className="hv-table">
        <caption className="sr-only">{t('hv.table')}</caption>
        <thead>
          <tr>
            <th scope="col" className="hv-h">
              {t('hv.col.horizon')}
            </th>
            <td />
            <th scope="col" className="hv-num">
              {t('hv.col.median')}
            </th>
            <th scope="col" className="hv-num">
              <span className="hv-tip" tabIndex={0} title={t('hv.col.mid80Tip')}>
                {t('hv.col.mid80')}
              </span>
            </th>
            <th scope="col" className="hv-num">
              <span className="hv-tip" tabIndex={0} title={t('hv.col.roseTip')}>
                {t('hv.col.rose')}
              </span>
            </th>
            <td className="hv-strip-cell" aria-hidden="true">
              <span className="hv-axis" dir="ltr">
                <span>−</span>
                <span>0</span>
                <span>+</span>
              </span>
            </td>
          </tr>
        </thead>
        {card.horizons.map((h, i) => {
          const label = h.hours !== null && Number.isInteger(h.hours) ? t('hv.h', { count: h.hours }) : null
          const tip = t('hv.stripTip', { a: pctSigned(h.cases.median, lang), b: h.base ? pctSigned(h.base.median, lang) : '—' })
          return (
            <tbody key={h.key} data-testid="hv-horizon" data-horizon={h.key}>
              <tr className="is-cases">
                <th scope="rowgroup" rowSpan={2} className="hv-h">
                  {label ? <span className="num">{label}</span> : <Fig>{h.key}</Fig>}
                </th>
                <th scope="row" className="hv-who">
                  {t('hv.row.cases')}
                </th>
                {cells(h.cases, 'distribution', h.key)}
                <td rowSpan={2} className="hv-strip-cell" title={tip}>
                  <RangeStrip cases={h.cases} base={h.base} weak={weak} id={`${id}-${i}`} />
                </td>
              </tr>
              <tr className="is-base">
                <th scope="row" className="hv-who">
                  <span className="hv-tip" tabIndex={0} title={t('hv.row.baseTip', { h: label ?? h.key })}>
                    {t('hv.row.base')}
                  </span>
                </th>
                {cells(h.base, 'base_rate', h.key)}
              </tr>
            </tbody>
          )
        })}
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------- context rows

function Context({ card, lang, mark }: { card: Card; lang: Lang; mark: MarkFn }) {
  const { t } = useTranslation('warroom')
  const spanText = useSpanText()
  const comma = <span className="hv-comma">{lang === 'ar' ? '، ' : ', '}</span>
  if (!card.regime && !card.stop) return null
  const r = card.regime
  const s = card.stop
  return (
    <dl className="hv-ctx">
      {r && (
        <>
          <dt>{t('hv.regime')}</dt>
          <dd data-testid="hv-regime">
            <RegimeLabel label={r.label} />
            {comma}
            {r.probability === null ? (
              <RuleBased />
            ) : (
              <span>
                {fillSlots(t('rg.likely', { p: slot('p') }), { p: mark('regime.probability', <Fig>{probability(r.probability, lang)}</Fig>) })}
              </span>
            )}
            {r.days !== null && (
              <>
                {comma}
                <span>{t('rg.for', { d: spanText(r.days) })}</span>
              </>
            )}
          </dd>
        </>
      )}
      {s && (
        <>
          <dt>{t('hv.stop')}</dt>
          <dd data-testid="hv-stop">
            <span>
              {fillSlots(t('hv.stopLine', { stop: slot('stop'), range: slot('range') }), {
                stop: mark('stop_noise_check.stop_distance_atr', <Fig>{formatFixed(s.stopAtr, 1, lang)}</Fig>),
                range: mark('stop_noise_check.median_4h_range_atr', <Fig>{formatFixed(s.rangeAtr, 1, lang)}</Fig>),
              })}
            </span>
            <span className={s.verdict === 'inside_noise' ? 'hv-noise is-inside' : 'hv-noise is-ok'}>
              {s.verdict === 'inside_noise' ? <TriangleAlert aria-hidden="true" strokeWidth={2} /> : <CircleCheck aria-hidden="true" strokeWidth={2} />}
              <span>{t(`hv.stopState.${s.verdict}`)}</span>
            </span>
          </dd>
        </>
      )}
    </dl>
  )
}

// -------------------------------------------------------------------------------- the card

/** Key stat: an untraced figure is marked where it appears in the line, else the whole line is. */
function KeyStat({ text, raws }: { text: string; raws: string[] }) {
  const norm = normaliseRaw(text)
  const found = raws.filter((r) => norm.includes(normaliseRaw(r)))
  const body = <Inline text={text} />
  return (
    <p className="hv-key" dir="auto" data-testid="hv-key">
      {raws.length === 0 ? body : found.length > 0 ? <UntracedScope raws={found}>{body}</UntracedScope> : <UntracedMark>{body}</UntracedMark>}
    </p>
  )
}

export function HistoryVerdictCard({ card, unmatched, lang }: { card: Card; unmatched: Unmatched[]; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const cardPaths = new Set(unmatched.filter((u) => u.location === 'card' && u.path).map((u) => u.path!))
  const keyRaws = unmatched.filter((u) => u.location === 'card' && u.path === 'key_stat').map((u) => u.raw)
  const mark: MarkFn = (path, node) => (cardPaths.has(path) ? <UntracedMark>{node}</UntracedMark> : node)
  return (
    <UntracedScope raws={[]}>
    <div className="hv-card" role="group" aria-label={t('kind.research_report')} data-testid="hv-card">
      <div className="hv-head">
        <span className="hv-tile">
          <Sigma aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="hv-kind">{t('kind.research_report')}</span>
        <AsOf ts={card.dataAsOf} lang={lang} />
      </div>
      <p className="hv-q" dir="auto" data-testid="hv-question">
        <Inline text={card.question} />
      </p>
      <div className="hv-verdict">
        <VerdictPill verdict={card.verdict} />
        {card.corrections.map((c, i) => (
          <CorrectionNote key={i} c={c} n={card.n} />
        ))}
      </div>
      {card.keyStat && <KeyStat text={card.keyStat} raws={keyRaws} />}
      <div className="hv-evidence">
        <Sample card={card} lang={lang} />
        {card.horizons.length > 0 && <HorizonTable card={card} lang={lang} mark={mark} />}
        <Context card={card} lang={lang} mark={mark} />
      </div>
      {card.overfit && (
        <p className="hv-warn" data-testid="hv-overfit">
          <TriangleAlert aria-hidden="true" strokeWidth={2} />
          <span>{t('hv.overfit')}</span>
        </p>
      )}
      {card.notes.length > 0 && (
        <div className="hv-notes" data-testid="hv-notes">
          <p className="hv-notes-h">
            <Database aria-hidden="true" strokeWidth={1.5} />
            <span>{t('hv.notes')}</span>
          </p>
          <ul>
            {card.notes.map((n, i) => (
              <li key={i} dir="auto">
                <Inline text={n} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {unmatched.length > 0 && (
        <p className="hv-unv-note" data-testid="hv-unv-note">
          <CircleAlert aria-hidden="true" strokeWidth={1.75} />
          <span>{t('hv.unvNote', { count: unmatched.length })}</span>
        </p>
      )}
    </div>
    </UntracedScope>
  )
}
