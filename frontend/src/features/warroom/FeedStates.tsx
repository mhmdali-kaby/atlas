import { ChartCandlestick, CloudOff, MessagesSquare, RefreshCw, SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Button, Skeleton } from '../../ui'
import type { WarroomThread } from './api'
import { InstrumentId } from './text'
import { around } from './tokens'

/* The feed's whole-pane states (design v4 War Room §8): loading, error, unknown thread, empty. */

export function FeedSkeleton() {
  const { t } = useTranslation('warroom')
  const agent = (w1: number, w2: number) => (
    <div className="msg-skel">
      <Skeleton className="skel-round" />
      <span className="lines">
        <Skeleton style={{ width: 120, height: 12 }} />
        <Skeleton style={{ width: `${w1}%`, height: 12 }} />
        {w2 > 0 && <Skeleton style={{ width: `${w2}%`, height: 12 }} />}
      </span>
    </div>
  )
  const owner = (w: number) => (
    <div className="msg-skel is-owner">
      <Skeleton style={{ width: w, height: 38, borderRadius: 18 }} />
    </div>
  )
  return (
    <div className="wr-column" aria-busy="true" aria-label={t('loading')} role="status" data-testid="wr-feed-loading">
      {agent(88, 64)}
      {agent(76, 40)}
      {owner(260)}
      {agent(92, 70)}
      {agent(58, 0)}
      {owner(180)}
      {agent(84, 52)}
    </div>
  )
}

export function FeedError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('warroom')
  return (
    <div className="wr-column">
      <div className="state-block" role="alert" data-testid="wr-feed-error">
        <div className="state-icon is-error">
          <CloudOff aria-hidden="true" strokeWidth={1.5} />
        </div>
        <h2 className="text-title-3">{t('error.title')}</h2>
        <p className="state-body">
          {t('error.bodyBefore')}{' '}
          <code className="cmd" dir="ltr">
            ./atlas up
          </code>
          {t('error.bodyAfter')}
        </p>
        <Button variant="secondary" onClick={onRetry}>
          <RefreshCw aria-hidden="true" strokeWidth={1.5} className="size-4" />
          <span>{t('tryAgain')}</span>
        </Button>
      </div>
    </div>
  )
}

/** A War Room URL whose thread is not in this env's list: stale link, other env or typo. */
export function FeedNotFound() {
  const { t } = useTranslation('warroom')
  return (
    <div className="wr-column">
      <div className="state-block" role="alert" data-testid="wr-feed-not-found">
        <div className="state-icon">
          <SearchX aria-hidden="true" strokeWidth={1.5} />
        </div>
        <h2 className="text-title-3">{t('notFound.title')}</h2>
        <p className="state-body">{t('notFound.body')}</p>
        <Button variant="secondary" asChild>
          <Link to="/warroom">{t('notFound.action')}</Link>
        </Button>
      </div>
    </div>
  )
}

export function FeedEmpty({ thread }: { thread: WarroomThread }) {
  const { t } = useTranslation('warroom')
  const inst = thread.kind === 'instrument'
  const Icon = inst ? ChartCandlestick : MessagesSquare
  return (
    <div className="wr-column">
      <div className="state-block" data-testid="wr-feed-empty">
        <div className="state-icon">
          <Icon aria-hidden="true" strokeWidth={1.5} />
        </div>
        <h2 className="text-title-3">
          {inst ? (
            <>
              {around(t('empty.instTitle', { inst: '\u0000' }))[0]}
              <InstrumentId id={thread.key} />
              {around(t('empty.instTitle', { inst: '\u0000' }))[1]}
            </>
          ) : (
            t('empty.title')
          )}
        </h2>
        <p className="state-body">{t(inst ? 'empty.instBody' : 'empty.body')}</p>
      </div>
    </div>
  )
}
