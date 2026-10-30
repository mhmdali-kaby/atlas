import {
  ArrowDownRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  CircleQuestionMark,
  ExternalLink,
  Eye,
  Newspaper,
  TriangleAlert,
} from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatDecimal, formatFixed, type Lang } from '../../lib/format'
import { useNow } from '../portfolio/hooks'
import type { Unmatched } from './historian'
import { AsOf, Fig } from './historianParts'
import { outages, sourceLabel, type FlowRow, type NewsBrief, type NewsCluster, type NewsEvent, type NewsSource, type PriceMove } from './newsBrief'
import { fillSlots, slot } from './slots'
import { InstrumentId, Markdown } from './text'
import { baghdadDay, formatDateTime, formatDayShort, formatFullTime, formatTime, formatUtcFull, formatUtcTime } from './time'
import { UntracedMark, UntracedScope } from './Untraced'
import './newsBrief.css'

/*
 * NewsBriefCard (design v9 "Watchlist and news briefs" §B): News Intelligence's research_report
 * with a news_brief card, in three forms — the 07:30 morning brief, an instrument brief answering
 * @News, and an alert analysis replying under an ingest news_alert. Headlines, source names and
 * event names are untrusted internet text (brief rule 7): plain React text with dir="auto", never
 * markdown or HTML. what_to_watch is the agent's own words and goes through the War Room tokenizer.
 * Status is decided by code (ATL-106) and always shown as an icon plus its word.
 */

const DAY_MS = 86_400_000
const STORY_LIMIT = 5

type Mark = (path: string, node: ReactNode) => ReactNode

// ------------------------------------------------------------------------------- small parts

const STATUS_ICON = { CONFIRMED: CheckCheck, REPORTED: Check, RUMOUR: CircleQuestionMark } as const

function useList(lang: Lang) {
  return (items: string[]) => new Intl.ListFormat(lang === 'ar' ? 'ar' : 'en', { type: 'conjunction' }).format(items)
}

function StatusBadge({ c, lang }: { c: NewsCluster; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const list = useList(lang)
  const names = list(c.statusSources.map((k) => sourceLabel(k, c.sources)))
  const tip =
    c.status === 'CONFIRMED'
      ? t('news_brief.tip.confirmed', { sources: names })
      : c.status === 'REPORTED'
        ? t('news_brief.tip.one', { sources: names })
        : t('news_brief.tip.rumour')
  const Icon = STATUS_ICON[c.status]
  return (
    <span className={`nb-status is-${c.status.toLowerCase()}`} tabIndex={0} title={tip} data-testid="nb-status" data-status={c.status}>
      <Icon aria-hidden="true" strokeWidth={2} />
      <span>{t(`tag.${c.status}`)}</span>
      <span className="sr-only">. {tip}</span>
    </span>
  )
}

function Pips({ n }: { n: number }) {
  const { t } = useTranslation('warroom')
  const label = t('news.impOf', { n })
  return (
    <span className={`nb-imp imp-${n}`} role="img" aria-label={label} title={label} data-testid="nb-imp">
      <span className="news-pips" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => (
          <i key={i} className={i <= n ? 'is-on' : undefined} style={{ height: 2 + i * 2 }} />
        ))}
      </span>
    </span>
  )
}

/** Sentiment −1…1: sign + arrow + a spoken word, never colour alone; |s| < 0.05 is neutral. */
function Senti({ value, lang, mark = (_p, n) => n, path = '' }: { value: number; lang: Lang; mark?: Mark; path?: string }) {
  const { t } = useTranslation('warroom')
  const tone = value >= 0.05 ? 'profit' : value <= -0.05 ? 'loss' : 'zero'
  const text = tone === 'zero' ? formatFixed(0, 2, lang) : formatDecimal(value.toFixed(2), lang, 2, 'exceptZero')
  const Icon = tone === 'profit' ? ArrowUpRight : ArrowDownRight
  return (
    <span className={`signed is-${tone}`} data-sign={tone}>
      {tone !== 'zero' && <Icon aria-hidden="true" strokeWidth={2.25} className="icon-dir" />}
      {mark(path, <Fig>{text}</Fig>)}
      <span className="sr-only"> {t(tone === 'profit' ? 'news.positive' : tone === 'loss' ? 'news.negative' : 'news.neutral')}</span>
    </span>
  )
}

