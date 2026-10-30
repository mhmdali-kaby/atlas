import { ExternalLink, Newspaper } from 'lucide-react'
import { Trans, useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import type { NewsAlert } from './newsAlert'
import { ImportancePips, SignedSentiment } from './newsParts'
import { fillSlots, slot } from './slots'
import { InstrumentId } from './text'
import { formatFullTime, formatTime } from './time'

/*
 * NewsAlertCard (design v6 "Data sources and news alerts" §5.8): a `news_alert` message from
 * ingest (ATL-65), author system:news. Its fields are the news.alert payload verbatim
 * (docs/handoff/ATL-65-news-alerts.md), and every text field is untrusted internet text (brief
 * rule 7): the headline and source render as plain React text, never markdown or HTML, and the
 * only link is the article URL, kept only when it is http(s).
 */

/** The sentiment label and its signed value. */
function Sentiment({ value, lang }: { value: number; lang: Lang }) {
  const { t } = useTranslation('warroom')
  return (
    <span className="news-senti" data-testid="news-sentiment">
      <span>{t('news.sentiment')}</span>
      <SignedSentiment value={value} lang={lang} />
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
          <ImportancePips n={a.importance} />
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
                <li key={id} className="news-inst">
                  <InstrumentId id={id} />
                </li>
              ))}
            </ul>
          )}
          {a.sentiment !== null && <Sentiment value={a.sentiment} lang={lang} />}
        </div>
      )}
      <div className="news-foot">
        <span data-testid="news-by">
          {a.publishedAt ? (
            fillSlots(t('news.by', { source: slot('source'), time: slot('time') }), {
              source: <bdi key="s">{a.sourceName}</bdi>,
              time: (
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
              {fillSlots(t('news.read', { host: slot('host') }), {
                host: (
                  <bdi key="h" dir="ltr">
                    {host}
                  </bdi>
                ),
              })}
            </span>
            <ExternalLink aria-hidden="true" strokeWidth={1.5} />
          </a>
        )}
      </div>
    </div>
  )
}
