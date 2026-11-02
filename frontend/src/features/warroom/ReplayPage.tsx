import { ArrowLeftRight, Calendar, ChevronLeft, ChevronRight, ChessKnight, CircleCheck, CloudOff, Flag, History, ListChecks, Lock, MessagesSquare, NotebookPen, RadioTower, RefreshCw, TriangleAlert, type LucideIcon } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import type { Lang } from '../../lib/format'
import { Button, Skeleton } from '../../ui'
import { useLang, useNow } from '../portfolio/hooks'
import type { WarroomMessage } from './api'
import { ReplayCalendar } from './ReplayCalendar'
import { ReplayFeed, type ReplayJump } from './ReplayFeed'
import { isMoment, isReplayable, lastReplayDay, replayDayName, replayPath, shiftDay, useReplay, type MomentKind } from './replay'
import { useAuthorName } from './shape'
import { plainText } from './tokens'
import { formatTime } from './time'
import './warroom.css'
import './replay.css'

/*
 * War Room Replay (ATL-206; brief §12 "Replay", design v18 Replay): /warroom/replay/<YYYY-MM-DD>
 * shows one finished Baghdad day read-only — the replay bar with the day and a way back to live,
 * the day's messages in order with the War Room's own cards, and the day's moments in the
 * inspector. No composer, no actions, no live messages. The timeline scrubber (ATL-207) goes
 * between the bar and the body.
 */

const MOMENT_ICON: Record<MomentKind, LucideIcon> = {
  watchlist: ListChecks,
  proposal: ChessKnight,
  order: ArrowLeftRight,
  fill: CircleCheck,
  system_alert: TriangleAlert,
  post_mortem: NotebookPen,
}

/** /warroom/replay without a valid finished day opens yesterday. */
export function ReplayRedirect() {
  const now = useNow(60_000)
  return <Navigate to={replayPath(lastReplayDay(now))} replace />
}

export function ReplayPage() {
  const { date } = useParams<{ date: string }>()
  const now = useNow(60_000)
  if (!isReplayable(date, now)) return <Navigate to={replayPath(lastReplayDay(now))} replace />
  return <ReplayDay key={date} day={date} now={now} />
}

function ReplayDay({ day, now }: { day: string; now: number }) {
  const { t } = useTranslation('warroom')
  const lang = useLang()
  const env = useTradingEnv()
  const query = useReplay(env, day)
  const messages = useMemo(() => query.data?.data ?? [], [query.data])
  const [jump, setJump] = useState<ReplayJump | null>(null)
  const dayName = replayDayName(day, lang)
  const status = query.isPending ? 'loading' : query.isError ? 'error' : messages.length === 0 ? 'empty' : 'ok'

  let main
  if (status === 'loading') {
    main = <ReplaySkeleton />
  } else if (status === 'error') {
    main = (
      <div className="wr-column">
        <div className="state-block" role="alert" data-testid="rp-error">
          <div className="state-icon is-error">
            <CloudOff aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h2 className="text-title-3">{t('rp.err.title')}</h2>
          <p className="state-body">
            {t('rp.err.bodyBefore')}{' '}
            <code className="cmd" dir="ltr">
              ./atlas up
            </code>
            {t('rp.err.bodyAfter')}
          </p>
          <Button variant="secondary" onClick={() => void query.refetch()} icon={<RefreshCw aria-hidden="true" strokeWidth={1.5} />}>
            {t('tryAgain')}
          </Button>
        </div>
      </div>
    )
  } else if (status === 'empty') {
    main = (
      <div className="wr-column">
        <div className="state-block" data-testid="rp-empty">
          <div className="state-icon">
            <MessagesSquare aria-hidden="true" strokeWidth={1.5} />
          </div>
          <h2 className="text-title-3">{t('rp.empty.title', { date: dayName })}</h2>
          <p className="state-body">{t('rp.empty.body')}</p>
          <DayPicker day={day} now={now} lang={lang} />
        </div>
      </div>
    )
  } else {
    main = (
      <>
        <ReplayFeed messages={messages} label={t('rp.feed', { date: dayName })} jump={jump} />
        <p className="rp-foot" data-testid="rp-foot">
          <Lock aria-hidden="true" strokeWidth={1.5} />
          <span>{t('rp.readonly')}</span>
          <Button asChild variant="plain" size="sm">
            <Link to="/warroom">{t('rp.live')}</Link>
          </Button>
        </p>
      </>
    )
  }

  return (
    <div className="rp" data-testid="rp-page" aria-busy={status === 'loading' || undefined}>
      <ReplayBar day={day} now={now} lang={lang} dayName={dayName} />
      {/* ATL-207: the day line (timeline scrubber) sits here, between the bar and the body. */}
      <div className="wr rp-body">
        <section className="rp-main" aria-label={t('rp.banner', { date: dayName })}>
          {main}
        </section>
        <ReplayInspector day={day} lang={lang} status={status} messages={messages} onPick={(id) => setJump((j) => ({ id, seq: (j?.seq ?? 0) + 1 }))} />
      </div>
    </div>
  )
}