/** "04:47 (01:47 UTC)" today, "27 Oct, 22:40 (19:40 UTC)" on another Baghdad day. */
function PubTime({ ts, lang, now }: { ts: string; lang: Lang; now: number }) {
  const { t } = useTranslation('warroom')
  const today = baghdadDay(ts) === baghdadDay(now)
  const tip = t('news_brief.timeTip', { baghdad: formatFullTime(ts, lang), utc: formatUtcFull(ts) })
  return (
    <time className="nb-time" dateTime={new Date(Date.parse(ts)).toISOString()} title={tip} tabIndex={0} data-testid="nb-time">
      {fillSlots(t('news_brief.pub', { time: slot('time'), utc: slot('utc') }), {
        time: <span className="num" data-testid="nb-baghdad">{today ? formatTime(ts, lang) : formatDateTime(ts, lang)}</span>,
        utc: (
          <bdi dir="ltr" className="num" data-testid="nb-utc">
            {formatUtcTime(ts)} UTC
          </bdi>
        ),
      })}
    </time>
  )
}

function SourceLine({ s, lang, now, tail }: { s: NewsSource; lang: Lang; now: number; tail?: ReactNode }) {
  const { t } = useTranslation('warroom')
  const host = s.url ? s.url.hostname.replace(/^www\./, '') : ''
  return (
    <li className="nb-src" data-testid="nb-src">
      <bdi className="nb-src-name" dir="auto">
        {s.name}
      </bdi>
      {s.publishedAt && <PubTime ts={s.publishedAt} lang={lang} now={now} />}
      {s.url && host && (
        <a className="nb-src-link" href={s.url.href} target="_blank" rel="noopener noreferrer" aria-label={t('news_brief.readOn', { host })} data-testid="nb-src-link">
          <bdi dir="ltr">{host}</bdi>
          <ExternalLink aria-hidden="true" strokeWidth={1.5} />
        </a>
      )}
      {tail}
    </li>
  )
}

/** An instrument with its move since the story was first seen: label ink, not P&L colour. */
function InstMove({ move, firstSeen, lang, now, mark, path }: { move: PriceMove; firstSeen: string | null; lang: Lang; now: number; mark: Mark; path: string }) {
  const { t } = useTranslation('warroom')
  const v = move.value === null ? null : `${formatDecimal(move.value.toFixed(1), lang, 1, 'exceptZero')} ATR`
  const when = firstSeen ? (baghdadDay(firstSeen) === baghdadDay(now) ? formatTime(firstSeen, lang) : formatDateTime(firstSeen, lang)) : '—'
  const tip = v === null ? (move.reason ?? t('news_brief.moveNone', { inst: move.instId })) : t('news_brief.moveTip', { inst: move.instId, v, time: when })
  return (
    <li className="nb-inst" title={tip} tabIndex={0} data-testid="nb-inst">
      <InstrumentId id={move.instId} />
      {v !== null && (
        <span className="nb-move" data-testid="nb-move">
          {mark(path, <Fig>{v}</Fig>)}
          <span className="sr-only"> {t('news_brief.sinceSr')}</span>
        </span>
      )}
    </li>
  )
}

function BaseChip({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span className="nb-base" title={id}>
      <bdi dir="ltr">{cut > 0 ? id.slice(0, cut) : id}</bdi>
      {cut > 0 && <span className="sr-only">{id.slice(cut)}</span>}
    </span>
  )
}

function Priced({ p }: { p: NonNullable<NewsBrief['pricedIn']> }) {
  const { t } = useTranslation('warroom')
  return (
    <span className={`nb-priced is-${p}`} tabIndex={0} title={t(`news_brief.priced.${p}Tip`)} data-testid="nb-priced">
      {t(`news_brief.priced.${p}`)}
    </span>
  )
}

function SecHead({ title, sub }: { title: ReactNode; sub?: ReactNode }) {
  return (
    <h3 className="nb-sec-h">
      <span>{title}</span>
      {sub && <span className="nb-sec-sub">{sub}</span>}
    </h3>
  )
}

const hours = (n: number) => <Fig>{String(n)}</Fig>

// -------------------------------------------------------------------------------- stories

