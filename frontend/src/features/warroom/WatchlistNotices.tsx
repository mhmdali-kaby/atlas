import { Activity, Lock, TriangleAlert, Zap } from 'lucide-react'
import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { Fig } from './historianParts'
import { fillSlots, slot } from './slots'
import { InstrumentId } from './text'
import type { Pick, WatchlistCard as Card } from './watchlist'
import { conv, insts, joinList, Time } from './watchlistFormat'

/*
 * The watchlist card's notices (design v9 §A): partial evidence, a news-down asset class, the
 * intraday re-run trigger and the added/dropped lines; and the footer listing the owner's edits
 * once the window is over. Every notice is a UI string built from codes.
 */

export interface WatchlistNoticesProps {
  card: Card
  capValue: number
  lang: Lang
  onJump: (messageId: string) => void
}

export function WatchlistNotices({ card, capValue, lang, onJump }: WatchlistNoticesProps) {
  const { t } = useTranslation('warroom')
  const capFig = <Fig>{conv(capValue, lang)}</Fig>
  const agentName = (slug: string) => t(`agents.${slug}.name`, { defaultValue: slug })

  const notices: ReactNode[] = []
  if (card.partial) {
    const why = [
      ...card.partial.teammates.map((tm) => t(`wl.partialWhy.${tm.status}`, { agent: agentName(tm.agent) })),
      ...(card.partial.staleMinutes !== null ? [t('wl.partialWhy.stale', { min: t('wl.min', { count: card.partial.staleMinutes }) })] : []),
    ]
    const text = why.length
      ? fillSlots(t('wl.partial', { why: slot('why'), cap: slot('cap') }), { why: <>{joinList(why, lang)}</>, cap: capFig })
      : fillSlots(t('wl.partialBare', { cap: slot('cap') }), { cap: capFig })
    notices.push(
      <p key="partial" className="wl-note is-warn" data-testid="wl-partial">
        <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
        <span>{text}</span>
      </p>,
    )
  }
  for (const cls of card.degradedClasses) {
    const since = card.degradedSince[cls] ?? null
    const word = t(`wl.dcls.${cls}`, { defaultValue: cls })
    notices.push(
      <p key={`deg-${cls}`} className="wl-note is-warn" data-testid="wl-degraded">
        <TriangleAlert aria-hidden="true" strokeWidth={1.5} />
        <span>
          {since
            ? fillSlots(t('wl.degradedSince', { cls: word, time: slot('time'), cap: slot('cap') }), { time: <Time ts={since} lang={lang} />, cap: capFig })
            : fillSlots(t('wl.degraded', { cls: word, cap: slot('cap') }), { cap: capFig })}
        </span>
      </p>,
    )
  }
  if (card.trigger) {
    const tr = card.trigger
    const body =
      tr.kind === 'news_alert' ? (
        <>
          <span>{fillSlots(t('wl.trigger.news_alert', { imp: slot('imp'), time: slot('time') }), { imp: <Fig>{tr.importance}</Fig>, time: <Time ts={tr.at} lang={lang} /> })}</span>
          {tr.headline && (
            <span className="wl-head-q" dir="auto">
              {tr.headline}
            </span>
          )}
        </>
      ) : (
        <span>
          {fillSlots(t('wl.trigger.vol_spike', { sigma: slot('sigma'), inst: slot('inst'), time: slot('time') }), {
            sigma: <Fig>{`${formatFixed(tr.sigma, 1, lang)}σ`}</Fig>,
            inst: <InstrumentId id={tr.instId} />,
            time: <Time ts={tr.at} lang={lang} />,
          })}
        </span>
      )
    const Icon = tr.kind === 'news_alert' ? Zap : Activity
    const messageId = tr.kind === 'news_alert' ? tr.messageId : null
    notices.push(
      messageId ? (
        <a
          key="trigger"
          className="wl-trigger"
          href={`#${messageId}`}
          onClick={(e) => {
            e.preventDefault()
            onJump(messageId)
          }}
          data-testid="wl-trigger"
        >
          <Icon aria-hidden="true" strokeWidth={1.5} />
          <span className="wl-trig-b">{body}</span>
        </a>
      ) : (
        <p key="trigger" className="wl-trigger" data-testid="wl-trigger">
          <Icon aria-hidden="true" strokeWidth={1.5} />
          <span className="wl-trig-b">{body}</span>
        </p>
      ),
    )
  }
  if (card.changes) {
    const since = card.changes.since ?? card.createdAt
    const time = since ? <Time ts={since} lang={lang} /> : null
    if (card.changes.added.length)
      notices.push(
        <p key="added" className="wl-change" data-testid="wl-added">
          {fillSlots(t('wl.changes.added', { time: slot('time'), list: slot('list') }), { time, list: <>{insts(card.changes.added, lang)}</> })}
        </p>,
      )
    if (card.changes.dropped.length)
      notices.push(
        <p key="dropped" className="wl-change" data-testid="wl-dropped">
          {fillSlots(t('wl.changes.dropped', { time: slot('time'), list: slot('list') }), { time, list: <>{insts(card.changes.dropped, lang)}</> })}
        </p>,
      )
  }
  return <>{notices}</>
}

export interface WatchlistFootProps {
  card: Card
  rows: Pick[]
  live: Pick[]
  updated: boolean
  windowOver: boolean
  conflict: boolean
  endsAt: string | null
  lang: Lang
}

/** The closed-window footer: when it closed, and what the owner removed, pinned or carried. */
export function WatchlistFoot({ card, rows, live, updated, windowOver, conflict, endsAt, lang }: WatchlistFootProps) {
  const { t } = useTranslation('warroom')
  const cardPinned = new Set(card.picks.filter((p) => p.pinned).map((p) => p.instId))
  const removed = rows.filter((r) => r.removedAt).map((r) => r.instId)
  const pinnedNow = live.filter((r) => r.pinned && !cardPinned.has(r.instId)).map((r) => r.instId)
  const carried = updated ? live.filter((r) => r.pinned && cardPinned.has(r.instId)).map((r) => r.instId) : []
  const edits: ReactNode[] = []
  if (removed.length && pinnedNow.length)
    edits.push(fillSlots(t('wl.edits.both', { removed: slot('removed'), pinned: slot('pinned') }), { removed: <>{insts(removed, lang)}</>, pinned: <>{insts(pinnedNow, lang)}</> }))
  else if (removed.length) edits.push(fillSlots(t('wl.edits.removed', { list: slot('list') }), { list: <>{insts(removed, lang)}</> }))
  else if (pinnedNow.length) edits.push(fillSlots(t('wl.edits.pinned', { list: slot('list') }), { list: <>{insts(pinnedNow, lang)}</> }))
  if (carried.length) edits.push(fillSlots(t('wl.edits.carried', { list: slot('list') }), { list: <>{insts(carried, lang)}</> }))

  if (!windowOver && edits.length === 0) return null
  return (
    <p className="wl-foot" data-testid="wl-foot">
      <Lock aria-hidden="true" strokeWidth={1.5} />
      <span>
        {windowOver && !conflict && endsAt && fillSlots(t('wl.closed', { time: slot('time') }), { time: <Time ts={endsAt} lang={lang} /> })}
        {edits.map((e, i) => (
          <Fragment key={i}>
            {(i > 0 || (windowOver && !conflict)) && ' '}
            {e}
          </Fragment>
        ))}
      </span>
    </p>
  )
}