function ReplayBar({ day, now, lang, dayName }: { day: string; now: number; lang: Lang; dayName: string }) {
  const { t } = useTranslation('warroom')
  const navigate = useNavigate()
  const next = shiftDay(day, 1)
  const canNext = isReplayable(next, now)
  return (
    <div className="rp-bar" role="region" aria-label={t('rp.banner', { date: dayName })} data-testid="rp-banner">
      <span className="rp-bar-ic" aria-hidden="true">
        <History strokeWidth={1.75} />
      </span>
      <div className="rp-bar-text">
        <p className="rp-bar-title">{t('rp.banner', { date: dayName })}</p>
        <p className="rp-bar-sub">{t('rp.bannerSub')}</p>
      </div>
      <div className="rp-bar-nav">
        <Button
          size="sm"
          className="rp-icon-btn"
          aria-label={t('rp.prevDay')}
          title={t('rp.prevDay')}
          onClick={() => navigate(replayPath(shiftDay(day, -1)))}
          data-testid="rp-prev-day"
        >
          <ChevronLeft aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </Button>
        <DayPicker day={day} now={now} lang={lang} trigger="bar" />
        <Button
          size="sm"
          className="rp-icon-btn"
          aria-label={t('rp.nextDay')}
          title={t('rp.nextDay')}
          disabled={!canNext}
          onClick={() => navigate(replayPath(next))}
          data-testid="rp-next-day"
        >
          <ChevronRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
        </Button>
      </div>
      <Button asChild variant="primary" size="sm" icon={<RadioTower aria-hidden="true" strokeWidth={1.5} />}>
        <Link to="/warroom" data-testid="rp-back-live">
          {t('rp.live')}
        </Link>
      </Button>
    </div>
  )
}

