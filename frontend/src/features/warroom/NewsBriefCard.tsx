import { ChevronRight, CircleAlert, Eye, Newspaper, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { formatFixed, formatList, type Lang } from '../../lib/format'
import { useNow } from '../portfolio/hooks'
import type { Unmatched } from './historian'
import { AsOf, Fig } from './historianParts'
import { outages, sourceLabel, type NewsBrief } from './newsBrief'
import { InstMove, SourceLine, StatusBadge, Stories, type Mark } from './newsBriefStories'
import { Calendar, Flow } from './newsBriefTables'
import { SignedSentiment } from './newsParts'
import { fillSlots, slot } from './slots'
import { InstrumentId, Markdown } from './text'
import { formatFullTime, formatTime, formatUtcFull } from './time'
import { UntracedMark, UntracedScope } from './Untraced'
import './newsBrief.css'

/*
 * NewsBriefCard (design v9 "Watchlist and news briefs" §B): News Intelligence's research_report
 * with a news_brief card, in three forms — the 07:30 morning brief, an instrument brief answering
 * @News, and an alert analysis replying under an ingest news_alert. Headlines, source names and
 * event names are untrusted internet text (brief rule 7): plain React text with dir="auto", never
 * markdown or HTML. what_to_watch is the agent's own words and goes through the War Room tokenizer.
 * Status is decided by code (ATL-106) and always shown as an icon plus its word.
 *
 * Stories live in newsBriefStories.tsx, the calendar and news-flow tables in newsBriefTables.tsx.
 */

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

// -------------------------------------------------------------------------- degraded band

function Degraded({ brief, lang }: { brief: NewsBrief; lang: Lang }) {
  const { t } = useTranslation('warroom')
  return (
    <>
      {outages(brief).map(({ since, classes }) => {
        const names = formatList(classes.map((k) => t(`news_brief.class.${k}`, { defaultValue: k })), lang)
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
    const names = c ? formatList(c.statusSources.map((k) => sourceLabel(k, c.sources)), lang) : ''
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
                <SignedSentiment value={c.sentiment} lang={lang} wrap={(n) => mark('clusters.0.sentiment', n)} />
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
            <dd>{brief.net24 === null ? '—' : <SignedSentiment value={brief.net24} lang={lang} wrap={(n) => mark('net_sentiment_24h', n)} />}</dd>
          </div>
          <div>
            <dt>{fillSlots(t('news_brief.sent', { n: slot('n') }), { n: hours(72) })}</dt>
            <dd>{brief.net72 === null ? '—' : <SignedSentiment value={brief.net72} lang={lang} wrap={(n) => mark('net_sentiment_72h', n)} />}</dd>
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
