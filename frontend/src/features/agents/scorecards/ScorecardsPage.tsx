import { CalendarCheck, CloudOff, Gauge, Info, RefreshCw, TriangleAlert, UsersRound } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useState, type CSSProperties } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { useTradingEnv } from '../../../app/shell/tradingEnv'
import { Button, SegmentedControl, Skeleton } from '../../../ui'
import { useLang } from '../../portfolio/hooks'
import { formatDay } from '../../portfolio/time'
import { Medallion } from '../../warroom/Medallion'
import { Fig, Tip } from '../parts'
import { SCORE_DAYS, useAgentScores, type AgentScores, type ScoreDays, type ScoresReport } from './api'
import { fmtWeight, weightDetail } from './format'
import { CARDS, STALE_DAYS, VOTERS, daysBetween, firstScored, scoredThrough, weightOf, weightX } from './model'
import { ScorecardCard } from './ScorecardCard'
import { SC_NS } from './i18n'
import '../../settings-connections/settings.css'
import '../../warroom/warroom.css'
import '../agents.css'
import './scorecards.css'

/*
 * Agents → Scorecards (ATL-201; brief §9.3, §13, §15; design v16 §5). How well each agent's calls
 * turned out over 7, 30 or 90 days, read from ATL-200's GET /api/agents/scores?days=N: the vote
 * weights of the five voters, then nine cards in the order work flows through the team. Changing
 * the period refetches; the cards dim while it loads. With no scores, or when they fail to load,
 * the head shows only its title and intro.
 */

const dayText = (day: string, lang: 'en' | 'ar') => formatDay(`${day}T12:00:00Z`, lang)

function Head({ days, onDays, report, bare, loading, portfolio }: {
  days: ScoreDays
  onDays: (d: ScoreDays) => void
  report?: ScoresReport
  bare?: boolean
  loading?: boolean
  portfolio?: boolean
}) {
  const { t } = useTranslation(SC_NS)
  const lang = useLang()
  const through = report ? scoredThrough(report.data) : null
  const stale = report && through ? daysBetween(through, report.meta.to) >= STALE_DAYS : false
  return (
    <header className="sc-head">
      <div className="sc-head-text">
        <h2 className="text-title-1 font-semibold">{t('title')}</h2>
        <p className="text-subheadline text-label-secondary">{t(portfolio ? 'pfIntro' : 'intro')}</p>
      </div>
      {!bare && (
        <div className="sc-head-aside">
          {loading ? (
            <Skeleton style={{ width: 240, height: 32, borderRadius: 9 }} />
          ) : (
            <SegmentedControl<string>
              aria-label={t('range')}
              value={String(days)}
              onValueChange={(v) => onDays(Number(v) as ScoreDays)}
              options={SCORE_DAYS.map((d) => ({ value: String(d), label: t(`d${d}`) }))}
            />
          )}
          {!loading && through && (
            <Tip label={t('throughTip')}>
              <p className={stale ? 'sc-through is-stale' : 'sc-through'} tabIndex={0} data-testid="sc-through">
                {stale ? <TriangleAlert className="icon" aria-hidden="true" /> : <CalendarCheck className="icon" aria-hidden="true" />}
                <span>
                  <Trans t={t} i18nKey="through" values={{ date: dayText(through, lang) }} components={{ f: <span className="num" /> }} />
                </span>
              </p>
            </Tip>
          )}
        </div>
      )}
    </header>
  )
}

