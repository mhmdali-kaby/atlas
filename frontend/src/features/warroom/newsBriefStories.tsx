import { Check, CheckCheck, ChevronDown, ChevronUp, CircleQuestionMark, ExternalLink } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDecimal, formatList, type Lang } from '../../lib/format'
import { Fig } from './historianParts'
import { sourceLabel, type NewsBrief, type NewsCluster, type NewsSource, type PriceMove } from './newsBrief'
import { ImportancePips, SignedSentiment } from './newsParts'
import { fillSlots, slot } from './slots'
import { InstrumentId } from './text'
import { baghdadDay, formatDateTime, formatFullTime, formatTime, formatUtcFull, formatUtcTime } from './time'

/*
 * The story list of NewsBriefCard (design v9 "Watchlist and news briefs" §B): each cluster with its
 * code-decided status badge (icon + word), importance, instruments with their ATR move since first
 * seen, sentiment and sources (name, Baghdad time with UTC, http(s) link in a new tab). Headlines and
 * source names are untrusted text: plain React text with dir="auto".
 */

const STORY_LIMIT = 5

/** Decorates a figure at a card path when the number guard did not trace it. */
export type Mark = (path: string, node: ReactNode) => ReactNode

const STATUS_ICON = { CONFIRMED: CheckCheck, REPORTED: Check, RUMOUR: CircleQuestionMark } as const

export function StatusBadge({ c, lang }: { c: NewsCluster; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const names = formatList(c.statusSources.map((k) => sourceLabel(k, c.sources)), lang)
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

/** Importance 1…5 with its spoken label, for story rows and calendar rows. */
export function BriefImportance({ n }: { n: number }) {
  const { t } = useTranslation('warroom')
  const label = t('news.impOf', { n })
  return (
    <span className={`nb-imp imp-${n}`} role="img" aria-label={label} title={label} data-testid="nb-imp">
      <ImportancePips n={n} />
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

export function SourceLine({ s, lang, now, tail }: { s: NewsSource; lang: Lang; now: number; tail?: ReactNode }) {
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
export function InstMove({ move, firstSeen, lang, now, mark, path }: { move: PriceMove; firstSeen: string | null; lang: Lang; now: number; mark: Mark; path: string }) {
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
          <BriefImportance n={c.importance} />
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
                <SignedSentiment value={c.sentiment} lang={lang} wrap={(n) => mark(`clusters.${i}.sentiment`, n)} />
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

export function Stories({ brief, ranked, limit, allSources, empty, lang, now, mark }: { brief: NewsBrief; ranked: boolean; limit: boolean; allSources: boolean; empty: string; lang: Lang; now: number; mark: Mark }) {
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
