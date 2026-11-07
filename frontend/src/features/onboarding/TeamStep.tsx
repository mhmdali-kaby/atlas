import { CircleAlert, CirclePause, Clock, Earth, Info, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { useOwnerZone } from '../../lib/ownerZone'
import { diffFromTeam } from '../../lib/zones'
import { Button, Skeleton } from '../../ui'
import { cx } from '../../ui/cx'
import { useAgents, type AgentRow } from '../agents/api'
import { useLang, useNow } from '../portfolio/hooks'
import { DayLine } from '../settings-general/DayLine'
import { clock, hm, scheduleFrom, type ScheduleValues } from '../settings-general/dayModel'
import { Medallion } from '../warroom/Medallion'
import { agentBySlug } from '../warroom/agents'
import { Notice } from './Notice'
import { useSettings } from './settings'
import { StepHead, StepMain, type StepProps } from './steps'
import '../warroom/warroom.css'
import './onboarding-n.css'

/*
 * Meet your team (design v21 onboarding.js team()): one card per agent GET /api/agents returns, in
 * the order work flows through the team, each with its medallion (glyph in its accent ring), name,
 * role line and when it works; an agent the owner turned off keeps its card, greyed, with "Off" in
 * words. Below, the team's day on the shared DayLine in the owner's zone. A failed load never
 * blocks finishing.
 */

/** The order work flows through the team (design FLOW). Agents the API adds later come after. */
export const FLOW = [
  'orchestrator',
  'news_intelligence',
  'historian',
  'market_scout',
  'technical_analyst',
  'derivatives_sentiment',
  'strategist',
  'devils_advocate',
  'portfolio_manager',
] as const

export function inFlowOrder(rows: AgentRow[]): AgentRow[] {
  const rank = (slug: string) => {
    const i = (FLOW as readonly string[]).indexOf(slug)
    return i < 0 ? FLOW.length : i
  }
  return [...rows].sort((a, b) => rank(a.slug) - rank(b.slug))
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const ISLAND = { f: <bdi dir="ltr" className="fig" /> }

export function TeamStep({ autoFocus }: StepProps) {
  const { t } = useTranslation('onboarding')
  const lang = useLang()
  const now = useNow(60_000)
  const owner = useOwnerZone()
  const agents = useAgents('demo')
  const settings = useSettings('demo')
  const raw = settings.data?.data.settings as Record<string, unknown> | undefined
  const schedule = scheduleFrom(raw)
  const weekly = typeof raw?.['schedule.historian_weekly_review'] === 'string' ? (raw['schedule.historian_weekly_review'] as string) : 'Sun 18:00'
  const diff = diffFromTeam(owner, now)
  const rows = agents.data ? inFlowOrder(agents.data.data) : null
  const offCount = rows?.filter((a) => !a.enabled).length ?? 0

  let list: ReactNode
  if (agents.isPending) {
    list = (
      <ul className="ob-agents" aria-busy="true" aria-label={t('team.loading')} data-testid="team-loading">
        {FLOW.map((slug) => (
          <li key={slug} className="ob-agent is-loading" aria-hidden="true">
            <Skeleton className="ob-agent-sk-medal" />
            <span className="ob-agent-text">
              <Skeleton style={{ width: '62%', height: 14 }} />
              <Skeleton style={{ width: '88%', height: 11, marginTop: 6 }} />
              <Skeleton style={{ width: '70%', height: 11, marginTop: 10 }} />
            </span>
          </li>
        ))}
      </ul>
    )
  } else if (!rows) {
    list = (
      <Notice
        tone="warning"
        role="alert"
        icon={CircleAlert}
        title={t('team.errTitle')}
        className="ob-team-err"
        data-testid="team-error"
        actions={
          <Button size="sm" variant="secondary" onClick={() => void agents.refetch()} loading={agents.isFetching} icon={agents.isFetching ? undefined : <RefreshCw />}>
            {t('team.retry')}
          </Button>
        }
      >
        {t('team.errText')}
      </Notice>
    )
  } else {
    list = (
      <>
        <ul className="ob-agents" data-testid="team-list">
          {rows.map((a) => (
            <AgentCard key={a.slug} agent={a} when={<When slug={a.slug} v={schedule} weekly={weekly} diff={diff} lang={lang} />} />
          ))}
        </ul>
        {offCount > 0 && (
          <p className="ob-team-note" data-testid="team-off-note">
            <Info aria-hidden="true" strokeWidth={1.5} />
            <span>{t('team.offNote', { count: offCount })}</span>
          </p>
        )}
      </>
    )
  }

  return (
    <>
      <StepHead autoFocus={autoFocus} title={t('team.title')} lead={t('team.lead')} />
      <StepMain>
        <section aria-labelledby="ob-team-title" className="grid">
          <h3 className="px-4 pb-2 text-footnote font-semibold text-label-secondary" id="ob-team-title">
            {t('team.team')}
          </h3>
          {list}
        </section>
        <section aria-labelledby="ob-day-title" className="grid">
          <h3 className="px-4 pb-2 text-footnote font-semibold text-label-secondary" id="ob-day-title">
            {t('team.day')}
          </h3>
          <div className="ob-day" data-testid="team-day">
            <DayLine values={schedule} frame="owner" now={now} lang={lang} />
            <p className="ob-day-note">
              <Earth aria-hidden="true" strokeWidth={1.5} />
              <span>{t('team.dayNote')}</span>
            </p>
          </div>
        </section>
      </StepMain>
    </>
  )
}

function AgentCard({ agent, when }: { agent: AgentRow; when: ReactNode }) {
  const { t } = useTranslation(['onboarding', 'warroom'])
  const off = !agent.enabled
  const known = !!agentBySlug(agent.slug)
  const name = known ? t(`warroom:agents.${agent.slug}.name`) : agent.name
  const role = t(`onboarding:team.role.${agent.slug}`, { defaultValue: '' })
  return (
    <li className={cx('ob-agent', off && 'is-off')} data-testid="team-agent" data-slug={agent.slug} data-enabled={agent.enabled}>
      <Medallion slug={agent.slug} size={40} status={off ? 'offline' : 'idle'} />
      <span className="ob-agent-text">
        <span className="ob-agent-name">
          {name}
          {off && <span className="ob-agent-off">{t('onboarding:team.off')}</span>}
        </span>
        {role && <span className="ob-agent-role">{role}</span>}
        {off ? (
          <span className="ob-agent-when is-off">
            <CirclePause aria-hidden="true" strokeWidth={1.5} />
            <span>{t('onboarding:team.offWhen')}</span>
          </span>
        ) : (
          known && (
            <span className="ob-agent-when">
              <Clock aria-hidden="true" strokeWidth={1.5} />
              <span>
                <span className="sr-only">{t('onboarding:team.whenSr')} </span>
                {when}
              </span>
            </span>
          )
        )}
      </span>
    </li>
  )
}

/** When an agent works, with every schedule time moved into the owner's zone (as LTR islands). */
function When({ slug, v, weekly, diff, lang }: { slug: string; v: ScheduleValues; weekly: string; diff: number; lang: string }) {
  const { t } = useTranslation('onboarding')
  const at = (s: string) => clock(hm(s) + diff)
  const key = `team.when.${slug === 'technical_analyst' || slug === 'derivatives_sentiment' ? 'cycle' : slug}`
  let values: Record<string, string | number> = {}
  if (slug === 'news_intelligence') values = { t: at(v.news_morning_brief) }
  if (slug === 'market_scout') values = { t: at(v.scout_watchlist) }
  if (slug === 'portfolio_manager') values = { t: at(v.pm_daily_report) }
  if (slug === 'technical_analyst' || slug === 'derivatives_sentiment') {
    values = { n: v.analysis_interval_minutes, a: at(v.analysis_first_slot), b: at(v.analysis_last_slot) }
  }
  if (slug === 'historian') {
    const [day = 'Sun', time = '18:00'] = weekly.split(' ')
    let m = hm(time) + diff
    let d = Math.max(0, WEEKDAYS.indexOf(day))
    if (m >= 1440) {
      m -= 1440
      d = (d + 1) % 7
    } else if (m < 0) {
      m += 1440
      d = (d + 6) % 7
    }
    // 1 Nov 2026 is a Sunday, so day d of that week names the weekday in the owner's language.
    const dayName = new Intl.DateTimeFormat(lang === 'ar' ? 'ar' : 'en-GB', { weekday: 'long', timeZone: 'UTC' }).format(
      new Date(Date.UTC(2026, 10, 1 + d)),
    )
    values = { t: at(v.historian_regime_table), day: dayName, w: clock(m) }
  }
  return <Trans t={t} i18nKey={key} values={values} components={ISLAND} />
}