function Story({ c, i, ranked, allSources, lang, now, mark }: { c: NewsCluster; i: number; ranked: boolean; allSources: boolean; lang: Lang; now: number; mark: Mark }) {
  const { t } = useTranslation('warroom')
  const [open, setOpen] = useState(false)
  const listId = useId()
  const type = c.eventType ? t(`news.type.${c.eventType}`, { defaultValue: '' }) : ''
  const showAll = allSources || open
  const shown = showAll ? c.sources : c.sources.slice(0, 1)
  const more = c.sources.length - 1
  const toggle =
    !allSources && more > 0 ? (
      <button type="button" className="nb-src-more" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((o) => !o)} data-testid="nb-src-more">
        {open ? t('news_brief.fewerSrc') : t('news_brief.moreSrc', { count: more })}
      </button>
    ) : null
  return (
    <li className="nb-story" data-testid="nb-story">
      {ranked && (
        <span className="nb-rank num" aria-hidden="true">
          {i + 1}
        </span>
      )}
      <div className="nb-story-main">
        <p className="nb-headline" dir="auto" data-testid="nb-headline">
          {ranked && <span className="sr-only">{i + 1}. </span>}
          {c.headline}
        </p>
        <div className="nb-tags">
          <StatusBadge c={c} lang={lang} />
          {type && <span className="nb-type">{type}</span>}
          <Pips n={c.importance} />
        </div>
        {(c.moves.length > 0 || c.sentiment !== null) && (
          <div className="nb-meta">
            {c.moves.length > 0 && (
              <ul className="nb-insts" aria-label={t('news.instruments')}>
                {c.moves.map((m, j) => (
                  <InstMove key={m.instId} move={m} firstSeen={c.firstSeen} lang={lang} now={now} mark={mark} path={`clusters.${i}.price_move_since_first_seen_atr.${j}.value`} />
                ))}
              </ul>
            )}
            {c.sentiment !== null && (
              <span className="nb-senti" data-testid="nb-senti">
                <span>{t('news.sentiment')}</span>
                <Senti value={c.sentiment} lang={lang} mark={mark} path={`clusters.${i}.sentiment`} />
              </span>
            )}
          </div>
        )}
        {c.sources.length > 0 && (
          <div className="nb-srcs">
            <ul className="nb-src-list" id={listId}>
              {shown.map((s, j) => (
                <SourceLine key={j} s={s} lang={lang} now={now} tail={j === shown.length - 1 ? toggle : undefined} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </li>
  )
}

function Stories({ brief, ranked, limit, allSources, empty, lang, now, mark }: { brief: NewsBrief; ranked: boolean; limit: boolean; allSources: boolean; empty: string; lang: Lang; now: number; mark: Mark }) {
  const { t } = useTranslation('warroom')
  const [open, setOpen] = useState(false)
  const listId = useId()
  const all = brief.clusters
  if (all.length === 0) return <p className="nb-empty">{empty}</p>
  const list = limit && !open ? all.slice(0, STORY_LIMIT) : all
  const Chev = open ? ChevronUp : ChevronDown
  return (
    <>
      <ol className={ranked ? 'nb-stories is-ranked' : 'nb-stories'} id={listId}>
        {list.map((c, i) => (
          <Story key={`${c.clusterId}:${i}`} c={c} i={i} ranked={ranked} allSources={allSources} lang={lang} now={now} mark={mark} />
        ))}
      </ol>
      {limit && all.length > STORY_LIMIT && (
        <button type="button" className="nb-more" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((o) => !o)} data-testid="nb-more">
          <Chev aria-hidden="true" strokeWidth={1.75} />
          <span>{open ? t('news_brief.showFewer') : fillSlots(t('news_brief.showAll', { n: slot('n') }), { n: <Fig>{String(all.length)}</Fig> })}</span>
        </button>
      )}
    </>
  )
}

// ------------------------------------------------------------------------------- calendar

function ValueCell({ v, label, cls }: { v: string | null; label: string; cls: string }) {
  const { t } = useTranslation('warroom')
  return (
    <td className={`is-num ${cls}`} role="cell" data-l={label}>
      {v === null ? (
        <>
          <span aria-hidden="true">—</span>
          <span className="sr-only">{t('news_brief.none')}</span>
        </>
      ) : (
        <Fig>{v.replace(/^-(?=\d)/, '−')}</Fig>
      )}
    </td>
  )
}

function Calendar({ events, grouped, caption, lang, now }: { events: NewsEvent[]; grouped: boolean; caption: string; lang: Lang; now: number }) {
  const { t } = useTranslation('warroom')
  const groups: Array<{ day: string; ts: string; rows: NewsEvent[] }> = []
  for (const e of events) {
    const d = baghdadDay(e.time)
    const last = groups[groups.length - 1]
    if (grouped && last && last.day === d) last.rows.push(e)
    else if (!grouped && last) last.rows.push(e)
    else groups.push({ day: d, ts: e.time, rows: [e] })
  }
  const dayLabel = (ts: string) => {
    const date = formatDayShort(ts, lang)
    const d = baghdadDay(ts)
    if (d === baghdadDay(now)) return t('news_brief.day.today', { date })
    if (d === baghdadDay(now + DAY_MS)) return t('news_brief.day.tomorrow', { date })
    return date
  }
  return (
    <div className="nb-cal-wrap">
      <table className="nb-cal" role="table" data-testid="nb-cal">
        <caption className="sr-only">{caption}</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" className="nb-time-cell">
              {t('news_brief.col.time')}
            </th>
            <th scope="col" role="columnheader">
              {t('news_brief.col.event')}
            </th>
            <th scope="col" role="columnheader" className="nb-imp-cell">
              {t('news_brief.col.imp')}
            </th>
            <th scope="col" role="columnheader" className="is-num">
              {t('news_brief.col.forecast')}
            </th>
            <th scope="col" role="columnheader" className="is-num">
              {t('news_brief.col.previous')}
            </th>
            <th scope="col" role="columnheader" className="nb-aff-cell">
              {t('news_brief.col.affected')}
            </th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody role="rowgroup" key={g.day + g.ts}>
            {grouped && (
              <tr className="nb-day" role="row">
                <th colSpan={6} scope="rowgroup" role="rowheader" data-testid="nb-day">
                  {dayLabel(g.ts)}
                </th>
              </tr>
            )}
            {g.rows.map((e, i) => {
              const past = Date.parse(e.time) <= now
              const tip = t('news_brief.timeTip', { baghdad: formatFullTime(e.time, lang), utc: formatUtcFull(e.time) })
              const today = baghdadDay(e.time) === baghdadDay(now)
              return (
                <tr key={i} className={past ? 'is-past' : undefined} role="row" data-testid="nb-event">
                  <td className="nb-time-cell" role="cell">
                    <time className="num" dateTime={e.time} title={past ? `${t('news_brief.passed')}. ${tip}` : tip}>
                      {grouped || today ? formatTime(e.time, lang) : formatDateTime(e.time, lang)}
                    </time>
                    {past && (
                      <>
                        <Check aria-hidden="true" strokeWidth={2} className="nb-past-ic" />
                        <span className="sr-only"> {t('news_brief.passed')}</span>
                      </>
                    )}
                  </td>
                  <td className="nb-ev" role="cell" dir="auto">
                    {e.name}
                  </td>
                  <td className="nb-imp-cell" role="cell">
                    {e.importance !== null && <Pips n={e.importance} />}
                  </td>
                  <ValueCell v={e.forecast} label={t('news_brief.col.forecast')} cls="nb-fc" />
                  <ValueCell v={e.previous} label={t('news_brief.col.previous')} cls="nb-pv" />
                  <td className="nb-aff-cell" role="cell">
                    <span className="nb-aff">
                      {e.sensitive.map((id) => (
                        <BaseChip key={id} id={id} />
                      ))}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        ))}
      </table>
    </div>
  )
}

function Flow({ rows, lang }: { rows: FlowRow[]; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const max = Math.max(1, ...rows.map((r) => r.stories))
  return (
    <table className="nb-flow" role="table" data-testid="nb-flow">
      <caption className="sr-only">{t('news_brief.flow')}</caption>
      <thead role="rowgroup">
        <tr role="row">
          <th scope="col" role="columnheader">
            {t('news_brief.col.inst')}
          </th>
          <th scope="col" role="columnheader" className="nb-flow-count">
            {t('news_brief.col.count')}
          </th>
          <th scope="col" role="columnheader" className="is-num">
            {t('news_brief.col.senti')}
          </th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {rows.map((r) => (
          <tr role="row" key={r.instId} data-testid="nb-flow-row">
            <th scope="row" role="rowheader">
              <InstrumentId id={r.instId} />
            </th>
            <td className="nb-flow-count" role="cell">
              <span className="nb-flow-cell">
                <span className="nb-bar" aria-hidden="true">
                  <i style={{ width: `${Math.max(4, (r.stories / max) * 100).toFixed(1)}%` }} />
                </span>
                <span className="nb-bar-n num">{t('news_brief.count', { count: r.stories })}</span>
              </span>
            </td>
            <td className="is-num" role="cell" data-l={t('news.sentiment')}>
              {r.netSentiment === null ? <span aria-hidden="true">—</span> : <Senti value={r.netSentiment} lang={lang} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// -------------------------------------------------------------------------- degraded band

function Degraded({ brief, lang }: { brief: NewsBrief; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const list = useList(lang)
  return (
    <>
      {outages(brief).map(({ since, classes }) => {
        const names = list(classes.map((k) => t(`news_brief.class.${k}`, { defaultValue: k })))
        const cap = <Fig>{formatFixed(0.5, 2, lang)}</Fig>
        const time = since ? (
          <time className="num nb-time" dateTime={since} tabIndex={0} title={t('news_brief.timeTip', { baghdad: formatFullTime(since, lang), utc: formatUtcFull(since) })}>
            <Fig>{formatTime(since, lang)}</Fig>
          </time>
        ) : null
        return (
          <div className="nb-deg" key={since ?? 'never'} data-testid="nb-degraded">
            <TriangleAlert aria-hidden="true" strokeWidth={1.75} />
            <div>
              <p className="nb-deg-title">
                {time
                  ? fillSlots(t('news_brief.deg.titleSince', { classes: slot('classes'), time: slot('time') }), { classes: names, time })
                  : fillSlots(t('news_brief.deg.title', { classes: slot('classes') }), { classes: names })}
              </p>
              <p className="nb-deg-body">
                {fillSlots(t(time ? 'news_brief.deg.bodySince' : 'news_brief.deg.body', { classes: slot('classes'), cap: slot('cap') }), { classes: names, cap })}
              </p>
              <Link className="nb-deg-link" to="/settings/sources">
                {t('news_brief.deg.action')}
                <ChevronRight aria-hidden="true" strokeWidth={1.75} className="icon-dir" />
              </Link>
            </div>
          </div>
        )
      })}
    </>
  )
}

function Watch({ text, raws }: { text: string; raws: string[] }) {
  const { t } = useTranslation('warroom')
  return (
    <div className="nb-watch" data-testid="nb-watch">
      <p className="nb-watch-h">
        <Eye aria-hidden="true" strokeWidth={1.5} />
        <span>{t('news_brief.watch')}</span>
      </p>
      <UntracedScope raws={raws}>
        <Markdown text={text} className="nb-watch-t" />
      </UntracedScope>
    </div>
  )
}

// -------------------------------------------------------------------------------- the card

export function NewsBriefCard({ brief, unmatched, lang }: { brief: NewsBrief; unmatched: Unmatched[]; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const list = useList(lang)
  const now = useNow(60_000)
  const cardPaths = new Set(unmatched.filter((u) => u.location === 'card' && u.path).map((u) => u.path!))
  const mark: Mark = (path, node) => (cardPaths.has(path) ? <UntracedMark>{node}</UntracedMark> : node)
  const watchRaws = unmatched.filter((u) => u.location === 'card' && u.path === 'what_to_watch').map((u) => u.raw)
  const form = brief.form
  const formName = t(`news_brief.form.${form}`)

  let body: ReactNode
  if (form === 'morning_brief') {
    body = (
      <>
        <Degraded brief={brief} lang={lang} />
        <section className="nb-sec" data-testid="nb-top">
          <SecHead title={t('news_brief.top')} sub={t('news_brief.topSub')} />
          <Stories brief={brief} ranked limit allSources={false} empty={t('news_brief.empty.top')} lang={lang} now={now} mark={mark} />
        </section>
        <section className="nb-sec" data-testid="nb-calendar">
          <SecHead title={fillSlots(t('news_brief.cal', { n: slot('n') }), { n: hours(48) })} sub={t('news_brief.calSub')} />
          {brief.events.length === 0 ? (
            <p className="nb-empty">{fillSlots(t('news_brief.empty.events', { n: slot('n') }), { n: hours(48) })}</p>
          ) : (
            <Calendar events={brief.events} grouped caption={t('news_brief.cal', { n: 48 })} lang={lang} now={now} />
          )}
        </section>
        {brief.topInstruments.length > 0 && (
          <section className="nb-sec" data-testid="nb-flow-sec">
            <SecHead title={t('news_brief.flow')} sub={fillSlots(t('news_brief.flowSub', { n: slot('n') }), { n: hours(24) })} />
            <Flow rows={brief.topInstruments} lang={lang} />
          </section>
        )}
      </>
    )
  } else if (form === 'alert_analysis') {
    const c = brief.clusters[0]
    const names = c ? list(c.statusSources.map((k) => sourceLabel(k, c.sources))) : ''
    body = (
      <>
        <Degraded brief={brief} lang={lang} />
        {brief.whatToWatch && <Watch text={brief.whatToWatch} raws={watchRaws} />}
        <dl className="nb-facts" data-testid="nb-facts">
          {c && (
            <>
              <dt>{t('news_brief.status')}</dt>
              <dd>
                <StatusBadge c={c} lang={lang} />
                {names && <span className="nb-by">{fillSlots(t('news_brief.by', { sources: slot('s') }), { s: <bdi>{names}</bdi> })}</span>}
              </dd>
              {c.sources.length > 0 && (
                <>
                  <dt>{t('news_brief.sources')}</dt>
                  <dd>
                    <ul className="nb-src-list">
                      {c.sources.map((s, j) => (
                        <SourceLine key={j} s={s} lang={lang} now={now} />
                      ))}
                    </ul>
                  </dd>
                </>
              )}
              {c.moves.length > 0 && (
                <>
                  <dt>{t('news_brief.since')}</dt>
                  <dd>
                    <ul className="nb-insts">
                      {c.moves.map((m, j) => (
                        <InstMove key={m.instId} move={m} firstSeen={c.firstSeen} lang={lang} now={now} mark={mark} path={`clusters.0.price_move_since_first_seen_atr.${j}.value`} />
                      ))}
                    </ul>
                  </dd>
                </>
              )}
            </>
          )}
          {brief.pricedIn && (
            <>
              <dt>{t('news_brief.priced.label')}</dt>
              <dd>
                <Priced p={brief.pricedIn} />
              </dd>
            </>
          )}
          {c && c.sentiment !== null && (
            <>
              <dt>{t('news.sentiment')}</dt>
              <dd>
                <Senti value={c.sentiment} lang={lang} mark={mark} path="clusters.0.sentiment" />
              </dd>
            </>
          )}
        </dl>
      </>
    )
  } else {
    const inst = brief.instIds[0]
    body = (
      <>
        {inst && (
          <p className="nb-title" data-testid="nb-title">
            <InstrumentId id={inst} />
          </p>
        )}
        <Degraded brief={brief} lang={lang} />
        {brief.whatToWatch && <Watch text={brief.whatToWatch} raws={watchRaws} />}
        <dl className="nb-sum" data-testid="nb-sum">
          <div>
            <dt>{fillSlots(t('news_brief.sent', { n: slot('n') }), { n: hours(24) })}</dt>
            <dd>{brief.net24 === null ? '—' : <Senti value={brief.net24} lang={lang} mark={mark} path="net_sentiment_24h" />}</dd>
          </div>
          <div>
            <dt>{fillSlots(t('news_brief.sent', { n: slot('n') }), { n: hours(72) })}</dt>
            <dd>{brief.net72 === null ? '—' : <Senti value={brief.net72} lang={lang} mark={mark} path="net_sentiment_72h" />}</dd>
          </div>
          {brief.pricedIn && (
            <div>
              <dt>{t('news_brief.priced.label')}</dt>
              <dd>
                <Priced p={brief.pricedIn} />
              </dd>
            </div>
          )}
        </dl>
        <section className="nb-sec" data-testid="nb-stories">
          <SecHead title={t('news_brief.stories')} sub={fillSlots(t('news_brief.storiesSub', { n: slot('n') }), { n: hours(72) })} />
          <Stories brief={brief} ranked={false} limit={false} allSources empty={t('news_brief.empty.stories', { n: 72 })} lang={lang} now={now} mark={mark} />
        </section>
        <section className="nb-sec" data-testid="nb-calendar">
          <SecHead title={t('news_brief.upcoming')} sub={t('news_brief.calSub')} />
          {brief.events.length === 0 ? (
            <p className="nb-empty">{fillSlots(t('news_brief.empty.events', { n: slot('n') }), { n: hours(48) })}</p>
          ) : (
            <Calendar events={brief.events} grouped={false} caption={t('news_brief.upcoming')} lang={lang} now={now} />
          )}
        </section>
      </>
    )
  }

  return (
    <UntracedScope raws={[]}>
      <div className={`nb-card nb-${form}`} role="group" aria-label={formName} data-testid="nb-card" data-form={form}>
        <div className="nb-head">
          <span className="nb-tile">
            <Newspaper aria-hidden="true" strokeWidth={1.5} />
          </span>
          <span className="nb-kind">{formName}</span>
          <AsOf ts={brief.dataAsOf} lang={lang} />
        </div>
        {body}
        {unmatched.length > 0 && (
          <p className="hv-unv-note" data-testid="nb-unv-note">
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{t('hv.unvNote', { count: unmatched.length })}</span>
          </p>
        )}
      </div>
    </UntracedScope>
  )
}