function Weights({ agents }: { agents: AgentScores[] }) {
  const { t } = useTranslation([SC_NS, 'warroom'])
  const lang = useLang()
  const by = new Map(agents.map((a) => [a.agent, a]))
  return (
    <section className="sc-panel sc-weights" aria-labelledby="sc-w-title" data-testid="sc-weights">
      <div className="sc-w-head">
        <h2 className="text-headline font-semibold" id="sc-w-title">
          {t('w.title')}
        </h2>
        <p className="sc-w-sub">{t('w.sub')}</p>
      </div>
      <div className="sc-w-scale" aria-hidden="true">
        <span />
        <span className="sc-w-axis" dir="ltr">
          <span style={{ '--x': 0 } as CSSProperties}>
            <Fig>0.5×</Fig>
          </span>
          <span style={{ '--x': 50 } as CSSProperties}>
            <Fig>1.0×</Fig>
          </span>
          <span style={{ '--x': 100 } as CSSProperties}>
            <Fig>1.5×</Fig>
          </span>
        </span>
        <span />
        <span />
      </div>
      <ul className="sc-w-list">
        {VOTERS.map((slug) => {
          const w = weightOf(by.get(slug))
          const detail = weightDetail(w, t, lang)
          const x = weightX(w.weight)
          const name = t(`warroom:agents.${slug}.short`, { defaultValue: t(`warroom:agents.${slug}.name`) })
          return (
            <li
              key={slug}
              className={w.few ? 'sc-w-row is-few' : 'sc-w-row'}
              aria-label={t('w.aria', { name: t(`warroom:agents.${slug}.name`), w: fmtWeight(w.weight, lang), detail })}
              data-testid="sc-w-row"
              data-slug={slug}
            >
              <span className="sc-w-who">
                <Medallion slug={slug} size={28} status="idle" />
                <span className="sc-w-name">{name}</span>
              </span>
              <span className="sc-w-plot" dir="ltr" aria-hidden="true">
                <i
                  className={w.weight >= 1 ? 'sc-w-bar is-more' : 'sc-w-bar is-less'}
                  style={{ '--a': Math.min(50, x).toFixed(1), '--b': Math.max(50, x).toFixed(1) } as CSSProperties}
                />
                <i className="sc-w-dot" style={{ '--x': x.toFixed(1) } as CSSProperties} />
              </span>
              <span className="sc-w-val">
                <Fig>{fmtWeight(w.weight, lang)}</Fig>
              </span>
              <span className="sc-w-detail">{detail}</span>
            </li>
          )
        })}
      </ul>
      <p className="sc-w-foot">
        <Info className="icon" aria-hidden="true" />
        <span>{t('w.always30')}</span>
      </p>
    </section>
  )
}

function Loading({ portfolio }: { portfolio?: boolean }) {
  const { t } = useTranslation(SC_NS)
  return (
    <div className="sc-page" aria-busy="true" aria-label={t('loading')} data-testid="sc-loading">
      <Head days={30} onDays={() => undefined} loading portfolio={portfolio} />
      {!portfolio && (
        <div className="sc-panel sc-weights sc-skel-weights" aria-hidden="true">
          <Skeleton style={{ width: 220, height: 16 }} />
          <Skeleton style={{ width: '60%', height: 12 }} />
          {VOTERS.map((v) => (
            <Skeleton key={v} style={{ width: '100%', height: 28 }} />
          ))}
        </div>
      )}
      <div className="sc-panel sc-grid" aria-hidden="true">
        {CARDS.map((c) => (
          <div className="sc-card is-skel" key={c.slug}>
            <div className="sc-card-head">
              <Skeleton style={{ width: 36, height: 36, borderRadius: '50%' }} />
              <span className="grid gap-1.5">
                <Skeleton style={{ width: 120, height: 14 }} />
                <Skeleton style={{ width: 170, height: 11 }} />
              </span>
            </div>
            <Skeleton style={{ width: 110, height: 11 }} />
            <Skeleton style={{ width: 90, height: 28 }} />
            <Skeleton style={{ width: '100%', height: 56, borderRadius: 8 }} />
            <Skeleton style={{ width: '100%', height: 12 }} />
            <Skeleton style={{ width: '80%', height: 12 }} />
          </div>
        ))}
      </div>
    </div>
  )
}

