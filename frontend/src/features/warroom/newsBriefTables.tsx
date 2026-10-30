import { Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { Fig } from './historianParts'
import type { FlowRow, NewsEvent } from './newsBrief'
import { BriefImportance } from './newsBriefStories'
import { SignedSentiment } from './newsParts'
import { InstrumentId } from './text'
import { baghdadDay, formatDateTime, formatDayShort, formatFullTime, formatTime, formatUtcFull } from './time'

/*
 * The tables of NewsBriefCard (design v9 §B): the event calendar (grouped by Baghdad day in the
 * morning brief, flat in an instrument brief) and the morning brief's news flow by instrument.
 * Event names are untrusted text: plain React text with dir="auto".
 */

const DAY_MS = 86_400_000

function BaseChip({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <span className="nb-base" title={id}>
      <bdi dir="ltr">{cut > 0 ? id.slice(0, cut) : id}</bdi>
      {cut > 0 && <span className="sr-only">{id.slice(cut)}</span>}
    </span>
  )
}

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

export function Calendar({ events, grouped, caption, lang, now }: { events: NewsEvent[]; grouped: boolean; caption: string; lang: Lang; now: number }) {
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
                    {e.importance !== null && <BriefImportance n={e.importance} />}
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

export function Flow({ rows, lang }: { rows: FlowRow[]; lang: Lang }) {
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
              {r.netSentiment === null ? <span aria-hidden="true">—</span> : <SignedSentiment value={r.netSentiment} lang={lang} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
