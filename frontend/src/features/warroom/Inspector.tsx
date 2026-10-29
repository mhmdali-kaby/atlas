import { ChartCandlestick, Lock, UsersRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Skeleton } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import { agentBySlug, agentStatus } from './agents'
import type { WarroomThread } from './api'
import { Medallion } from './Medallion'
import { InstrumentId } from './text'
import { around } from './tokens'
import { dayOrTime, formatDate } from './time'

/*
 * The inspector (design v4 War Room §7, ATL-39 part): the selected thread's head, the price chart's
 * empty state in instrument threads, the agent's facts in a private thread, and the thread facts.
 * Open positions (ATL-43) slot in above the thread facts.
 */
export function Inspector({
  thread,
  threadName,
  lastMessageAt,
  loading,
}: {
  thread: WarroomThread | undefined
  threadName: string
  /** The newest loaded message's time, so the facts agree with the feed; null when empty. */
  lastMessageAt: string | null
  loading: boolean
}) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const now = useNow(60_000)
  const none = (
    <>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{t('none')}</span>
    </>
  )

  let head = null
  let body = null
  if (thread?.kind === 'instrument') {
    const [before, after] = around(t('chartEmpty.bodyBefore') + ' \u0000 ' + t('chartEmpty.bodyAfter'))
    head = (
      <>
        <h2 className="text-title-3 insp-name">
          <InstrumentId id={thread.key} />
        </h2>
        <p className="insp-kind">
          <ChartCandlestick aria-hidden="true" strokeWidth={1.5} />
          <span>{t('instSub')}</span>
        </p>
      </>
    )
    body = (
      <section className="insp-sec" aria-labelledby="wr-insp-chart">
        <h3 className="insp-title" id="wr-insp-chart">
          <span>{t('chart')}</span>
        </h3>
        <div className="insp-empty" data-testid="wr-chart-empty">
          <ChartCandlestick aria-hidden="true" strokeWidth={1.5} />
          <p className="insp-empty-title">{t('chartEmpty.title')}</p>
          <p>
            {before}
            <InstrumentId id={thread.key} />
            {after}
          </p>
        </div>
      </section>
    )
  } else if (thread?.kind === 'agent' && agentBySlug(thread.key)) {
    const agent = agentBySlug(thread.key)!
    const status = agentStatus(agent.slug)
    head = (
      <div className="insp-agent">
        <Medallion slug={agent.slug} size={44} status={status} />
        <div>
          <h2 className="text-title-3">{threadName}</h2>
          <p className="insp-kind">
            <Lock aria-hidden="true" strokeWidth={1.5} />
            <span>{t('agentSub')}</span>
          </p>
        </div>
      </div>
    )
    body = (
      <section className="insp-sec">
        <dl className="insp-facts">
          <dt>{t('agent.status')}</dt>
          <dd>{t(`status.${status}`)}</dd>
          <dt>{t('agent.handle')}</dt>
          <dd>
            <bdi dir="ltr">@{agent.handle}</bdi>
          </dd>
        </dl>
        <p className="insp-role">{t(`agents.${agent.slug}.role`)}</p>
      </section>
    )
  } else {
    head = (
      <>
        <h2 className="text-title-3 insp-name">{thread ? threadName : <Skeleton style={{ width: 90, height: 18 }} />}</h2>
        <p className="insp-kind">
          <UsersRound aria-hidden="true" strokeWidth={1.5} />
          <span>{t('mainSub')}</span>
        </p>
      </>
    )
  }

  return (
    <aside className="inspector" id="wr-inspector" aria-label={t('inspector')} data-testid="wr-inspector">
      <header className="insp-head">{head}</header>
      {body}
      <section className="insp-sec" aria-labelledby="wr-insp-facts">
        <h3 className="insp-title" id="wr-insp-facts">
          <span>{t('facts')}</span>
        </h3>
        <dl className="insp-facts" aria-busy={loading || undefined}>
          <dt>{t('started')}</dt>
          <dd>{loading || !thread ? <Skeleton style={{ width: 76, height: 11 }} /> : formatDate(thread.created_at, lang, true)}</dd>
          <dt>{t('lastMessage')}</dt>
          <dd>
            {loading ? (
              <Skeleton style={{ width: 40, height: 11 }} />
            ) : lastMessageAt ? (
              <span className="num">{dayOrTime(lastMessageAt, lang, now, t('yesterday'))}</span>
            ) : (
              none
            )}
          </dd>
        </dl>
      </section>
    </aside>
  )
}