/** The "Choose a day" popover: the bar's date button, or the empty state's button. */
function DayPicker({ day, now, lang, trigger = 'empty' }: { day: string; now: number; lang: Lang; trigger?: 'bar' | 'empty' }) {
  const { t } = useTranslation('warroom')
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        {trigger === 'bar' ? (
          <Button size="sm" className="rp-date" icon={<Calendar aria-hidden="true" strokeWidth={1.5} />} aria-label={`${t('rp.pick')}: ${replayDayName(day, lang)}`} data-testid="rp-date">
            {replayDayName(day, lang, true)}
          </Button>
        ) : (
          <Button variant="secondary" icon={<Calendar aria-hidden="true" strokeWidth={1.5} />} data-testid="rp-pick">
            {t('rp.pick')}
          </Button>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="rp-cal-pop" sideOffset={6} align="center" aria-label={t('rp.pick')} collisionPadding={12}>
          <ReplayCalendar
            selected={day}
            now={now}
            lang={lang}
            onPick={(d) => {
              setOpen(false)
              navigate(replayPath(d))
            }}
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function ReplayInspector({
  day,
  lang,
  status,
  messages,
  onPick,
}: {
  day: string
  lang: Lang
  status: 'loading' | 'error' | 'empty' | 'ok'
  messages: WarroomMessage[]
  onPick: (id: string) => void
}) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  const moments = useMemo(() => messages.filter(isMoment), [messages])
  const first = messages[0]
  const last = messages[messages.length - 1]

  let body
  if (status === 'loading') {
    body = (
      <div className="rp-mos" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="rp-mo-skel">
            <Skeleton style={{ width: 34, height: 11 }} />
            <Skeleton style={{ width: 24, height: 24, borderRadius: '50%' }} />
            <span className="grid flex-1 gap-1.5">
              <Skeleton style={{ width: '70%', height: 12 }} />
              <Skeleton style={{ width: '90%', height: 10 }} />
            </span>
          </span>
        ))}
      </div>
    )
  } else if (status === 'error' || moments.length === 0) {
    body = (
      <div className="insp-empty">
        {status === 'error' ? <CloudOff aria-hidden="true" strokeWidth={1.5} /> : <Flag aria-hidden="true" strokeWidth={1.5} />}
        <p>{status === 'error' ? t('rp.momentsWait') : t('rp.momentsNone')}</p>
      </div>
    )
  } else {
    body = (
      <ol className="rp-mos" data-testid="rp-moments">
        {moments.map((m) => {
          const Icon = MOMENT_ICON[m.kind]
          return (
            <li key={m.id}>
              <button type="button" className={`rp-mo is-${m.kind}`} onClick={() => onPick(m.id)} data-testid="rp-moment">
                <span className="rp-mo-time num">{formatTime(m.created_at, lang)}</span>
                <span className="rp-mo-ic" aria-hidden="true">
                  <Icon strokeWidth={2} />
                </span>
                <span className="rp-mo-text">
                  <span className="rp-mo-l1">
                    <span className="rp-mo-what">{t(`rp.mk.${m.kind}`)}</span>
                    <span className="rp-mo-who">{authorName(m)}</span>
                  </span>
                  <span className="rp-mo-sub" dir="auto">
                    {plainText(m.content_md, (tag) => t(`tag.${tag}`))}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    )
  }

  return (
    <aside className="inspector rp-insp" aria-label={t('rp.insp')} data-testid="rp-inspector">
      <header className="insp-head">
        <h2 className="text-title-3">{replayDayName(day, lang)}</h2>
        <p className="insp-kind">
          <History aria-hidden="true" strokeWidth={1.5} />
          <span>{status === 'ok' || status === 'empty' ? t('rp.msgs', { count: messages.length }) : t('rp.title')}</span>
        </p>
      </header>
      <section className="insp-sec rp-mo-sec" aria-labelledby="rp-mo-h">
        <h3 className="insp-title" id="rp-mo-h">
          <span>
            {t('rp.moments')}
            {status === 'ok' && moments.length > 0 && <span className="num"> {moments.length}</span>}
          </span>
        </h3>
        {body}
      </section>
      {first && last && (
        <section className="insp-sec">
          <dl className="insp-facts">
            <dt>{t('rp.first')}</dt>
            <dd className="num">{formatTime(first.created_at, lang)}</dd>
            <dt>{t('rp.last')}</dt>
            <dd className="num">{formatTime(last.created_at, lang)}</dd>
          </dl>
        </section>
      )}
    </aside>
  )
}

function ReplaySkeleton() {
  const { t } = useTranslation('warroom')
  return (
    <div className="wr-column" role="status" aria-label={t('loading')} data-testid="rp-loading">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="msg-skel">
          <Skeleton className="skel-round" />
          <span className="lines">
            <Skeleton style={{ width: 180, height: 13 }} />
            <Skeleton style={{ width: '80%', height: 12 }} />
            <Skeleton style={{ width: '60%', height: 12 }} />
          </span>
        </div>
      ))}
    </div>
  )
}
