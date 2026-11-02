import { CloudOff, Flag, History } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { Skeleton } from '../../ui'
import type { WarroomMessage } from './api'
import { MOMENT_ICON } from './momentIcons'
import { isMoment, replayDayName } from './replay'
import { useAuthorName } from './shape'
import { plainText } from './tokens'
import { formatTime } from './time'

/** The Replay inspector: the day's moments, faint until the playhead reaches them, and the first/last message times. */
export function ReplayInspector({
  day,
  lang,
  status,
  messages,
  times,
  at,
  onPick,
}: {
  day: string
  lang: Lang
  status: 'loading' | 'error' | 'empty' | 'ok'
  messages: WarroomMessage[]
  times: number[]
  at: number
  onPick: (id: string) => void
}) {
  const { t } = useTranslation('warroom')
  const authorName = useAuthorName()
  const moments = useMemo(() => messages.flatMap((m, i) => (isMoment(m) ? [{ m, when: times[i]! }] : [])), [messages, times])
  // The moment the replay has just reached is highlighted; those still ahead are faint.
  const reached = moments.filter((x) => x.when <= at)
  const currentId = reached[reached.length - 1]?.m.id
  const listRef = useRef<HTMLOListElement>(null)
  useEffect(() => {
    if (!currentId) return
    const el = listRef.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(currentId)}"]`)
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [currentId])
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
      <ol className="rp-mos" data-testid="rp-moments" ref={listRef}>
        {moments.map(({ m, when }) => {
          const Icon = MOMENT_ICON[m.kind]
          return (
            <li key={m.id}>
              <button
                type="button"
                className={`rp-mo is-${m.kind} ${when <= at ? 'is-past' : 'is-future'}`}
                aria-current={m.id === currentId ? 'step' : undefined}
                onClick={() => onPick(m.id)}
                data-id={m.id}
                data-testid="rp-moment"
              >
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
    <aside className="inspector rp-insp" id="rp-inspector" aria-label={t('rp.insp')} data-testid="rp-inspector">
      <header className="insp-head">
        <h2 className="text-title-3">{replayDayName(day, lang)}</h2>
        <p className="insp-kind">
          <History aria-hidden="true" strokeWidth={1.5} />
          <span>{status === 'ok' || status === 'empty' ? t('rp.msgs', { count: messages.length, n: formatFixed(messages.length, 0, lang) }) : t('rp.title')}</span>
        </p>
      </header>
      <section className="insp-sec rp-mo-sec" aria-labelledby="rp-mo-h">
        <h3 className="insp-title" id="rp-mo-h">
          <span>
            {t('rp.moments')}
            {status === 'ok' && moments.length > 0 && <span className="num"> {formatFixed(moments.length, 0, lang)}</span>}
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
