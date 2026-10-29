import { ArrowDownRight, ArrowUpRight, ExternalLink, Newspaper } from 'lucide-react'
import { Fragment, type ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { formatDecimal, formatFixed, type Lang } from '../../lib/format'
import type { WarroomMessage } from './api'
import { formatFullTime, formatTime } from './time'

/*
 * NewsAlertCard (design v6 "Data sources and news alerts" §5.8): a `news_alert` message from
 * ingest (ATL-65), author system:news. Its fields are the news.alert payload verbatim
 * (docs/handoff/ATL-65-news-alerts.md), and every text field is untrusted internet text (brief
 * rule 7): the headline and source render as plain React text, never markdown or HTML, and the
 * only link is the article URL, kept only when it is http(s).
 */

export interface NewsAlert {
  headline: string
  sourceName: string
  url: URL | null
  publishedAt: string | null
  importance: number
  eventType: string | null
  instIds: string[]
  /** −1…1, or null when the item was not scored (or the value is not a finite number). */
  sentiment: number | null
}

const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)

function httpUrl(v: unknown): URL | null {
  const s = str(v)
  if (!s) return null
  try {
    const u = new URL(s)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u : null
  } catch {
    return null
  }
}

/** The card's fields, or null when the message carries no headline (GenericCard draws it then). */
export function readNewsAlert(card: WarroomMessage['card']): NewsAlert | null {
  if (!card) return null
  const headline = str(card.headline)
  if (!headline) return null
  const imp = typeof card.importance === 'number' && Number.isFinite(card.importance) ? Math.round(card.importance) : 1
  const sentiment = typeof card.sentiment === 'number' && Number.isFinite(card.sentiment) ? Math.max(-1, Math.min(1, card.sentiment)) : null
  const ids = Array.isArray(card.inst_ids) ? card.inst_ids : []
  const published = str(card.published_at)
  return {
    headline,
    sourceName: str(card.source_name) ?? str(card.source) ?? '',
    url: httpUrl(card.url),
    publishedAt: published && !Number.isNaN(Date.parse(published)) ? published : null,
    importance: Math.max(1, Math.min(5, imp)),
    eventType: str(card.event_type),
    instIds: [...new Set(ids.filter((id): id is string => typeof id === 'string' && id.trim() !== ''))],
    sentiment,
  }
}

/*
 * Untrusted values never go through <Trans>, which parses its interpolated string for tags: a
 * source name like "<strong>x</strong>" would become markup. The template is translated with
 * private-use slot markers and split around them; the values are placed as React children.
 */
const SLOT = { source: '\uE000source\uE000', time: '\uE000time\uE000', host: '\uE000host\uE000' } as const

function fill(template: string, nodes: Record<string, ReactNode>): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\uE000[a-z]+\uE000/g
  let last = 0
  for (const m of template.matchAll(re)) {
    if (m.index > last) out.push(<Fragment key={`t${last}`}>{template.slice(last, m.index)}</Fragment>)
    out.push(nodes[m[0]] ?? m[0])
    last = m.index + m[0].length
  }
  if (last < template.length) out.push(<Fragment key={`t${last}`}>{template.slice(last)}</Fragment>)
  return out
}

/** A family ("TSLA-USDT") or instId, base in 600 and the rest secondary, as an LTR island. */
function InstChip({ id }: { id: string }) {
  const cut = id.indexOf('-')
  return (
    <li className="news-inst">
      <bdi dir="ltr" className="inst-tok">
        <span className="inst-base">{cut < 0 ? id : id.slice(0, cut)}</span>
        {cut >= 0 && <span className="inst-rest">{id.slice(cut)}</span>}
      </bdi>
    </li>
  )
}

/** Sentiment as a signed value: sign + arrow + a spoken word, never colour alone; |s| < 0.05 is neutral. */
function Sentiment({ value, lang }: { value: number; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const tone = value >= 0.05 ? 'profit' : value <= -0.05 ? 'loss' : 'zero'
  const text = tone === 'zero' ? formatFixed(0, 2, lang) : formatDecimal(value.toFixed(2), lang, 2, 'exceptZero')
  const Icon = tone === 'profit' ? ArrowUpRight : ArrowDownRight
  return (
    <span className="news-senti" data-testid="news-sentiment">
      <span>{t('news.sentiment')}</span>
      <span className={`signed is-${tone}`} data-sign={tone}>
        {tone !== 'zero' && <Icon aria-hidden="true" strokeWidth={2.25} className="icon-dir" />}
        <bdi dir="ltr" className="num fig">
          {text}
        </bdi>
        <span className="sr-only"> {t(tone === 'profit' ? 'news.positive' : tone === 'loss' ? 'news.negative' : 'news.neutral')}</span>
      </span>
    </span>
  )
}

export function NewsAlertCard({ alert: a, lang }: { alert: NewsAlert; lang: Lang }) {
  const { t } = useTranslation('warroom')
  const type = a.eventType ? t(`news.type.${a.eventType}`, { defaultValue: '' }) : ''
  const host = a.url ? a.url.hostname.replace(/^www\./, '') : ''
  const hasMeta = a.instIds.length > 0 || a.sentiment !== null
  return (
    <div className={`news-card imp-${a.importance}`} data-testid="news-card" data-importance={a.importance}>
      <div className="news-head">
        <span className="news-tile">
          <Newspaper aria-hidden="true" strokeWidth={1.5} />
        </span>
        <span className="news-kind">{t('kind.news_alert')}</span>
        <span className="news-imp" role="img" aria-label={t('news.impOf', { n: a.importance })}>
          <span className="news-pips" aria-hidden="true">
            {[1, 2, 3, 4, 5].map((n) => (
              <i key={n} className={n <= a.importance ? 'is-on' : undefined} style={{ height: 2 + n * 2 }} />
            ))}
          </span>
          <span aria-hidden="true">
            <Trans t={t} i18nKey="news.imp" values={{ n: a.importance }} components={{ n: <span className="num" /> }} />
          </span>
        </span>
        {type && (
          <span className="news-type" data-testid="news-type">
            {type}
          </span>
        )}
      </div>
      <p className="news-headline" dir="auto" data-testid="news-headline">
        {a.headline}
      </p>
      {hasMeta && (
        <div className="news-meta">
          {a.instIds.length > 0 && (
            <ul className="news-insts" aria-label={t('news.instruments')}>
              {a.instIds.map((id) => (
                <InstChip key={id} id={id} />
              ))}
            </ul>
          )}
          {a.sentiment !== null && <Sentiment value={a.sentiment} lang={lang} />}
        </div>
      )}
      <div className="news-foot">
        <span data-testid="news-by">
          {a.publishedAt ? (
            fill(t('news.by', { source: SLOT.source, time: SLOT.time }), {
              [SLOT.source]: <bdi key="s">{a.sourceName}</bdi>,
              [SLOT.time]: (
                <time key="t" className="num" dateTime={a.publishedAt} title={formatFullTime(a.publishedAt, lang)}>
                  {formatTime(a.publishedAt, lang)}
                </time>
              ),
            })
          ) : (
            <bdi>{a.sourceName}</bdi>
          )}
        </span>
        {a.url && host && (
          <a className="news-link" href={a.url.href} target="_blank" rel="noopener noreferrer" data-testid="news-link">
            <span>
              {fill(t('news.read', { host: SLOT.host }), {
                [SLOT.host]: (
                  <bdi key="h" dir="ltr">
                    {host}
                  </bdi>
                ),
              })}
            </span>
            <ExternalLink aria-hidden="true" strokeWidth={1.5} className="icon-dir" />
          </a>
        )}
      </div>
    </div>
  )
}