function State({ kind, onRetry, retrying }: { kind: 'error' | 'empty'; onRetry: () => void; retrying: boolean }) {
  const { t } = useTranslation(SC_NS)
  const key = kind === 'error' ? 'err' : 'empty'
  return (
    <section className="ag-group st-empty" role={kind === 'error' ? 'alert' : undefined} data-testid={`sc-${kind}`}>
      <span className={kind === 'error' ? 'st-empty-icon is-error' : 'st-empty-icon'} aria-hidden="true">
        {kind === 'error' ? <CloudOff strokeWidth={1.5} /> : <Gauge strokeWidth={1.5} />}
      </span>
      <h2 className="text-title-3">{t(`${key}.title`)}</h2>
      <p className="max-w-[52ch] text-subheadline text-label-secondary">
        <Trans t={t} i18nKey={`${key}.body`} components={{ cmd: <code className="cmd" dir="ltr" /> }} />
      </p>
      {kind === 'error' ? (
        <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry} loading={retrying}>
          {t('retry')}
        </Button>
      ) : (
        <Button variant="secondary" className="mt-2" asChild>
          <Link to="/agents" data-testid="sc-see-team">
            <UsersRound className="icon" aria-hidden="true" />
            <span>{t('empty.cta')}</span>
          </Link>
        </Button>
      )}
    </section>
  )
}

/** The scorecards page body. `portfolio` is ATL-204's variant: no weights panel, P&L on each card. */
export function ScorecardsView({ portfolio = false }: { portfolio?: boolean }) {
  const { t } = useTranslation(SC_NS)
  const lang = useLang()
  const env = useTradingEnv()
  const [days, setDays] = useState<ScoreDays>(30)
  const q = useAgentScores(env, days)
  const report = q.data
  const refetching = q.isFetching && q.isPlaceholderData
  // Announced politely once the chosen period's figures are on screen.
  const announce = report && !q.isPlaceholderData ? t('periodSr', { period: t(`d${report.meta.days}`) }) : ''

  if (q.isPending) return <Loading portfolio={portfolio} />
  if (q.isError || !report) {
    return (
      <div className="sc-page">
        <Head days={days} onDays={setDays} bare portfolio={portfolio} />
        <State kind="error" onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </div>
    )
  }
  const agents = report.data
  const through = scoredThrough(agents)
  if (!through) {
    return (
      <div className="sc-page">
        <Head days={days} onDays={setDays} bare portfolio={portfolio} />
        <State kind="empty" onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </div>
    )
  }
  const by = new Map(agents.map((a) => [a.agent, a]))
  const first = firstScored(agents)
  const have = first ? daysBetween(first, through) + 1 : 0
  const shorter = first !== null && first > report.meta.from
  const staleN = daysBetween(through, report.meta.to)
  const stale = staleN >= STALE_DAYS

  return (
    <Tooltip.Provider delayDuration={450}>
      <div className="sc-page" data-testid="sc-page">
        <Head days={days} onDays={setDays} report={report} portfolio={portfolio} />
        {stale && (
          <div className="sc-stale" role="status" data-testid="sc-stale">
            <TriangleAlert className="icon" aria-hidden="true" />
            <div>
              <p className="sc-stale-title">{t('staleTitle', { n: staleN })}</p>
              <p className="sc-stale-body">
                <Trans t={t} i18nKey="stale" values={{ date: dayText(through, lang) }} components={{ f: <span className="num" /> }} />
              </p>
            </div>
          </div>
        )}
        {shorter && (
          <p className="sc-note" data-testid="sc-shorter">
            <Info className="icon" aria-hidden="true" />
            <span>{t('shorter', { n: have, period: t(`d${report.meta.days}`) })}</span>
          </p>
        )}
        {!portfolio && <Weights agents={agents} />}
        <section
          className={refetching ? 'sc-panel sc-grid is-refetching' : 'sc-panel sc-grid'}
          aria-label={t('cards')}
          aria-busy={refetching}
          data-testid="sc-grid"
        >
          {CARDS.map((c) => (
            <ScorecardCard
              key={c.slug}
              slug={c.slug}
              scores={by.get(c.slug)}
              days={report.meta.days}
              from={report.meta.from}
              to={through}
              weight={!portfolio && VOTERS.includes(c.slug) ? weightOf(by.get(c.slug)) : null}
              pnl={portfolio}
            />
          ))}
        </section>
        <div className="sr-only" aria-live="polite" data-testid="sc-live">
          {announce}
        </div>
      </div>
    </Tooltip.Provider>
  )
}

export function ScorecardsPage() {
  return <ScorecardsView />
}
